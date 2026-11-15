"""Hosted onboarding: SSOJet sign-in, agent pairing, and account storage in Atlas."""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from flask import Blueprint, Flask, abort, jsonify, redirect, render_template, request, session, url_for
from pymongo.database import Database

SESSION_LIFETIME = timedelta(hours=12)
PAIRING_TTL = timedelta(minutes=10)
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ENV = (
    "SSOJET_AUTHORITY", "SSOJET_CLIENT_ID", "SSOJET_CLIENT_SECRET", "SSOJET_REDIRECT_URI",
    "SESSION_SECRET", "MONGODB_URI", "MONGODB_DB",
)

Document = dict[str, Any]


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


def register(app: Flask, database: Database) -> None:
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
    database.pairing_codes.create_index("expires_at", expireAfterSeconds=0)
    database.pairing_codes.create_index("code_hash", unique=True)
    database.devices.create_index("token_hash", unique=True)
    blueprint = Blueprint("hosted", __name__)

    def current_user() -> str:
        user_id = session.get("user_id")
        if not isinstance(user_id, str):
            abort(401)
        return user_id

    def json_body() -> Document:
        # JSON-only bodies plus SameSite=Lax cookies block cross-site form posts.
        value = request.get_json(silent=True) if request.is_json else None
        if not isinstance(value, dict):
            abort(415)
        return value

    @blueprint.get("/login")
    def login():
        return oauth.ssojet.authorize_redirect(os.environ["SSOJET_REDIRECT_URI"])

    @blueprint.get("/auth/callback")
    def callback():
        token = oauth.ssojet.authorize_access_token()
        claims = token.get("userinfo") or oauth.ssojet.userinfo(token=token)
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            abort(401)
        database.users.update_one({"_id": subject}, {
            "$set": {"email": claims.get("email"), "last_login_at": now()},
            "$setOnInsert": {"created_at": now()},
        }, upsert=True)
        session.clear()
        session.permanent = True
        session["user_id"] = subject
        return redirect(url_for("hosted.onboarding_page"))

    @blueprint.post("/logout")
    def logout():
        session.clear()
        return {"ok": True}

    @blueprint.get("/onboarding")
    def onboarding_page():
        return render_template("onboarding.html", signed_in=isinstance(session.get("user_id"), str))

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

    @blueprint.post("/api/agent/pair")
    def pair():
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
        return jsonify(device_id=device_id, token=token), 201

    app.register_blueprint(blueprint)
