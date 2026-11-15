"""Hosted onboarding: SSOJet sign-in and account storage in Atlas."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Flask, abort, redirect, render_template, session, url_for
from pymongo.database import Database

SESSION_LIFETIME = timedelta(hours=12)
ENV = (
    "SSOJET_AUTHORITY", "SSOJET_CLIENT_ID", "SSOJET_CLIENT_SECRET", "SSOJET_REDIRECT_URI",
    "SESSION_SECRET", "MONGODB_URI", "MONGODB_DB",
)


def configured() -> bool:
    present = [name for name in ENV if os.environ.get(name)]
    if present and len(present) != len(ENV):
        missing = sorted(set(ENV) - set(present))
        raise RuntimeError(f"hosted onboarding is partly configured; missing {', '.join(missing)}")
    return bool(present)


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
    blueprint = Blueprint("hosted", __name__)

    def current_user() -> str:
        user_id = session.get("user_id")
        if not isinstance(user_id, str):
            abort(401)
        return user_id

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

    app.register_blueprint(blueprint)
