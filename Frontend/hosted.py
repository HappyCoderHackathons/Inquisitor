"""Hosted onboarding: account sign-in, agent pairing, collection in Atlas, and model training."""
from __future__ import annotations

import hashlib
import json
import os
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
    delete_device_records,
    ensure_indexes,
    record_inference,
    record_model_version,
)
from flask import (
    Blueprint,
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from models import (
    DEFAULT_PRIVACY_PREFERENCES,
    DevicePairingState,
    EnrollmentSession,
    EnrollmentSessionStatus,
)
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

GAMES = ("aim", "tetris", "typing", "arena")
GAME_SECONDS = 150
ROUND_MS = 60_000
MIN_TRAINING_WINDOWS = 20
MIN_VALIDATION_WINDOWS = 10
# (requests, window) per key; generous for real use, tight enough to stop guessing and floods.
LOGIN_LIMIT = (30, timedelta(minutes=1))
PAIR_LIMIT = (10, timedelta(minutes=10))
UPLOAD_LIMIT = (60, timedelta(minutes=1))
PAIRING_TTL = timedelta(minutes=10)
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ENV = (
    "BETTER_AUTH_URL", "MONGODB_URI", "MONGODB_DB", "ML_SERVICE_URL", "ML_TOKEN_FILE",
)

Document = dict[str, Any]
Trainer = Callable[[str, str, list[Document], list[str]], str]
Scorer = Callable[[str, str, Document], Document]
Deleter = Callable[[str, str], None]


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


def ml_client(base_url: str, token: str) -> tuple[Trainer, Scorer, Deleter]:
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

    def train(user_id: str, device_id: str, windows: list[Document], validation_sessions: list[str]) -> str:
        payload = {"windows": windows, "validation_session_ids": validation_sessions}
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
        call("PUT", f"/models/{model_id}/active", user_id, device_id)
        return model_id

    def score(user_id: str, device_id: str, window: Document) -> Document:
        return call("POST", "/models/active/score", user_id, device_id, {"window": window})

    def delete(user_id: str, device_id: str) -> None:
        call("DELETE", "/me/data", user_id, device_id)

    return train, score, delete


def progress(database: Database, onboarding: Document) -> Document:
    rounds = list(database.rounds.find({"onboarding_id": onboarding["_id"], "credited_ms": {"$gt": 0}}))
    seconds = {game: sum(r["credited_ms"] for r in rounds if r["game"] == game) // 1000 for game in GAMES}
    # The latest keyboard-active round of each game calibrates the threshold; earlier rounds train.
    latest = {}
    for item in sorted(rounds, key=lambda r: r["started_at"]):
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
        "ready_to_train": all(seconds[game] >= GAME_SECONDS for game in GAMES)
        and training >= MIN_TRAINING_WINDOWS and validation >= MIN_VALIDATION_WINDOWS,
    }


def register(
    app: Flask,
    database: Database,
    trainer: Trainer,
    scorer: Scorer,
    window_error: Callable[[Document], str | None],
    deleter: Deleter,
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
    for collection in (database.windows, database.monitoring_windows):
        collection.create_index(
            [("device_id", ASCENDING), ("session_id", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
    blueprint = Blueprint("hosted", __name__)

    def start_training(onboarding: Document, status: Document) -> None:
        claimed = database.onboarding.find_one_and_update(
            {"_id": onboarding["_id"], "state": "collecting"}, {"$set": {"state": "training", "error": None}}
        )
        if claimed is None:
            return
        windows = [item["window"] for item in database.windows.find(
            {"onboarding_id": onboarding["_id"], "usable": True, "session_id": {"$in": status["sessions"]}},
            {"_id": 0, "window": 1},
        )]

        def run() -> None:
            try:
                model_id = trainer(onboarding["user_id"], onboarding["device_id"], windows,
                                   status["validation_sessions"])
                update = {"state": "ready", "model_id": model_id, "completed_at": now()}
                record_model_version(
                    database, user_id=onboarding["user_id"], device_id=onboarding["device_id"],
                    metadata={"model_id": model_id, "state": "ready"}, active=True, created_at=update["completed_at"],
                )
            except (RuntimeError, OSError, KeyError, ValueError) as error:
                update = {"state": "failed", "error": str(error)}
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

    account_pages = {"/dashboard"}

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
            {"$set": {"last_heartbeat_at": now()}},
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
        return jsonify(code=code, expires_at=expires.isoformat()), 201

    @blueprint.get("/api/onboarding")
    def onboarding_status():
        return jsonify(progress(database, user_onboarding(current_user())))

    @blueprint.post("/api/onboarding/rounds")
    def start_round():
        onboarding = user_onboarding(current_user())
        game = json_body().get("game")
        if game not in GAMES or onboarding["state"] != "collecting":
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
        }
        database.rounds.insert_one(session)
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

    @blueprint.post("/api/devices/<device_id>/revoke")
    def revoke_device(device_id: str):
        user_id = current_user()
        json_body()
        revoked = database.devices.update_one(
            {"_id": device_id, "user_id": user_id, "revoked_at": None},
            {"$set": {"revoked_at": now(), "pairing_state": "revoked"}},
        )
        if revoked.matched_count == 0:
            abort(404)
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
                return jsonify(error="model service is unavailable; nothing was deleted"), 502
        onboarding_ids = [item["_id"] for item in database.onboarding.find({"user_id": user_id}, {"_id": 1})]
        database.rounds.delete_many({"onboarding_id": {"$in": onboarding_ids}})
        database.onboarding.delete_many({"user_id": user_id})
        for device_id in device_ids:
            delete_device_records(database, user_id, device_id)
        database.devices.update_many(
            {"user_id": user_id, "revoked_at": None}, {"$set": {"revoked_at": now(), "pairing_state": "revoked"}}
        )
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
        return jsonify(device_id=device_id, token=token), 201

    @blueprint.get("/api/agent/onboarding")
    def agent_onboarding():
        device = device_auth()
        onboarding = database.onboarding.find_one({"device_id": device["_id"]})
        if onboarding is None:
            abort(404)
        active = database.rounds.find_one({"onboarding_id": onboarding["_id"], "status": "active"})
        return jsonify(state=onboarding["state"], session_id=active["_id"] if active else None)

    @blueprint.post("/api/agent/windows")
    def upload_window():
        device = device_auth()
        throttle(f"upload:{device['_id']}", UPLOAD_LIMIT)
        window = json_body()
        error = window_error(window)
        if error:
            return jsonify(error=error), 422
        if window["device_id"] != device["_id"]:
            return jsonify(error="window belongs to another device"), 422
        if window["mode"] == "monitoring":
            return score_window(device, window)
        onboarding = database.onboarding.find_one({"device_id": device["_id"], "state": "collecting"})
        active = onboarding and database.rounds.find_one(
            {"_id": window["session_id"], "onboarding_id": onboarding["_id"], "status": "active"}
        )
        if not active:
            return jsonify(error="no active game round for this session"), 409
        usable = cast(dict[str, int], window["features"])["key_count"] >= 30
        try:
            database.windows.insert_one({"onboarding_id": onboarding["_id"], "user_id": device["user_id"],
                                         "device_id": device["_id"], "session_id": window["session_id"],
                                         "sequence": window["sequence"], "game": active["game"],
                                         "usable": usable, "window": window, "created_at": now()})
        except DuplicateKeyError:
            return jsonify(error="This device/session/sequence window already exists"), 409
        if usable:
            database.rounds.update_one({"_id": active["_id"]}, {"$inc": {"valid_window_count": 1}})
        return jsonify(ok=True, usable=usable), 201

    def score_window(device: Document, window: Document):
        if database.onboarding.find_one({"device_id": device["_id"], "state": "ready"}) is None:
            return jsonify(error="monitoring starts after onboarding trains a model"), 409
        try:
            record = database.monitoring_windows.insert_one({
                "user_id": device["user_id"], "device_id": device["_id"], "session_id": window["session_id"],
                "sequence": window["sequence"], "window": window, "created_at": now(),
            })
        except DuplicateKeyError:
            return jsonify(error="This device/session/sequence window already exists"), 409
        try:
            result = scorer(device["user_id"], device["_id"], window)
        except (RuntimeError, OSError):
            # Remove the unscored window so the agent can retry the same sequence.
            database.monitoring_windows.delete_one({"_id": record.inserted_id})
            return jsonify(error="scoring is unavailable; retry later"), 502
        stamp = now()
        record_inference(
            database, user_id=device["user_id"], device_id=device["_id"], window=window, result=result, created_at=stamp,
        )
        database.monitoring_windows.update_one({"_id": record.inserted_id}, {"$set": {"result": result}})
        return jsonify(result), 201

    app.register_blueprint(blueprint)
