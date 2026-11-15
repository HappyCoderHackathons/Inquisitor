"""Local hosted-mode server for the Rust tray's end-to-end link tests. Test only.

Runs the real `Frontend/hosted.py` routes, including the agent WebSocket, on
http://127.0.0.1:5077 with an in-memory database, a stub model service, and no
sign-in. It seeds the pairing code ABCD2345 (single use, 10 minutes) and adds
`/__test/*` helper routes that a real deployment must never have.

    uv run --no-project --python 3.13 --with-requirements Frontend/requirements.txt \
        --with "mongomock>=4.3,<5" python scripts/agent_link_test_server.py
    INQUISITOR_TEST_SERVER=http://127.0.0.1:5077 cargo test -- --ignored live_

Restart it before each run: the code works once.
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

FRONTEND = Path(__file__).resolve().parents[1] / "Frontend"
sys.path.insert(0, str(FRONTEND))

import hosted
import mongomock
import server
from flask import Flask, Response, jsonify

ADDRESS, PORT = "127.0.0.1", 5077
PAIRING_CODE = "ABCD2345"
TEST_USER = "better-auth:agent-link-test"


def no_session(method: str, url: str, **_: object) -> Mock:
    """Better Auth stand-in: nobody is signed in, which the agent routes do not need."""
    response = Mock(status_code=200, content=b"{}", headers={"Content-Type": "application/json"})
    response.json.return_value = None
    response.raw.headers.getlist.return_value = []
    return response


def score(user_id: str, device_id: str, window: dict[str, object]) -> dict[str, object]:
    return {"model_id": "model-test", "state": "NORMAL", "score": -0.4, "threshold": 0.1, "score_delta": -0.5}


def create_app() -> Flask:
    database = mongomock.MongoClient(tz_aware=True).db
    app = Flask(__name__, template_folder=str(FRONTEND / "templates"))
    env = {name: "test-only" for name in hosted.ENV} | {"BETTER_AUTH_URL": f"http://{ADDRESS}:{PORT}"}
    with patch.dict(os.environ, env):
        hosted.register(
            app, database, lambda *_: "model-test", score, server.onboarding_window_error,
            lambda *_: None, lambda *_: None, lambda *_: {}, lambda *_: None, lambda *_: None,
        )
    database.pairing_codes.insert_one({
        "code_hash": hosted.digest(PAIRING_CODE), "user_id": TEST_USER,
        "expires_at": datetime.now(UTC) + timedelta(minutes=10), "used_at": None,
    })

    @app.get("/")
    def index() -> Response:
        """Status page: this server has no website pages, only the API routes the tray uses."""
        unused = database.pairing_codes.count_documents({"used_at": None}) > 0
        return jsonify(
            server="Inquisitor agent link test server (test only, in-memory, no sign-in)",
            pairing_code=PAIRING_CODE if unused else "used; restart this server for a new one",
            tray_address=f"http://{ADDRESS}:{PORT}",
            devices=[{"id": row["_id"], "name": row.get("name"), "state": row.get("pairing_state"),
                      "connected": row.get("ws_connected"), "paused": row.get("agent_paused"),
                      "monitoring_windows": database.monitoring_windows.count_documents({"device_id": row["_id"]})}
                     for row in database.devices.find({})],
            helpers={
                "GET /__test/devices": "list device ids",
                "POST /__test/ready/<id>": "pretend a model is trained, so the tray switches to monitoring",
                "POST /__test/revoke/<id>": "remove the device from the account",
                "GET /__test/device/<id>": "connection, pause, and upload count for one device",
            },
        )

    @app.get("/__test/devices")
    def devices() -> Response:
        rows = database.devices.find({}, {"_id": 1, "name": 1, "pairing_state": 1})
        return jsonify(devices=[{"id": row["_id"], "name": row.get("name"), "state": row.get("pairing_state")}
                                for row in rows])

    @app.post("/__test/ready/<device_id>")
    def ready(device_id: str) -> Response:
        database.onboarding.update_one({"device_id": device_id}, {"$set": {"state": "ready", "model_id": "model-test"}})
        return jsonify(ok=True)

    @app.post("/__test/revoke/<device_id>")
    def revoke(device_id: str) -> Response:
        database.devices.update_one(
            {"_id": device_id}, {"$set": {"revoked_at": datetime.now(UTC), "pairing_state": "revoked"}},
        )
        return jsonify(ok=True)

    @app.get("/__test/device/<device_id>")
    def device(device_id: str) -> Response:
        found = database.devices.find_one({"_id": device_id}) or {}
        return jsonify(
            ws_connected=found.get("ws_connected"),
            agent_paused=found.get("agent_paused"),
            monitoring=database.monitoring_windows.count_documents({"device_id": device_id}),
        )

    return app


if __name__ == "__main__":
    with patch("hosted.requests.request", side_effect=no_session):
        create_app().run(ADDRESS, PORT, threaded=True)
