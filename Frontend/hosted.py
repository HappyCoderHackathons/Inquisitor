"""Hosted onboarding: account sign-in, agent pairing, collection in Atlas, and model training."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import secrets
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode
from uuid import uuid4

import requests
from atlas_persistence import (
    activate_model_version,
    delete_device_records,
    ensure_indexes,
    record_inference,
    record_model_version,
)
from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)
from flask_sock import Sock
from models import (
    DEFAULT_PRIVACY_PREFERENCES,
    DevicePairingState,
    EnrollmentSession,
    EnrollmentSessionStatus,
)
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.exceptions import RequestEntityTooLarge

GAMES = ("aim", "tetris", "typing", "arena")
GAME_SECONDS = 150
ROUND_MS = 60_000
MIN_TRAINING_WINDOWS = 20
MIN_VALIDATION_WINDOWS = 10
REQUIRED_WINDOWS = MIN_TRAINING_WINDOWS + MIN_VALIDATION_WINDOWS
# A paired device is online when one of its authenticated calls is this recent.
HEARTBEAT_ONLINE = timedelta(seconds=90)
# Spec 7.6: open an alert when this many of the last window are unusual, and close after sustained normal windows.
ALERT_WINDOW = 4
ALERT_UNUSUAL_COUNT = 3
ALERT_NORMAL_COUNT = 3
ALERT_EXPLANATION = (
    "Recent windows scored above this device's threshold. "
    "This is an anomaly signal, not proof of identity."
)
# (requests, window) per key; generous for real use, tight enough to stop guessing and floods.
LOGIN_LIMIT = (30, timedelta(minutes=1))
PAIR_LIMIT = (10, timedelta(minutes=10))
UPLOAD_LIMIT = (60, timedelta(minutes=1))
AGENT_WINDOW_MAX_BYTES = 64 * 1024
# Agent windows are native keyboard and mouse aggregates; enrollment trains the matching v4 model.
AGENT_SCHEMA_VERSION = 4
PAIRING_TTL = timedelta(minutes=10)
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ENV = (
    "BETTER_AUTH_URL", "MONGODB_URI", "MONGODB_DB", "ML_SERVICE_URL", "ML_TOKEN_FILE",
)

Document = dict[str, Any]
Trainer = Callable[[str, str, list[Document], list[str]], str | Document]
Scorer = Callable[[str, str, Document], Document]
Deleter = Callable[[str, str], None]
Activator = Callable[[str, str, str], None]
Comparer = Callable[[str, str, str, list[Document]], Document]
MODEL_LIMITATIONS = (
    "These figures describe this device's model only. A holdout rate counts alerts on sessions "
    "that were not used to fit the model. An impostor result counts alerts on someone else's sessions "
    "when that evaluation was stored. These figures are not proof of identity. A small sample makes the rate "
    "unstable, and a missing figure means that evaluation was not stored with the model."
)
ALGORITHM_LABELS = {
    "IsolationForest": (
        "Isolation Forest. It flags a window whose timing sits apart from this device's enrollment. "
        "The score is an anomaly signal, not an identity."
    ),
    "LogisticRegression": (
        "Logistic regression. It compares a window with this device's enrollment. "
        "The score is an anomaly signal, not an identity."
    ),
}
logger = logging.getLogger(__name__)


def audit_event(
    event: str,
    *,
    outcome: str,
    user_id: str | None = None,
    device_id: str | None = None,
    reason: str | None = None,
    sequence: int | None = None,
    schema_version: int | None = None,
    mode: str | None = None,
    device_count: int | None = None,
) -> None:
    metadata: dict[str, str | int] = {
        "event": event,
        "outcome": outcome,
    }
    if user_id is not None:
        metadata["user_id"] = user_id
    if device_id is not None:
        metadata["device_id"] = device_id
    if reason is not None:
        metadata["reason"] = reason
    if sequence is not None:
        metadata["sequence"] = sequence
    if schema_version is not None:
        metadata["schema_version"] = schema_version
    if mode is not None:
        metadata["mode"] = mode
    if device_count is not None:
        metadata["device_count"] = device_count
    logger.info(json.dumps(metadata, allow_nan=False, separators=(",", ":"), sort_keys=True))


def audit_agent_event(
    event: str,
    *,
    device_id: str,
    outcome: str,
    reason: str | None = None,
    sequence: int | None = None,
    schema_version: int | None = None,
    mode: str | None = None,
) -> None:
    audit_event(
        event,
        outcome=outcome,
        device_id=device_id,
        reason=reason,
        sequence=sequence,
        schema_version=schema_version,
        mode=mode,
    )


def audit_agent_window(
    device_id: str,
    outcome: str,
    reason: str | None = None,
    window: Document | None = None,
) -> None:
    audit_agent_event(
        "agent_window_ingestion",
        device_id=device_id,
        outcome=outcome,
        reason=reason,
        sequence=window["sequence"] if window is not None else None,
        schema_version=window["schema_version"] if window is not None else None,
        mode=window["mode"] if window is not None else None,
    )


def configured() -> bool:
    present = [name for name in ENV if os.environ.get(name)]
    if present and len(present) != len(ENV):
        missing = sorted(set(ENV) - set(present))
        raise RuntimeError(f"hosted onboarding is partly configured; missing {', '.join(missing)}")
    return bool(present)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


def alert_rule() -> tuple[int, int, int]:
    def read(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or not raw.isdigit() or int(raw) < 1:
            return default
        return int(raw)

    window = read("ALERT_WINDOW", ALERT_WINDOW)
    unusual_needed = read("ALERT_UNUSUAL_COUNT", ALERT_UNUSUAL_COUNT)
    normal_needed = read("ALERT_NORMAL_COUNT", ALERT_NORMAL_COUNT)
    if unusual_needed > window or normal_needed > window:
        return ALERT_WINDOW, ALERT_UNUSUAL_COUNT, ALERT_NORMAL_COUNT
    return window, unusual_needed, normal_needed


def persistence_decision(
    states: list[str], *, window: int, unusual_needed: int, normal_needed: int,
) -> str | None:
    if window < 1:
        raise ValueError("window must be positive")
    if not 1 <= unusual_needed <= window or not 1 <= normal_needed <= window:
        raise ValueError("persistence thresholds must be between 1 and window")
    recent = [state for state in states if state in {"NORMAL", "UNUSUAL"}][-window:]
    if len(recent) < window:
        return None
    if recent.count("UNUSUAL") >= unusual_needed:
        return "open"
    if recent.count("NORMAL") >= normal_needed:
        return "close"
    return None


def verification_delay(
    samples: list[tuple[datetime, str]], switched_at: datetime, *, window: int, unusual_needed: int,
) -> tuple[bool, int | None]:
    """Seconds from a demo switch until the persistence rule opens, using windows in time order."""
    mark = switched_at if switched_at.tzinfo is not None else switched_at.replace(tzinfo=UTC)
    recent: list[str] = []
    alert_before = False
    for created, state in samples:
        if state not in {"NORMAL", "UNUSUAL"}:
            continue
        seen = created if created.tzinfo is not None else created.replace(tzinfo=UTC)
        recent = (recent + [state])[-window:]
        if len(recent) < window or recent.count("UNUSUAL") < unusual_needed:
            continue
        if seen < mark:
            alert_before = True
            continue
        return alert_before, max(int((seen - mark).total_seconds()), 0)
    return alert_before, None


_listener_lock = threading.Lock()
_listeners: dict[tuple[str, str], list[queue.Queue[Document]]] = {}


def publish_monitoring(user_id: str, device_id: str, payload: Document) -> None:
    with _listener_lock:
        listeners = list(_listeners.get((user_id, device_id), ()))
    for listener in listeners:
        listener.put(payload)


def listen_monitoring(user_id: str, device_id: str):
    updates: queue.Queue[Document] = queue.Queue()
    key = (user_id, device_id)
    with _listener_lock:
        _listeners.setdefault(key, []).append(updates)
    try:
        while True:
            try:
                yield updates.get(timeout=15)
            except queue.Empty:
                yield None
    finally:
        with _listener_lock:
            current = _listeners.get(key, [])
            if updates in current:
                current.remove(updates)


def monitoring_event(payload: Document) -> str:
    return f"event: monitoring\ndata: {json.dumps(payload)}\n\n"


def whole_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def text_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return None
    return value


def timestamp_text(value: object) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return None


def empty_model_summary(device_id: str | None = None, device_name: str | None = None) -> Document:
    return {
        "available": False,
        "device_id": device_id,
        "device_name": device_name,
        "model_id": None,
        "trained_at": None,
        "training_windows": None,
        "validation_windows": None,
        "window_count": None,
        "session_count": None,
        "algorithm": None,
        "algorithm_label": None,
        "threshold": None,
        "threshold_label": None,
        "holdout_alerts": None,
        "holdout_windows": None,
        "impostor_alerts": None,
        "impostor_windows": None,
        "limitations": MODEL_LIMITATIONS,
    }


def paired_count(metadata: Document, alerts_key: str, windows_key: str) -> tuple[int | None, int | None]:
    alerts = whole_count(metadata.get(alerts_key))
    windows = whole_count(metadata.get(windows_key))
    if alerts is None or windows is None:
        return None, None
    return alerts, windows


def model_summary(database: Database, user_id: str, device_id: str | None = None) -> Document:
    if device_id is None:
        onboarding = database.onboarding.find_one({"user_id": user_id}, sort=[("created_at", -1)])
        if onboarding is None:
            return empty_model_summary()
    else:
        onboarding = database.onboarding.find_one({"user_id": user_id, "device_id": device_id})
        if onboarding is None:
            device = database.devices.find_one({"_id": device_id, "user_id": user_id})
            name = device.get("name") if device and isinstance(device.get("name"), str) else None
            return empty_model_summary(device_id, name)
    device = database.devices.find_one({"_id": onboarding["device_id"], "user_id": user_id})
    device_name = device.get("name") if device and isinstance(device.get("name"), str) else None
    version = database.model_versions.find_one(
        {"user_id": user_id, "device_id": onboarding["device_id"], "active": True},
        sort=[("created_at", -1)],
    )
    if version is None:
        return empty_model_summary(onboarding["device_id"], device_name)
    metadata = version.get("metadata") if isinstance(version.get("metadata"), dict) else {}
    training_windows = whole_count(metadata.get("training_window_count"))
    validation_windows = whole_count(metadata.get("validation_window_count"))
    if training_windows is None or validation_windows is None:
        window_count = database.windows.count_documents({"onboarding_id": onboarding["_id"], "usable": True})
        training_windows = None
        validation_windows = None
    else:
        window_count = training_windows + validation_windows
    training_sessions = text_list(metadata.get("training_sessions"))
    validation_sessions = text_list(metadata.get("validation_sessions")) or []
    if training_sessions is None:
        session_count = len(database.windows.distinct(
            "session_id", {"onboarding_id": onboarding["_id"], "usable": True},
        ))
    else:
        session_count = len(set(training_sessions) | set(validation_sessions))
    algorithm = metadata.get("algorithm")
    algorithm = algorithm if isinstance(algorithm, str) and algorithm else None
    threshold = finite_number(metadata.get("threshold"))
    rule = metadata.get("threshold_rule")
    threshold_label = None
    if threshold is not None:
        threshold_label = f"Alert when a window's score is above {threshold}."
        if isinstance(rule, str) and rule:
            threshold_label = f"{threshold_label} {rule}"
    holdout_alerts, holdout_windows = paired_count(metadata, "holdout_alert_count", "holdout_window_count")
    impostor_alerts, impostor_windows = paired_count(
        metadata, "impostor_evaluation_alert_count", "impostor_evaluation_window_count",
    )
    summary = empty_model_summary(onboarding["device_id"], device_name)
    summary.update({
        "available": True,
        "model_id": version["_id"],
        "trained_at": timestamp_text(metadata.get("trained_at")) or timestamp_text(version.get("created_at")),
        "training_windows": training_windows,
        "validation_windows": validation_windows,
        "window_count": window_count,
        "session_count": session_count,
        "algorithm": algorithm,
        "algorithm_label": ALGORITHM_LABELS.get(algorithm) if algorithm else None,
        "threshold": threshold,
        "threshold_label": threshold_label,
        "holdout_alerts": holdout_alerts,
        "holdout_windows": holdout_windows,
        "impostor_alerts": impostor_alerts,
        "impostor_windows": impostor_windows,
    })
    return summary


def ml_client(base_url: str, token: str) -> tuple[Trainer, Scorer, Deleter, Activator, Comparer, Deleter, Deleter]:
    def call(method: str, path: str, user_id: str, device_id: str, body: object | None = None) -> Document:
        request_ = urllib.request.Request(
            base_url.rstrip("/") + path, method=method,
            data=None if body is None else json.dumps(body, allow_nan=False).encode(),
            headers={"Authorization": f"Bearer {token}", "X-User-ID": user_id, "X-Device-ID": device_id,
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request_, timeout=120) as response:
                return cast(Document, json.load(response))
        except urllib.error.HTTPError as error:
            message = json.loads(error.read() or b"{}").get("error", "model service error")
            raise RuntimeError(f"model service returned {error.code}: {message}") from None

    def train(user_id: str, device_id: str, windows: list[Document], validation_sessions: list[str]) -> Document:
        # Agents upload schema v4 only, so enrollment trains the genuine-only v4 model.
        payload = {"windows": windows, "validation_session_ids": validation_sessions, "profile": "native-v4"}
        request_ = urllib.request.Request(
            base_url.rstrip("/") + "/models/train", method="POST",
            data=json.dumps(payload, allow_nan=False).encode(),
            headers={"Authorization": f"Bearer {token}", "X-User-ID": user_id, "X-Device-ID": device_id,
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request_, timeout=120) as response:
                metadata = cast(Document, json.load(response))
                status = response.status
        except urllib.error.HTTPError as error:
            message = json.loads(error.read() or b"{}").get("error", "model service error")
            raise RuntimeError(f"model service returned {error.code}: {message}") from None
        model_id = cast(str, metadata["model_id"])
        if status == 202:
            for _ in range(120):
                metadata = call("GET", f"/models/{model_id}", user_id, device_id)
                state = metadata.get("state")
                if state == "ready":
                    break
                if state == "failed":
                    raise RuntimeError(cast(str, metadata.get("error", "training failed")))
                threading.Event().wait(0.5)
            else:
                raise RuntimeError("training did not finish before the polling deadline")
        return metadata

    def score(user_id: str, device_id: str, window: Document) -> Document:
        return call("POST", "/models/active/score", user_id, device_id, {"window": window})

    def delete(user_id: str, device_id: str) -> None:
        call("DELETE", "/me/data", user_id, device_id)

    def activate(user_id: str, device_id: str, model_id: str) -> None:
        call("PUT", f"/models/{model_id}/active", user_id, device_id)

    def compare(user_id: str, device_id: str, model_id: str, windows: list[Document]) -> Document:
        return call("POST", f"/models/{model_id}/compare", user_id, device_id, {"windows": windows})

    def reset(user_id: str, device_id: str) -> None:
        call("POST", "/enrollment/reset", user_id, device_id)

    def deactivate(user_id: str, device_id: str) -> None:
        call("POST", "/models/active/deactivate", user_id, device_id)

    return train, score, delete, activate, compare, reset, deactivate


def collection_state(onboarding: Document) -> str:
    value = onboarding.get("collection", "active")
    return value if value in ("active", "paused", "finished") else "active"


def device_online(device: Document | None) -> bool:
    """A closed agent socket is offline at once; otherwise the last authenticated call must be recent.

    Devices that never opened a socket have no `ws_connected` field and keep the heartbeat rule alone.
    """
    if device is None or device.get("ws_connected") is False:
        return False
    seen = device.get("last_heartbeat_at")
    if not isinstance(seen, datetime):
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return now() - seen <= HEARTBEAT_ONLINE


def agent_presence(database: Database, onboarding: Document) -> str:
    device = database.devices.find_one(
        {"_id": onboarding["device_id"], "revoked_at": None, "pairing_state": "paired"}
    )
    if not device_online(device):
        return "offline"
    if collection_state(onboarding) == "paused" or cast(Document, device).get("agent_paused") is True:
        return "paused"
    return "active"


def agent_onboarding_status(database: Database, device_id: str) -> Document | None:
    """What the agent should collect: the same fields `GET /api/agent/onboarding` returns."""
    onboarding = database.onboarding.find_one({"device_id": device_id})
    if onboarding is None:
        return None
    active = None
    if onboarding["state"] == "collecting" and collection_state(onboarding) == "active":
        active = database.rounds.find_one({"onboarding_id": onboarding["_id"], "status": "active"})
    return {
        "state": onboarding["state"],
        "session_id": active["_id"] if active else None,
        "collection": collection_state(onboarding),
    }


AGENT_PROTOCOL = 1
# The agent sends a heartbeat every 30 seconds; a socket silent for longer than the online window is dropped.
AGENT_SOCKET_IDLE = HEARTBEAT_ONLINE
AGENT_MESSAGE_MAX_BYTES = 4 * 1024
POLICY_VIOLATION = 1008


class AgentLink:
    """One agent WebSocket's protocol, kept apart from the socket library so it can be tested directly.

    Agent to server: `{"v": 1, "type": "hello" | "heartbeat", "paused": bool}`; hello may add
    `agent_version`. Server to agent: `status` with the onboarding fields after hello and whenever
    they change, `monitoring` with only the monitoring state whenever it changes, or `revoked` once
    the device is revoked. Anything else closes the socket (1008).
    """

    def __init__(
        self, database: Database, device_id: str, monitoring: Callable[[Document], str | None] | None = None,
    ) -> None:
        self.database = database
        self.device_id = device_id
        self.monitoring = monitoring
        self.last_status: Document | None = None
        self.last_monitoring: str | None = None
        self.done = False
        self.close_code: int | None = None

    def receive(self, raw: str | bytes) -> list[Document]:
        message = None
        if isinstance(raw, str) and len(raw) <= AGENT_MESSAGE_MAX_BYTES:
            try:
                message = json.loads(raw)
            except ValueError:
                message = None
        if (
            not isinstance(message, dict)
            or message.get("v") != AGENT_PROTOCOL
            or message.get("type") not in ("hello", "heartbeat")
            or not isinstance(message.get("paused"), bool)
            or set(message) - {"v", "type", "paused", "agent_version"}
        ):
            self.done, self.close_code = True, POLICY_VIOLATION
            return []
        device = self.database.devices.find_one_and_update(
            {"_id": self.device_id, "pairing_state": "paired", "revoked_at": None},
            # The same fields every authenticated agent call refreshes, plus the socket's own state.
            {"$set": {"status": "online", "last_heartbeat_at": now(), "ws_connected": True,
                      "agent_paused": message["paused"]}},
            return_document=ReturnDocument.AFTER,
        )
        if device is None:
            self.done = True
            return [{"v": AGENT_PROTOCOL, "type": "revoked"}]
        status = agent_onboarding_status(self.database, self.device_id) or {
            "state": None, "session_id": None, "collection": None,
        }
        replies: list[Document] = []
        if message["type"] == "hello" or status != self.last_status:
            self.last_status = status
            replies.append({"v": AGENT_PROTOCOL, "type": "status", **status})
        state = self.monitoring(device) if self.monitoring is not None else None
        if state is not None and state != self.last_monitoring:
            self.last_monitoring = state
            replies.append({"v": AGENT_PROTOCOL, "type": "monitoring", "state": state})
        return replies

    def closed(self) -> None:
        self.database.devices.update_one({"_id": self.device_id}, {"$set": {"ws_connected": False}})


def progress(database: Database, onboarding: Document) -> Document:
    generation = onboarding.get("generation", 0)
    all_rounds = list(database.rounds.find({"onboarding_id": onboarding["_id"], "generation": generation}))
    rounds = sorted((item for item in all_rounds if item.get("credited_ms", 0) > 0), key=lambda r: r["started_at"])
    seconds = {game: sum(r["credited_ms"] for r in rounds if r["game"] == game) // 1000 for game in GAMES}
    # A retrain holds back its last keyboard-active round so old and new models are compared on unseen data.
    holdout = [item for item in rounds if item["valid_window_count"]][-1:] if generation else []
    rounds = [item for item in rounds if item not in holdout]
    # The latest keyboard-active round of each game calibrates the threshold; earlier rounds train.
    latest = {}
    for item in rounds:
        if item["valid_window_count"]:
            latest[item["game"]] = item["_id"]
    usable = {item["_id"]: item["valid_window_count"] for item in rounds}
    validation = sum(usable[session_id] for session_id in latest.values())
    training = sum(usable.values()) - validation
    return {
        "state": onboarding["state"], "error": onboarding.get("error"), "model_id": onboarding.get("model_id"),
        "device_id": onboarding["device_id"],
        "seconds": seconds, "required_seconds": GAME_SECONDS,
        "sessions": sorted(usable), "validation_sessions": sorted(latest.values()),
        "training_windows": training, "validation_windows": validation,
        "required_training_windows": MIN_TRAINING_WINDOWS, "required_validation_windows": MIN_VALIDATION_WINDOWS,
        "holdout_sessions": [item["_id"] for item in holdout],
        "holdout_windows": sum(item["valid_window_count"] for item in holdout),
        "retraining": bool(generation),
        "candidate_model_id": onboarding.get("candidate_model_id"),
        "previous_model_id": onboarding.get("previous_model_id"),
        "comparison": onboarding.get("comparison"),
        "ready_to_train": all(seconds[game] >= GAME_SECONDS for game in GAMES)
        and training >= MIN_TRAINING_WINDOWS and validation >= MIN_VALIDATION_WINDOWS
        and (not generation or bool(holdout)),
        "collection": collection_state(onboarding),
        "agent": agent_presence(database, onboarding),
        "usable_windows": sum(
            int(item.get("valid_window_count") or item.get("usable_windows") or 0) for item in all_rounds
        ),
        "duration_seconds": sum(int(item.get("credited_ms") or 0) for item in all_rounds) // 1000,
        "required_windows": REQUIRED_WINDOWS,
    }


def register(
    app: Flask,
    database: Database,
    trainer: Trainer,
    scorer: Scorer,
    window_error: Callable[[Document], str | None],
    deleter: Deleter,
    activator: Activator,
    comparer: Comparer,
    resetter: Deleter | None = None,
    deactivator: Deleter | None = None,
) -> None:
    public_origin = os.environ["BETTER_AUTH_URL"].rstrip("/")
    ensure_indexes(database)
    database.rate_limits.create_index("expires_at", expireAfterSeconds=0)
    database.pairing_codes.create_index("expires_at", expireAfterSeconds=0)
    database.pairing_codes.create_index("code_hash", unique=True)
    database.devices.create_index("token_hash", unique=True)
    database.devices.create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])
    database.devices.update_many(
        {"name": {"$exists": False}},
        {"$set": {"name": "unknown"}},
    )
    database.devices.update_many(
        {"pairing_state": {"$exists": False}, "revoked_at": None},
        {"$set": {"pairing_state": "paired"}},
    )
    database.devices.update_many(
        {"pairing_state": {"$exists": False}, "revoked_at": {"$exists": True, "$ne": None}},
        {"$set": {"pairing_state": "revoked"}},
    )
    database.devices.update_many(
        {"last_heartbeat_at": {"$exists": False}},
        {"$set": {"last_heartbeat_at": None}},
    )
    database.consent_records.create_index(
        [("user_id", ASCENDING), ("scope", ASCENDING), ("granted_at", ASCENDING)]
    )
    database.users.update_many(
        {"privacy_preferences": {"$exists": False}},
        {"$set": {"privacy_preferences": DEFAULT_PRIVACY_PREFERENCES}},
    )
    database.onboarding.create_index([("user_id", ASCENDING), ("device_id", ASCENDING)], unique=True)
    database.rounds.create_index([("device_id", ASCENDING), ("started_at", ASCENDING)])
    for session in database.rounds.find({
        "$or": [
            {"device_id": {"$exists": False}},
            {"status": {"$exists": False}},
            {"valid_window_count": {"$exists": False}},
        ],
    }):
        updates: Document = {}
        if "device_id" not in session:
            onboarding = database.onboarding.find_one({"_id": session["onboarding_id"]}, {"device_id": 1})
            if onboarding is not None:
                updates["device_id"] = onboarding["device_id"]
        if "status" not in session:
            if session.get("ended_at") is None:
                updates["status"] = "active"
            elif session.get("credited_ms", 0) > 0:
                updates["status"] = "completed"
            else:
                updates["status"] = "cancelled"
        if "valid_window_count" not in session:
            updates["valid_window_count"] = session.get("usable_windows", 0)
        if updates:
            database.rounds.update_one({"_id": session["_id"]}, {"$set": updates})
    database.rounds.update_many({"generation": {"$exists": False}}, {"$set": {"generation": 0}})
    database.rounds.create_index(
        [("device_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"status": "active", "device_id": {"$exists": True}},
        name="one_active_enrollment_per_device",
    )
    for collection in (database.windows, database.monitoring_windows):
        collection.create_index(
            [("device_id", ASCENDING), ("session_id", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
    blueprint = Blueprint("hosted", __name__)
    sock = Sock()

    @blueprint.get("/health")
    def health():
        try:
            database.command("ping")
        except PyMongoError:
            logger.warning(json.dumps({
                "component": "database",
                "event": "health_check",
                "outcome": "unavailable",
            }, separators=(",", ":"), sort_keys=True))
            return jsonify(state="degraded", database="unavailable"), 503
        return jsonify(state="ok", database="available")

    def start_training(onboarding: Document, status: Document) -> None:
        claimed = database.onboarding.find_one_and_update(
            {"_id": onboarding["_id"], "state": "collecting"}, {"$set": {"state": "training", "error": None}}
        )
        if claimed is None:
            return
        def session_windows(sessions: list[str]) -> list[Document]:
            return [item["window"] for item in database.windows.find(
                {"onboarding_id": onboarding["_id"], "usable": True, "session_id": {"$in": sessions}},
                {"_id": 0, "window": 1},
            )]

        windows = session_windows(status["sessions"])
        holdout = session_windows(status["holdout_sessions"])
        user_id, device_id = onboarding["user_id"], onboarding["device_id"]

        def run() -> None:
            update: Document
            try:
                result = trainer(user_id, device_id, windows, status["validation_sessions"])
                metadata = result if isinstance(result, dict) else {"model_id": result, "state": "ready"}
                model_id = metadata.get("model_id")
                if not isinstance(model_id, str) or not model_id:
                    raise ValueError("training did not return a model id")
                metadata = {**metadata, "model_id": model_id, "state": metadata.get("state") or "ready"}
                stamp = now()
                if status["retraining"]:
                    # A retrained model waits for the user's decision; the current model keeps scoring.
                    comparison = comparer(user_id, device_id, model_id, holdout)
                    update = {"state": "review", "candidate_model_id": model_id, "comparison": comparison,
                              "completed_at": stamp}
                else:
                    activator(user_id, device_id, model_id)
                    update = {"state": "ready", "model_id": model_id, "completed_at": stamp}
                record_model_version(
                    database, user_id=user_id, device_id=device_id, metadata=metadata,
                    active=not status["retraining"], created_at=stamp,
                )
            except (RuntimeError, OSError, KeyError, ValueError) as error:
                update = {"state": "ready" if status["retraining"] else "failed", "error": str(error)}
            database.onboarding.update_one({"_id": onboarding["_id"]}, {"$set": update})

        # shortcut: in-process thread loses the job on restart; move to a job queue if training gets slow.
        threading.Thread(target=run, daemon=True).start()

    def throttle(key: str, limit: tuple[int, timedelta]) -> None:
        count, window = limit
        bucket = int(now().timestamp() // window.total_seconds())
        record = database.rate_limits.find_one_and_update(
            {"_id": f"{key}:{bucket}"}, {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": now() + 2 * window}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        if record["count"] > count:
            abort(429)

    def client_ip() -> str:
        # shortcut: uses the socket peer address; add ProxyFix when deployed behind a trusted proxy.
        return request.remote_addr or "unknown"

    def signed_in_user() -> str | None:
        if "auth_user" not in g:
            g.auth_user = None
            if request.headers.get("Cookie"):
                response = auth_request("GET", "get-session")
                if response.status_code != 200:
                    abort(503, description="Sign-in service is unavailable")
                try:
                    result = response.json()
                    user = result.get("user") if isinstance(result, dict) else None
                    if user is not None:
                        if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"] \
                                or not isinstance(user.get("email"), str):
                            raise ValueError("invalid auth user")
                        g.auth_user = "better-auth:" + user["id"]
                        timestamp = now()
                        database.users.update_one({"_id": g.auth_user}, {
                            "$set": {"email": user["email"]},
                            "$setOnInsert": {
                                "created_at": timestamp,
                                "privacy_preferences": DEFAULT_PRIVACY_PREFERENCES,
                            },
                        }, upsert=True)
                except ValueError:
                    abort(503, description="Invalid sign-in service response")
        return g.auth_user

    def current_user() -> str:
        user_id = signed_in_user()
        if user_id is None:
            abort(401)
        return user_id

    def owned_enrollment(enrollment_id: str, user_id: str) -> Document:
        enrollment = database.rounds.find_one({"_id": enrollment_id})
        if enrollment is None:
            abort(404)
        if database.devices.find_one({"_id": enrollment["device_id"], "user_id": user_id}) is None:
            abort(404)
        if database.onboarding.find_one(
            {
                "_id": enrollment["onboarding_id"],
                "user_id": user_id,
                "device_id": enrollment["device_id"],
            }
        ) is None:
            abort(404)
        return enrollment

    def enrollment_response(enrollment: Document) -> Document:
        usable_windows = database.windows.count_documents({
            "device_id": enrollment["device_id"],
            "session_id": enrollment["_id"],
            "usable": True,
        })
        started = cast(datetime, enrollment["started_at"])
        ended = cast(datetime | None, enrollment.get("ended_at"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if ended is not None and ended.tzinfo is None:
            ended = ended.replace(tzinfo=UTC)
        return {
            "id": enrollment["_id"],
            "device_id": enrollment["device_id"],
            "game": enrollment["game"],
            "status": enrollment["status"],
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "ended_at": ended.isoformat().replace("+00:00", "Z") if ended is not None else None,
            "progress": {"usable_window_count": usable_windows},
        }

    account_pages = {"/dashboard", "/demo", "/devices"}

    def safe_next() -> str:
        value = request.args.get("next", "")
        return value if value in account_pages else ""

    def auth_query(*, signup: bool, nxt: str) -> str:
        params: dict[str, str] = {}
        if signup:
            params["mode"] = "signup"
        if nxt:
            params["next"] = nxt
        return urlencode(params)

    @app.before_request
    def redirect_anonymous_account_pages():
        if request.method != "GET" or request.path not in account_pages or signed_in_user() is not None:
            return None
        return redirect(url_for("hosted.login", next=request.path))

    def json_body() -> Document:
        # JSON-only bodies plus SameSite=Lax cookies block cross-site form posts.
        if request.headers.get("Origin") not in (None, public_origin) \
                or request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)
        value = request.get_json(silent=True) if request.is_json else None
        if not isinstance(value, dict):
            abort(415)
        return value

    def user_onboarding(user_id: str) -> Document:
        onboarding = database.onboarding.find_one({"user_id": user_id}, sort=[("created_at", -1)])
        if onboarding is None:
            abort(404)
        return onboarding

    def device_auth() -> Document:
        header = request.headers.get("Authorization", "")
        device = database.devices.find_one_and_update(
            {
                "token_hash": digest(header.removeprefix("Bearer ")) if header.startswith("Bearer ") else "",
                "pairing_state": "paired",
                "revoked_at": None,
            },
            {"$set": {"status": "online", "last_heartbeat_at": now()}},
            return_document=ReturnDocument.AFTER,
        )
        if device is None:
            abort(401)
        return device

    @blueprint.get("/login")
    def login():
        nxt = safe_next()
        return redirect(url_for("hosted.onboarding_page", **({"next": nxt} if nxt else {})))

    @blueprint.get("/register")
    def register_page():
        params = {"mode": "signup"}
        nxt = safe_next()
        if nxt:
            params["next"] = nxt
        return redirect(url_for("hosted.onboarding_page", **params))

    @blueprint.get("/dashboard")
    def account_dashboard():
        if signed_in_user() is None:
            return redirect(url_for("hosted.login", next="/dashboard"))
        return render_template("dashboard.html")

    @blueprint.get("/demo")
    def demo_page():
        if signed_in_user() is None:
            return redirect(url_for("hosted.login", next="/demo"))
        return render_template("demo.html")

    @blueprint.get("/devices")
    def devices_page():
        if signed_in_user() is None:
            return redirect(url_for("hosted.login", next="/devices"))
        return render_template("devices.html")

    def auth_request(method: str, path: str) -> requests.Response:
        headers = {name: request.headers[name] for name in ("Cookie", "Content-Type", "Origin", "Sec-Fetch-Site")
                   if name in request.headers}
        headers["X-Real-IP"] = client_ip()
        try:
            return requests.request(method, f"http://127.0.0.1:3001/api/auth/{path}", headers=headers,
                                    data=request.get_data() if method == "POST" else None,
                                    timeout=10, allow_redirects=False)
        except requests.RequestException:
            abort(503, description="Sign-in service is unavailable")

    @blueprint.route("/api/auth/<path:path>", methods=["GET", "POST"])
    def auth_proxy(path: str):
        allowed = {"sign-in/email", "sign-up/email", "sign-out"} if request.method == "POST" else {"get-session"}
        if path not in allowed:
            abort(404)
        if request.method == "POST":
            json_body()
            throttle(f"login:{client_ip()}", LOGIN_LIMIT)
        upstream = auth_request(request.method, path)
        response = app.response_class(upstream.content, status=upstream.status_code,
                                      content_type=upstream.headers.get("Content-Type", "application/json"))
        for cookie in upstream.raw.headers.getlist("Set-Cookie"):
            response.headers.add("Set-Cookie", cookie)
        response.headers["Cache-Control"] = "no-store"
        return response

    @blueprint.get("/onboarding")
    def onboarding_page():
        nxt = safe_next()
        signup = request.args.get("mode") == "signup"
        return render_template(
            "onboarding.html",
            signed_in=signed_in_user() is not None,
            mode="signup" if signup else "login",
            next_path=nxt,
            toggle_query=auth_query(signup=not signup, nxt=nxt),
        )

    @blueprint.get("/api/me")
    def me():
        user = database.users.find_one({"_id": current_user()}, {"email": 1})
        if user is None:
            abort(401)
        return {"user_id": user["_id"], "email": user.get("email")}

    @blueprint.post("/api/pairing-codes")
    def create_pairing_code():
        user_id = current_user()
        json_body()
        code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        expires = now() + PAIRING_TTL
        database.pairing_codes.insert_one({"code_hash": digest(code), "user_id": user_id, "expires_at": expires,
                                           "used_at": None})
        audit_event("pairing_code_issued", outcome="accepted", user_id=user_id)
        return jsonify(code=code, expires_at=expires.isoformat()), 201

    @blueprint.get("/api/onboarding")
    def onboarding_status():
        return jsonify(progress(database, user_onboarding(current_user())))

    @blueprint.post("/api/enrollments")
    def create_enrollment():
        user_id = current_user()
        body = json_body()
        device_id = body.get("device_id")
        game = body.get("game")
        if not isinstance(device_id, str) or not device_id or game not in GAMES:
            abort(422)
        device = database.devices.find_one({
            "_id": device_id,
            "user_id": user_id,
            "pairing_state": "paired",
            "revoked_at": None,
        })
        if device is None:
            abort(404)
        onboarding = database.onboarding.find_one({"user_id": user_id, "device_id": device_id})
        if onboarding is None:
            abort(404)
        if onboarding["state"] != "collecting" or collection_state(onboarding) != "active":
            abort(409)
        if database.rounds.find_one({"device_id": device_id, "status": "active"}) is not None:
            abort(409)

        started = now()
        session_id = uuid4().hex
        session_status: EnrollmentSessionStatus = "active"
        session: EnrollmentSession = {
            "_id": session_id,
            "onboarding_id": onboarding["_id"],
            "device_id": device_id,
            "game": game,
            "started_at": started,
            "ended_at": None,
            "status": session_status,
            "credited_ms": 0,
            "valid_window_count": 0,
            "generation": onboarding.get("generation", 0),
        }
        try:
            database.rounds.insert_one(session)
        except DuplicateKeyError:
            abort(409)
        return jsonify(enrollment_response(session)), 201

    @blueprint.get("/api/enrollments/<enrollment_id>")
    def get_enrollment(enrollment_id: str):
        enrollment = owned_enrollment(enrollment_id, current_user())
        return jsonify(enrollment_response(enrollment))

    @blueprint.post("/api/enrollments/<enrollment_id>/complete")
    def complete_enrollment(enrollment_id: str):
        user_id = current_user()
        json_body()
        enrollment = owned_enrollment(enrollment_id, user_id)
        if enrollment["status"] != "active":
            abort(409)
        ended = now()
        started = cast(datetime, enrollment["started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        credited = min(max(int((ended - started).total_seconds() * 1000), 0), ROUND_MS)
        completed = database.rounds.find_one_and_update(
            {"_id": enrollment_id, "device_id": enrollment["device_id"], "status": "active"},
            {"$set": {"ended_at": ended, "credited_ms": credited, "status": "completed"}},
            return_document=ReturnDocument.AFTER,
        )
        if completed is None:
            abort(409)
        return jsonify(enrollment_response(completed))

    @blueprint.post("/api/onboarding/rounds")
    def start_round():
        onboarding = user_onboarding(current_user())
        game = json_body().get("game")
        if game not in GAMES or onboarding["state"] != "collecting" or collection_state(onboarding) != "active":
            abort(409 if game in GAMES else 422)
        started = now()
        database.rounds.update_many(
            {"onboarding_id": onboarding["_id"], "status": "active"},
            {"$set": {"ended_at": started, "credited_ms": 0, "status": "cancelled"}},
        )
        session_id = uuid4().hex
        session_status: EnrollmentSessionStatus = "active"
        session: EnrollmentSession = {
            "_id": session_id,
            "onboarding_id": onboarding["_id"],
            "device_id": onboarding["device_id"],
            "game": game,
            "started_at": started,
            "ended_at": None,
            "status": session_status,
            "credited_ms": 0,
            "valid_window_count": 0,
            "generation": onboarding.get("generation", 0),
        }
        try:
            database.rounds.insert_one(session)
        except DuplicateKeyError:
            abort(409)
        return jsonify(session_id=session_id), 201

    @blueprint.post("/api/onboarding/rounds/current/<outcome>")
    def end_round(outcome: str):
        onboarding = user_onboarding(current_user())
        json_body()
        if outcome not in ("finish", "cancel"):
            abort(404)
        active = database.rounds.find_one({"onboarding_id": onboarding["_id"], "status": "active"})
        if active is None:
            abort(409)
        ended = now()
        started = cast(datetime, active["started_at"]).replace(tzinfo=UTC)
        credited = min(int((ended - started).total_seconds() * 1000), ROUND_MS) if outcome == "finish" else 0
        status: EnrollmentSessionStatus = "completed" if outcome == "finish" else "cancelled"
        database.rounds.update_one(
            {"_id": active["_id"]},
            {"$set": {"ended_at": ended, "credited_ms": credited, "status": status}},
        )
        return jsonify(progress(database, onboarding))

    @blueprint.post("/api/onboarding/train")
    def train():
        onboarding = user_onboarding(current_user())
        json_body()
        status = progress(database, onboarding)
        if onboarding["state"] != "collecting" or not status["ready_to_train"]:
            return jsonify(status), 409
        start_training(onboarding, status)
        return jsonify(progress(database, user_onboarding(onboarding["user_id"]))), 202

    def typed_confirmation(phrase: str) -> bool:
        body = json_body()
        return set(body) == {"confirmation"} and body.get("confirmation") == phrase

    def latest_onboarding(user_id: str) -> Document | None:
        return database.onboarding.find_one({"user_id": user_id}, sort=[("created_at", -1)])

    def service_call(action: Deleter | None, user_id: str, device_id: str) -> str | None:
        if action is None:
            return "model service is unavailable; nothing was changed"
        try:
            action(user_id, device_id)
        except (RuntimeError, OSError):
            return "model service is unavailable; nothing was changed"
        return None

    @blueprint.get("/api/model")
    def model_details():
        user_id = current_user()
        device_id = request.args.get("device_id")
        if device_id not in (None, ""):
            if database.devices.find_one({"_id": device_id, "user_id": user_id}) is None:
                abort(404)
        else:
            device_id = None
        return jsonify(model_summary(database, user_id, device_id))

    @blueprint.post("/api/models/reset")
    def reset_model_enrollment():
        user_id = current_user()
        if not typed_confirmation("RESET"):
            return jsonify(error="Type RESET to confirm this action"), 400
        onboarding = latest_onboarding(user_id)
        if onboarding is None:
            abort(404)
        if onboarding["state"] == "training":
            return jsonify(error="training is already running"), 409
        failure = service_call(resetter, user_id, onboarding["device_id"])
        if failure:
            return jsonify(error=failure), 502
        database.windows.delete_many({"onboarding_id": onboarding["_id"]})
        database.rounds.update_many(
            {"onboarding_id": onboarding["_id"]},
            {"$set": {
                "usable_windows": 0,
                "valid_window_count": 0,
                "credited_ms": 0,
                "status": "cancelled",
                "ended_at": now(),
                "generation": 0,
            }},
        )
        database.model_versions.delete_many({
            "user_id": user_id, "device_id": onboarding["device_id"], "active": True,
        })
        database.onboarding.update_one(
            {"_id": onboarding["_id"]},
            {"$set": {
                "state": "collecting", "collection": "active", "model_id": None, "error": None,
                "candidate_model_id": None, "comparison": None, "previous_model_id": None, "generation": 0,
            }},
        )
        return jsonify(ok=True)

    @blueprint.post("/api/models/deactivate")
    def deactivate_hosted_model():
        user_id = current_user()
        if not typed_confirmation("DEACTIVATE"):
            return jsonify(error="Type DEACTIVATE to confirm this action"), 400
        onboarding = latest_onboarding(user_id)
        if onboarding is None:
            abort(404)
        if onboarding["state"] == "training":
            return jsonify(error="training is already running"), 409
        failure = service_call(deactivator, user_id, onboarding["device_id"])
        if failure:
            return jsonify(error=failure), 502
        database.model_versions.update_many(
            {"user_id": user_id, "device_id": onboarding["device_id"], "active": True},
            {"$set": {"active": False}},
        )
        return jsonify(ok=True, active=False)

    def close_open_round(onboarding: Document, *, credit: bool) -> None:
        active = database.rounds.find_one({"onboarding_id": onboarding["_id"], "status": "active"})
        if active is None:
            return
        ended = now()
        credited = 0
        status: EnrollmentSessionStatus = "cancelled"
        if credit:
            started = cast(datetime, active["started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            credited = min(int((ended - started).total_seconds() * 1000), ROUND_MS)
            status = "completed"
        database.rounds.update_one(
            {"_id": active["_id"]},
            {"$set": {"ended_at": ended, "credited_ms": credited, "status": status}},
        )

    def mark_collection(onboarding: Document, value: str) -> Document:
        database.onboarding.update_one({"_id": onboarding["_id"]}, {"$set": {"collection": value}})
        onboarding["collection"] = value
        return progress(database, onboarding)

    @blueprint.post("/api/onboarding/pause")
    def pause_enrollment():
        onboarding = user_onboarding(current_user())
        json_body()
        if onboarding["state"] != "collecting" or collection_state(onboarding) == "finished":
            return jsonify(progress(database, onboarding)), 409
        if collection_state(onboarding) == "paused":
            return jsonify(progress(database, onboarding))
        return jsonify(mark_collection(onboarding, "paused"))

    @blueprint.post("/api/onboarding/resume")
    def resume_enrollment():
        onboarding = user_onboarding(current_user())
        json_body()
        if onboarding["state"] != "collecting" or collection_state(onboarding) != "paused":
            return jsonify(progress(database, onboarding)), 409
        return jsonify(mark_collection(onboarding, "active"))

    @blueprint.post("/api/onboarding/discard")
    def discard_enrollment():
        onboarding = user_onboarding(current_user())
        json_body()
        if onboarding["state"] != "collecting":
            return jsonify(progress(database, onboarding)), 409
        close_open_round(onboarding, credit=False)
        # Only the current generation is discarded; a retrain keeps the windows behind the active model.
        current = {"onboarding_id": onboarding["_id"], "generation": onboarding.get("generation", 0)}
        sessions = [item["_id"] for item in database.rounds.find(current, {"_id": 1})]
        database.windows.delete_many({"onboarding_id": onboarding["_id"], "session_id": {"$in": sessions}})
        database.rounds.update_many(current, {"$set": {"usable_windows": 0, "valid_window_count": 0, "credited_ms": 0}})
        return jsonify(mark_collection(onboarding, "active"))

    @blueprint.post("/api/onboarding/finish")
    def finish_enrollment():
        onboarding = user_onboarding(current_user())
        json_body()
        if onboarding["state"] != "collecting":
            return jsonify(progress(database, onboarding)), 409
        if collection_state(onboarding) == "finished":
            return jsonify(progress(database, onboarding))
        close_open_round(onboarding, credit=True)
        return jsonify(mark_collection(onboarding, "finished"))

    @blueprint.post("/api/onboarding/retrain")
    def approve_retrain():
        onboarding = user_onboarding(current_user())
        if json_body().get("approve") is not True:
            return jsonify(error="retraining requires explicit approval"), 422
        claimed = database.onboarding.find_one_and_update(
            {"_id": onboarding["_id"], "state": "ready", "model_id": {"$exists": True, "$ne": None}},
            {"$set": {"state": "collecting", "collection": "active", "error": None, "candidate_model_id": None,
                      "comparison": None, "retrain_approved_at": now()},
             "$inc": {"generation": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if claimed is None:
            return jsonify(progress(database, onboarding)), 409
        return jsonify(progress(database, claimed))

    def switch_model(onboarding: Document, model_id: str, update: Document):
        try:
            activator(onboarding["user_id"], onboarding["device_id"], model_id)
        except (RuntimeError, OSError) as error:
            return jsonify(error=f"model service could not activate the model: {error}"), 502
        activate_model_version(database, user_id=onboarding["user_id"], device_id=onboarding["device_id"],
                               model_id=model_id, activated_at=now())
        database.onboarding.update_one({"_id": onboarding["_id"]}, {"$set": update})
        return jsonify(progress(database, user_onboarding(onboarding["user_id"])))

    @blueprint.post("/api/onboarding/candidate/<decision>")
    def decide_candidate(decision: str):
        onboarding = user_onboarding(current_user())
        json_body()
        if decision not in ("activate", "keep"):
            abort(404)
        if onboarding["state"] != "review" or not onboarding.get("candidate_model_id"):
            return jsonify(progress(database, onboarding)), 409
        if decision == "keep":
            database.onboarding.update_one({"_id": onboarding["_id"]},
                                           {"$set": {"state": "ready", "candidate_model_id": None}})
            return jsonify(progress(database, user_onboarding(onboarding["user_id"])))
        return switch_model(onboarding, onboarding["candidate_model_id"], {
            "state": "ready", "model_id": onboarding["candidate_model_id"],
            "previous_model_id": onboarding["model_id"], "candidate_model_id": None,
        })

    @blueprint.post("/api/onboarding/rollback")
    def roll_back():
        onboarding = user_onboarding(current_user())
        json_body()
        if onboarding["state"] != "ready" or not onboarding.get("previous_model_id"):
            return jsonify(progress(database, onboarding)), 409
        return switch_model(onboarding, onboarding["previous_model_id"], {
            "model_id": onboarding["previous_model_id"], "previous_model_id": onboarding["model_id"],
        })

    @blueprint.post("/api/devices/<device_id>/revoke")
    def revoke_device(device_id: str):
        user_id = current_user()
        json_body()
        revoked = database.devices.update_one(
            {"_id": device_id, "user_id": user_id, "revoked_at": None},
            {"$set": {"revoked_at": now(), "pairing_state": "revoked"}},
        )
        if revoked.matched_count == 0:
            audit_event(
                "device_revocation",
                outcome="rejected",
                user_id=user_id,
                device_id=device_id,
                reason="not_found_or_already_revoked",
            )
            abort(404)
        audit_event("device_revocation", outcome="accepted", user_id=user_id, device_id=device_id)
        return jsonify(ok=True)

    @blueprint.delete("/api/me/data")
    def delete_my_data():
        user_id = current_user()
        json_body()
        device_ids = [device["_id"] for device in database.devices.find({"user_id": user_id}, {"_id": 1})]
        if device_ids:
            try:
                deleter(user_id, device_ids[0])
            except (RuntimeError, OSError):
                audit_event(
                    "behavioral_data_deletion",
                    outcome="failed",
                    user_id=user_id,
                    reason="model_service_unavailable",
                    device_count=len(device_ids),
                )
                return jsonify(error="model service is unavailable; nothing was deleted"), 502
        onboarding_ids = [item["_id"] for item in database.onboarding.find({"user_id": user_id}, {"_id": 1})]
        database.rounds.delete_many({"onboarding_id": {"$in": onboarding_ids}})
        database.onboarding.delete_many({"user_id": user_id})
        for device_id in device_ids:
            delete_device_records(database, user_id, device_id)
        database.devices.update_many(
            {"user_id": user_id, "revoked_at": None}, {"$set": {"revoked_at": now(), "pairing_state": "revoked"}}
        )
        audit_event("behavioral_data_deletion", outcome="accepted", user_id=user_id, device_count=len(device_ids))
        return jsonify(ok=True, devices=len(device_ids))

    @blueprint.post("/api/agent/pair")
    def pair():
        throttle(f"pair:{client_ip()}", PAIR_LIMIT)
        body = json_body()
        code = body.get("code")
        name = body.get("name")
        if (
            not isinstance(code, str)
            or not isinstance(name, str)
            or not name
            or len(name) > 64
            or not name.isascii()
            or not name.isprintable()
            or name.strip() != name
        ):
            abort(422)
        pairing = database.pairing_codes.find_one_and_update(
            {"code_hash": digest(code.strip().upper()), "used_at": None, "expires_at": {"$gt": now()}},
            {"$set": {"used_at": now()}},
        )
        if pairing is None:
            audit_event("agent_pairing", outcome="rejected", reason="invalid_expired_or_used_code")
            return jsonify(error="pairing code is invalid, expired, or already used"), 404
        device_id, token = uuid4().hex, secrets.token_urlsafe(32)
        pairing_state: DevicePairingState = "paired"
        database.devices.insert_one({
            "_id": device_id,
            "name": name,
            "user_id": pairing["user_id"],
            "pairing_state": pairing_state,
            "token_hash": digest(token),
            "created_at": now(),
            "last_heartbeat_at": None,
            "revoked_at": None,
        })
        database.onboarding.insert_one({"_id": uuid4().hex, "user_id": pairing["user_id"], "device_id": device_id,
                                        "state": "collecting", "created_at": now()})
        audit_event(
            "agent_paired",
            outcome="accepted",
            user_id=pairing["user_id"],
            device_id=device_id,
        )
        return jsonify(device_id=device_id, token=token), 201

    @blueprint.post("/agent/heartbeat")
    @blueprint.post("/api/agent/heartbeat")
    def agent_heartbeat():
        device = device_auth()
        audit_agent_event("agent_heartbeat", device_id=device["_id"], outcome="accepted")
        return jsonify(
            status=device["status"],
            last_seen=timestamp_text(device.get("last_heartbeat_at")),
        )

    @blueprint.get("/api/agent/onboarding")
    def agent_onboarding():
        device = device_auth()
        status = agent_onboarding_status(database, device["_id"])
        if status is None:
            abort(404)
        return jsonify(**status)

    @blueprint.before_request
    def authorize_agent_socket():
        # flask-sock accepts the upgrade before the view runs, so reject here while a plain HTTP error
        # can still be returned. A browser always sends Origin; refusing foreign ones stops cross-site
        # WebSocket hijacking. The agent sends none.
        if request.path != "/api/agent/ws":
            return None
        if request.headers.get("Origin") not in (None, public_origin):
            abort(403)
        g.agent_device = device_auth()
        return None

    @sock.route("/api/agent/ws", bp=blueprint)
    def agent_socket(ws):
        link = AgentLink(
            database, g.agent_device["_id"],
            lambda device: monitoring_state(device["user_id"], device, agent_state(device)[0]),
        )
        try:
            while not link.done:
                raw = ws.receive(timeout=AGENT_SOCKET_IDLE.total_seconds())
                if raw is None:
                    break
                for reply in link.receive(raw):
                    ws.send(json.dumps(reply))
                publish_device_monitoring(g.agent_device["_id"])
            ws.close(reason=link.close_code or 1000)
        finally:
            link.closed()
            publish_device_monitoring(g.agent_device["_id"])

    @blueprint.post("/api/agent/windows")
    def upload_window():
        request.max_content_length = AGENT_WINDOW_MAX_BYTES
        device = device_auth()
        throttle(f"upload:{device['_id']}", UPLOAD_LIMIT)
        try:
            window = json_body()
        except RequestEntityTooLarge:
            audit_agent_window(device["_id"], "rejected", "request_too_large")
            return jsonify(error="Request body exceeds the allowed size"), 413
        error = window_error(window)
        if error:
            audit_agent_window(device["_id"], "rejected", "invalid_payload")
            return jsonify(error=error), 422
        if window.get("schema_version") != AGENT_SCHEMA_VERSION:
            audit_agent_window(device["_id"], "rejected", "schema_version")
            return jsonify(error=f"agents must send schema v{AGENT_SCHEMA_VERSION} windows"), 422
        if window["device_id"] != device["_id"]:
            audit_agent_window(device["_id"], "rejected", "device_mismatch")
            return jsonify(error="window belongs to another device"), 422
        if window["mode"] == "monitoring":
            return score_window(device, window)
        onboarding = database.onboarding.find_one({"device_id": device["_id"], "state": "collecting"})
        if onboarding is None or collection_state(onboarding) != "active":
            return jsonify(error="enrollment is not collecting"), 409
        active = database.rounds.find_one(
            {"_id": window["session_id"], "onboarding_id": onboarding["_id"], "status": "active"}
        )
        if not active:
            audit_agent_window(device["_id"], "rejected", "inactive_session")
            return jsonify(error="no active game round for this session"), 409
        usable = cast(dict[str, int], window["features"])["key_count"] >= 30
        try:
            database.windows.insert_one({"onboarding_id": onboarding["_id"], "user_id": device["user_id"],
                                         "device_id": device["_id"], "session_id": window["session_id"],
                                         "sequence": window["sequence"], "game": active["game"],
                                         "usable": usable, "window": window, "created_at": now()})
        except DuplicateKeyError:
            audit_agent_window(device["_id"], "rejected", "duplicate_sequence", window=window)
            return jsonify(error="This device/session/sequence window already exists"), 409
        if usable:
            database.rounds.update_one({"_id": active["_id"]}, {"$inc": {"valid_window_count": 1}})
        audit_agent_window(device["_id"], "accepted", window=window)
        return jsonify(ok=True, usable=usable), 201

    def apply_alert_rule(user_id: str, device_id: str, stamp: datetime) -> None:
        window, unusual_needed, normal_needed = alert_rule()
        states = [
            item.get("classification")
            for item in database.inference_results.find(
                {"user_id": user_id, "device_id": device_id}, {"classification": 1},
            ).sort("created_at", ASCENDING)
        ]
        decision = persistence_decision(
            [state for state in states if isinstance(state, str)],
            window=window, unusual_needed=unusual_needed, normal_needed=normal_needed,
        )
        open_alert = database.alerts.find_one({"user_id": user_id, "device_id": device_id, "status": "open"})
        if decision == "open" and open_alert is None:
            database.alerts.insert_one({
                "_id": uuid4().hex,
                "user_id": user_id,
                "device_id": device_id,
                "status": "open",
                "severity": "medium",
                "explanation": ALERT_EXPLANATION,
                "started_at": stamp,
                "ended_at": None,
                "acknowledged_at": None,
                "acknowledgement": None,
            })
        elif decision == "close" and open_alert is not None:
            database.alerts.update_one(
                {"_id": open_alert["_id"]}, {"$set": {"status": "closed", "ended_at": stamp}},
            )

    def aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def owned_device(user_id: str, device_id: str | None) -> Document | None:
        if device_id is None:
            return database.devices.find_one(
                {"user_id": user_id, "revoked_at": None},
                sort=[("last_heartbeat_at", -1), ("created_at", -1)],
            )
        device = database.devices.find_one({"_id": device_id, "user_id": user_id})
        if device is None:
            abort(404)
        return device

    def agent_state(device: Document | None) -> tuple[str, bool]:
        if device is None or not device_online(device):
            return "offline", True
        onboarding = database.onboarding.find_one({"device_id": device["_id"], "user_id": device["user_id"]})
        if (onboarding is not None and collection_state(onboarding) == "paused") or device.get("agent_paused") is True:
            return "paused", False
        return "active", False

    def monitoring_state(user_id: str, device: Document | None, presence: str) -> str | None:
        if device is None:
            return None
        if presence == "offline":
            return "offline"
        if presence == "paused":
            return "paused"
        latest = database.inference_results.find_one(
            {"user_id": user_id, "device_id": device["_id"]}, sort=[("created_at", -1)],
        )
        if latest is None:
            return "not_enough_activity"
        if database.alerts.find_one({"user_id": user_id, "device_id": device["_id"], "status": "open"}):
            return "needs_verification"
        classification = latest.get("classification")
        if classification == "UNUSUAL":
            return "unusual"
        if classification == "NORMAL":
            return "normal"
        return "not_enough_activity"

    def alert_payload(alert: Document) -> Document:
        started = alert.get("started_at") or alert.get("created_at")
        ended = alert.get("ended_at")
        duration = None
        if isinstance(started, datetime) and isinstance(ended, datetime):
            duration = max(int((aware(ended) - aware(started)).total_seconds()), 0)
        return {
            "id": alert["_id"],
            "device_id": alert["device_id"],
            "status": alert.get("status"),
            "severity": alert.get("severity"),
            "explanation": alert.get("explanation") or ALERT_EXPLANATION,
            "started_at": timestamp_text(started),
            "ended_at": timestamp_text(ended),
            "duration_seconds": duration,
            "ongoing": alert.get("status") == "open",
            "acknowledgement": alert.get("acknowledgement"),
            "acknowledged_at": timestamp_text(alert.get("acknowledged_at")),
        }

    def status_document(user_id: str, device: Document | None) -> Document:
        presence, stale = agent_state(device)
        latest = None
        if device is not None:
            latest = database.inference_results.find_one(
                {"user_id": user_id, "device_id": device["_id"]}, sort=[("created_at", -1)],
            )
        score = finite_number(latest.get("score")) if latest else None
        threshold = finite_number(latest.get("threshold")) if latest else None
        return {
            "state": monitoring_state(user_id, device, presence),
            "window_state": latest.get("classification") if latest else None,
            "score": score,
            "threshold": threshold,
            "scored_at": timestamp_text(latest.get("created_at")) if latest else None,
            "agent": presence if device is not None else "offline",
            "last_seen": timestamp_text(device.get("last_heartbeat_at")) if device else None,
            "stale": stale if device is not None else True,
            "device_id": device["_id"] if device else None,
            "device_name": device.get("name") if device else None,
        }

    def stream_payload(user_id: str, device: Document) -> Document:
        payload = status_document(user_id, device)
        payload["alerts"] = [
            alert_payload(item) for item in database.alerts.find(
                {"user_id": user_id, "device_id": device["_id"], "status": "open"},
            ).sort("started_at", -1)
        ]
        return payload

    def publish_device_monitoring(device_id: str) -> None:
        device = database.devices.find_one({"_id": device_id})
        if device is not None:
            publish_monitoring(device["user_id"], device_id, stream_payload(device["user_id"], device))

    @blueprint.get("/monitoring/status")
    @blueprint.get("/api/monitoring/status")
    def monitoring_status():
        user_id = current_user()
        device = owned_device(user_id, request.args.get("device_id"))
        return jsonify(status_document(user_id, device))

    @blueprint.get("/api/monitoring/stream")
    def monitoring_stream():
        user_id = current_user()
        device = owned_device(user_id, request.args.get("device_id"))
        if device is None:
            abort(404)

        def generate():
            yield monitoring_event(stream_payload(user_id, device))
            for payload in listen_monitoring(user_id, device["_id"]):
                yield ": keep-alive\n\n" if payload is None else monitoring_event(payload)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @blueprint.get("/monitoring/history")
    @blueprint.get("/api/monitoring/history")
    def monitoring_history():
        user_id = current_user()
        raw_limit = request.args.get("limit", "60")
        if not raw_limit.isdigit() or not 1 <= int(raw_limit) <= 200:
            return jsonify(error="limit must be an integer from 1 to 200"), 422
        since = request.args.get("since")
        until = request.args.get("until")
        created: Document = {}
        for name, value in (("since", since), ("until", until)):
            if value in (None, ""):
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return jsonify(error=f"{name} must be an ISO timestamp"), 422
            created["$gte" if name == "since" else "$lte"] = aware(parsed)
        device = owned_device(user_id, request.args.get("device_id"))
        if device is None:
            return jsonify(axis="Anomaly score", points=[])
        query: Document = {"user_id": user_id, "device_id": device["_id"]}
        if created:
            query["created_at"] = created
        rows = list(database.inference_results.find(query).sort("created_at", -1).limit(int(raw_limit)))
        rows.reverse()
        return jsonify(axis="Anomaly score", points=[{
            "t": timestamp_text(item.get("created_at")),
            "score": finite_number(item.get("score")),
            "threshold": finite_number(item.get("threshold")),
            "baseline_score": finite_number(item.get("result", {}).get("baseline_score")
                                            if isinstance(item.get("result"), dict) else None),
            "state": item.get("classification"),
        } for item in rows])

    @blueprint.get("/alerts")
    @blueprint.get("/api/alerts")
    def list_alerts():
        user_id = current_user()
        query: Document = {"user_id": user_id}
        status = request.args.get("status")
        if status not in (None, ""):
            if status not in {"open", "closed"}:
                return jsonify(error="status must be open or closed"), 422
            query["status"] = status
        device_id = request.args.get("device_id")
        if device_id not in (None, ""):
            owned_device(user_id, device_id)
            query["device_id"] = device_id
        alerts = database.alerts.find(query).sort("started_at", -1)
        return jsonify(alerts=[alert_payload(item) for item in alerts])

    @blueprint.post("/api/alerts/<alert_id>/acknowledge")
    def acknowledge_alert(alert_id: str):
        user_id = current_user()
        body = json_body()
        label = body.get("label")
        if set(body) != {"label"} or label not in {"expected", "unexpected"}:
            return jsonify(error="label must be expected or unexpected"), 400
        alert = database.alerts.find_one({"_id": alert_id, "user_id": user_id})
        if alert is None:
            abort(404)
        stamp = now()
        database.alerts.update_one(
            {"_id": alert_id}, {"$set": {"acknowledgement": label, "acknowledged_at": stamp}},
        )
        alert["acknowledgement"] = label
        alert["acknowledged_at"] = stamp
        device = database.devices.find_one({"_id": alert["device_id"], "user_id": user_id})
        if device is not None:
            publish_monitoring(user_id, device["_id"], stream_payload(user_id, device))
        return jsonify(alert_payload(alert))

    def device_payload(device: Document) -> Document:
        seen = device.get("last_heartbeat_at")
        revoked = device.get("revoked_at") is not None or device.get("pairing_state") == "revoked"
        fresh = device_online(device)
        return {
            "id": device["_id"],
            "name": device.get("name"),
            "last_seen": timestamp_text(seen),
            "pairing_state": device.get("pairing_state"),
            "revoked": revoked,
            "stale": revoked or not fresh,
        }

    def switch_payload(switch: Document) -> Document:
        window, unusual_needed, _normal_needed = alert_rule()
        samples = [
            (item.get("created_at"), item.get("classification"))
            for item in database.inference_results.find(
                {"user_id": switch["user_id"], "device_id": switch["device_id"]},
            ).sort("created_at", ASCENDING)
        ]
        typed = [
            (created, state) for created, state in samples
            if isinstance(created, datetime) and isinstance(state, str)
        ]
        alert_before, latency = verification_delay(
            typed, switch["switched_at"], window=window, unusual_needed=unusual_needed,
        )
        return {
            "id": switch["_id"],
            "device_id": switch["device_id"],
            "switched_at": timestamp_text(switch.get("switched_at")),
            "alert_before_switch": alert_before,
            "latency_seconds": latency,
        }

    @blueprint.get("/api/devices")
    def list_devices():
        user_id = current_user()
        devices = database.devices.find({"user_id": user_id}).sort("created_at", ASCENDING)
        return jsonify(devices=[device_payload(item) for item in devices])

    @blueprint.post("/api/demo/switches")
    def mark_switch():
        user_id = current_user()
        body = json_body()
        if body != {}:
            return jsonify(error="a switch marker does not accept extra fields"), 400
        device = owned_device(user_id, request.args.get("device_id"))
        if device is None:
            abort(404)
        stamp = now()
        switch = {
            "_id": uuid4().hex,
            "user_id": user_id,
            "device_id": device["_id"],
            "switched_at": stamp,
        }
        database.demo_switches.insert_one(switch)
        return jsonify(switch_payload(switch)), 201

    @blueprint.get("/api/demo/switches")
    def list_switches():
        user_id = current_user()
        device_id = request.args.get("device_id")
        query: Document = {"user_id": user_id}
        if device_id not in (None, ""):
            owned_device(user_id, device_id)
            query["device_id"] = device_id
        switches = database.demo_switches.find(query).sort("switched_at", ASCENDING)
        return jsonify(switches=[switch_payload(item) for item in switches])

    def score_window(device: Document, window: Document):
        # Monitoring continues on the active model while a retrain collects, trains, or awaits review.
        if database.onboarding.find_one({"device_id": device["_id"], "model_id": {"$exists": True, "$ne": None}}) is None:
            audit_agent_window(device["_id"], "rejected", "model_not_ready", window=window)
            return jsonify(error="monitoring starts after onboarding trains a model"), 409
        try:
            record = database.monitoring_windows.insert_one({
                "user_id": device["user_id"], "device_id": device["_id"], "session_id": window["session_id"],
                "sequence": window["sequence"], "window": window, "created_at": now(),
            })
        except DuplicateKeyError:
            audit_agent_window(device["_id"], "rejected", "duplicate_sequence", window=window)
            return jsonify(error="This device/session/sequence window already exists"), 409
        try:
            result = scorer(device["user_id"], device["_id"], window)
        except (RuntimeError, OSError):
            # Remove the unscored window so the agent can retry the same sequence.
            database.monitoring_windows.delete_one({"_id": record.inserted_id})
            audit_agent_window(device["_id"], "failed", "scoring_unavailable", window=window)
            return jsonify(error="scoring is unavailable; retry later"), 502
        stamp = now()
        record_inference(
            database, user_id=device["user_id"], device_id=device["_id"], window=window, result=result, created_at=stamp,
        )
        apply_alert_rule(device["user_id"], device["_id"], stamp)
        publish_monitoring(device["user_id"], device["_id"], stream_payload(device["user_id"], device))
        database.monitoring_windows.update_one({"_id": record.inserted_id}, {"$set": {"result": result}})
        audit_agent_window(device["_id"], "accepted", window=window)
        return jsonify(result), 201

    app.register_blueprint(blueprint)
