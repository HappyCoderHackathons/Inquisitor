from __future__ import annotations

import os
import re
import secrets
import threading
from pathlib import Path

from flask import Flask, g, jsonify, request

from .model import ModelStore


def create_app() -> Flask:
    token = Path(os.environ["ML_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token):
        raise RuntimeError("ML_TOKEN_FILE must contain a dedicated token of at least 32 URL-safe characters")
    store = ModelStore(Path(os.environ["ML_DATA_DIR"]))
    training_lock = threading.Lock()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

    @app.before_request
    def authenticate():
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not request.headers.get("Authorization", "").startswith("Bearer ") or not secrets.compare_digest(
            supplied.encode("utf-8"), token.encode("ascii")
        ):
            return jsonify(error="unauthorized"), 401
        if request.path != "/health":
            g.user_id = request.headers.get("X-User-ID", "")
            g.device_id = request.headers.get("X-Device-ID", "")
            if not g.user_id or not g.device_id or max(len(g.user_id), len(g.device_id)) > 256:
                return jsonify(error="authorized user and device headers are required"), 400
        return None

    def body(fields: set[str]) -> dict:
        value = request.get_json()
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("request fields do not match the endpoint contract")
        return value

    @app.get("/health")
    def health():
        return jsonify(state="ok")

    @app.post("/models/train")
    def train():
        value = body({"windows", "validation_session_id"})
        if not isinstance(value["windows"], list) or not isinstance(value["validation_session_id"], str):
            raise ValueError("windows must be a list and validation_session_id a string")
        if not training_lock.acquire(blocking=False):
            return jsonify(error="training is busy; retry later"), 503
        try:
            metadata = store.train(value["windows"], g.user_id, g.device_id, value["validation_session_id"])
            return jsonify(metadata), 201
        finally:
            training_lock.release()

    @app.get("/models/<model_id>")
    def metadata(model_id: str):
        return jsonify(store.metadata(model_id, g.user_id, g.device_id))

    @app.post("/models/<model_id>/score")
    def score(model_id: str):
        return jsonify(store.score(model_id, body({"window"})["window"], g.user_id, g.device_id))

    @app.delete("/models/<model_id>")
    def delete(model_id: str):
        store.delete(model_id, g.user_id, g.device_id)
        return "", 204

    @app.errorhandler(ValueError)
    def invalid(error: ValueError):
        return jsonify(error=str(error)), 422

    @app.errorhandler(PermissionError)
    def forbidden(error: PermissionError):
        return jsonify(error="forbidden"), 403

    @app.errorhandler(FileNotFoundError)
    def missing(error: FileNotFoundError):
        return jsonify(error="model not found"), 404

    @app.errorhandler(RuntimeError)
    def unavailable(error: RuntimeError):
        return jsonify(error="training is unavailable on this service"), 503

    return app
