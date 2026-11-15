"""Hosted onboarding: SSOJet sign-in, agent pairing, game-round collection in Atlas, and model training."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import uuid4

from flask import Blueprint, Flask, abort, jsonify, redirect, render_template, request, session, url_for
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

GAMES = ("aim", "tetris", "typing", "arena")
GAME_SECONDS = 150
ROUND_MS = 60_000
MIN_TRAINING_WINDOWS = 20
MIN_VALIDATION_WINDOWS = 10
SESSION_LIFETIME = timedelta(hours=12)
# (requests, window) per key; generous for real use, tight enough to stop guessing and floods.
LOGIN_LIMIT = (30, timedelta(minutes=1))
PAIR_LIMIT = (10, timedelta(minutes=10))
UPLOAD_LIMIT = (60, timedelta(minutes=1))
PAIRING_TTL = timedelta(minutes=10)
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ENV = (
    "SSOJET_AUTHORITY", "SSOJET_CLIENT_ID", "SSOJET_CLIENT_SECRET", "SSOJET_REDIRECT_URI",
    "SESSION_SECRET", "MONGODB_URI", "MONGODB_DB", "ML_SERVICE_URL", "ML_TOKEN_FILE",
)

Document = dict[str, Any]
Trainer = Callable[[str, str, list[Document], list[str]], str]


def configured() -> bool:
    present = [name for name in ENV if os.environ.get(name)]
    if present and len(present) != len(ENV):
        missing = sorted(set(ENV) - set(present))
        raise RuntimeError(f"hosted onboarding is partly configured; missing {', '.join(missing)}")
    return bool(present)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(timezone.utc)


def ml_trainer(base_url: str, token: str) -> Trainer:
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
        metadata = call("POST", "/models/train", user_id, device_id,
                        {"windows": windows, "validation_session_ids": validation_sessions})
        model_id = cast(str, metadata["model_id"])
        call("PUT", f"/models/{model_id}/active", user_id, device_id)
        return model_id

    return train


def progress(database: Database, onboarding: Document) -> Document:
    rounds = list(database.rounds.find({"onboarding_id": onboarding["_id"], "credited_ms": {"$gt": 0}}))
    seconds = {game: sum(r["credited_ms"] for r in rounds if r["game"] == game) // 1000 for game in GAMES}
    # The latest keyboard-active round of each game calibrates the threshold; earlier rounds train.
    latest = {}
    for item in sorted(rounds, key=lambda r: r["started_at"]):
        if item["usable_windows"]:
            latest[item["game"]] = item["_id"]
    usable = {item["_id"]: item["usable_windows"] for item in rounds}
    validation = sum(usable[session_id] for session_id in latest.values())
    training = sum(usable.values()) - validation
    return {
        "state": onboarding["state"], "error": onboarding.get("error"), "model_id": onboarding.get("model_id"),
        "seconds": seconds, "required_seconds": GAME_SECONDS,
        "sessions": sorted(usable), "validation_sessions": sorted(latest.values()),
        "training_windows": training, "validation_windows": validation,
        "required_training_windows": MIN_TRAINING_WINDOWS, "required_validation_windows": MIN_VALIDATION_WINDOWS,
        "ready_to_train": all(seconds[game] >= GAME_SECONDS for game in GAMES)
        and training >= MIN_TRAINING_WINDOWS and validation >= MIN_VALIDATION_WINDOWS,
    }


def register(app: Flask, database: Database, trainer: Trainer, window_error: Callable[[Document], str | None]) -> None:
    from authlib.integrations.flask_client import OAuth

    app.secret_key = os.environ["SESSION_SECRET"]
    secure = os.environ["SSOJET_REDIRECT_URI"].startswith("https://")
    app.config.update(SESSION_COOKIE_SECURE=secure, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME)
    oauth = OAuth(app)
    oauth.register(
        name="ssojet", client_id=os.environ["SSOJET_CLIENT_ID"], client_secret=os.environ["SSOJET_CLIENT_SECRET"],
        server_metadata_url=os.environ["SSOJET_AUTHORITY"].rstrip("/") + "/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256"},
    )
    database.sessions.create_index("expires_at", expireAfterSeconds=0)
    database.rate_limits.create_index("expires_at", expireAfterSeconds=0)
    database.pairing_codes.create_index("expires_at", expireAfterSeconds=0)
    database.pairing_codes.create_index("code_hash", unique=True)
    database.devices.create_index("token_hash", unique=True)
    database.onboarding.create_index([("user_id", ASCENDING), ("device_id", ASCENDING)], unique=True)
    database.windows.create_index(
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
        # The cookie holds only a random session ID; the record in Atlas is what logout and expiry revoke.
        sid = session.get("sid")
        record = database.sessions.find_one({"_id": digest(sid), "expires_at": {"$gt": now()}}) \
            if isinstance(sid, str) else None
        return record["user_id"] if record else None

    def current_user() -> str:
        user_id = signed_in_user()
        if user_id is None:
            abort(401)
        return user_id

    def json_body() -> Document:
        # JSON-only bodies plus SameSite=Lax cookies block cross-site form posts.
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
        device = database.devices.find_one({"token_hash": digest(header.removeprefix("Bearer "))}) \
            if header.startswith("Bearer ") else None
        if device is None or device.get("revoked_at") is not None:
            abort(401)
        return device

    @blueprint.get("/login")
    def login():
        throttle(f"login:{client_ip()}", LOGIN_LIMIT)
        return oauth.ssojet.authorize_redirect(os.environ["SSOJET_REDIRECT_URI"])

    @blueprint.get("/auth/callback")
    def callback():
        throttle(f"login:{client_ip()}", LOGIN_LIMIT)
        token = oauth.ssojet.authorize_access_token()
        claims = token.get("userinfo") or oauth.ssojet.userinfo(token=token)
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            abort(401)
        database.users.update_one({"_id": subject}, {
            "$set": {"email": claims.get("email"), "last_login_at": now()},
            "$setOnInsert": {"created_at": now()},
        }, upsert=True)
        sid = secrets.token_urlsafe(32)
        database.sessions.insert_one({"_id": digest(sid), "user_id": subject, "expires_at": now() + SESSION_LIFETIME})
        session.clear()
        session.permanent = True
        session["sid"] = sid
        return redirect(url_for("hosted.onboarding_page"))

    @blueprint.post("/logout")
    def logout():
        sid = session.get("sid")
        if isinstance(sid, str):
            database.sessions.delete_one({"_id": digest(sid)})
        session.clear()
        return redirect(url_for("hosted.onboarding_page"))

    @blueprint.get("/onboarding")
    def onboarding_page():
        return render_template("onboarding.html", signed_in=signed_in_user() is not None)

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
        database.rounds.update_many({"onboarding_id": onboarding["_id"], "ended_at": None},
                                    {"$set": {"ended_at": now(), "credited_ms": 0}})
        session_id = uuid4().hex
        database.rounds.insert_one({"_id": session_id, "onboarding_id": onboarding["_id"], "game": game,
                                    "started_at": now(), "ended_at": None, "credited_ms": 0, "usable_windows": 0})
        return jsonify(session_id=session_id), 201

    @blueprint.post("/api/onboarding/rounds/current/<outcome>")
    def end_round(outcome: str):
        onboarding = user_onboarding(current_user())
        json_body()
        if outcome not in ("finish", "cancel"):
            abort(404)
        active = database.rounds.find_one({"onboarding_id": onboarding["_id"], "ended_at": None})
        if active is None:
            abort(409)
        ended = now()
        started = cast(datetime, active["started_at"]).replace(tzinfo=timezone.utc)
        credited = min(int((ended - started).total_seconds() * 1000), ROUND_MS) if outcome == "finish" else 0
        database.rounds.update_one({"_id": active["_id"]}, {"$set": {"ended_at": ended, "credited_ms": credited}})
        status = progress(database, onboarding)
        if status["ready_to_train"]:
            start_training(onboarding, status)
            status = progress(database, user_onboarding(onboarding["user_id"]))
        return jsonify(status)

    @blueprint.post("/api/agent/pair")
    def pair():
        throttle(f"pair:{client_ip()}", PAIR_LIMIT)
        code = json_body().get("code")
        if not isinstance(code, str):
            abort(422)
        pairing = database.pairing_codes.find_one_and_update(
            {"code_hash": digest(code.strip().upper()), "used_at": None, "expires_at": {"$gt": now()}},
            {"$set": {"used_at": now()}},
        )
        if pairing is None:
            return jsonify(error="pairing code is invalid, expired, or already used"), 404
        device_id, token = uuid4().hex, secrets.token_urlsafe(32)
        database.devices.insert_one({"_id": device_id, "user_id": pairing["user_id"], "token_hash": digest(token),
                                     "created_at": now(), "revoked_at": None})
        database.onboarding.insert_one({"_id": uuid4().hex, "user_id": pairing["user_id"], "device_id": device_id,
                                        "state": "collecting", "created_at": now()})
        return jsonify(device_id=device_id, token=token), 201

    @blueprint.get("/api/agent/onboarding")
    def agent_onboarding():
        device = device_auth()
        onboarding = database.onboarding.find_one({"device_id": device["_id"]})
        if onboarding is None:
            abort(404)
        active = database.rounds.find_one({"onboarding_id": onboarding["_id"], "ended_at": None})
        return jsonify(state=onboarding["state"], session_id=active["_id"] if active else None)

    @blueprint.post("/api/agent/windows")
    def upload_window():
        device = device_auth()
        throttle(f"upload:{device['_id']}", UPLOAD_LIMIT)
        window = json_body()
        error = window_error(window)
        if error:
            return jsonify(error=error), 422
        if window["device_id"] != device["_id"] or window["mode"] != "enrollment":
            return jsonify(error="onboarding windows must be enrollment windows from this device"), 422
        onboarding = database.onboarding.find_one({"device_id": device["_id"], "state": "collecting"})
        active = onboarding and database.rounds.find_one(
            {"_id": window["session_id"], "onboarding_id": onboarding["_id"], "ended_at": None}
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
            database.rounds.update_one({"_id": active["_id"]}, {"$inc": {"usable_windows": 1}})
        return jsonify(ok=True, usable=usable), 201

    app.register_blueprint(blueprint)
