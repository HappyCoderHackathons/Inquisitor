from __future__ import annotations

import os
import re
import secrets
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, g, jsonify, request

from backend.account.store import AccountStore, retention_seconds_from_env
from backend.http_security import API_CSP, apply_security_headers

from .model import LABELED_PROFILES, ModelStore, validate_window
from .published import PublishedModel


def create_app() -> Flask:
    token = Path(os.environ["ML_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token):
        raise RuntimeError("ML_TOKEN_FILE must contain a dedicated token of at least 32 URL-safe characters")
    data_dir = Path(os.environ["ML_DATA_DIR"])
    store = ModelStore(data_dir)
    account = AccountStore(data_dir / "account.sqlite3", data_dir / "account-artifacts", retention_seconds_from_env())
    published_path = os.environ.get("ML_PUBLISHED_MODEL_FILE")
    published = PublishedModel(Path(published_path)) if published_path else None
    published_user = os.environ.get("ML_PUBLISHED_USER_ID")
    published_device = os.environ.get("ML_PUBLISHED_DEVICE_ID")
    published_model_id = os.environ.get("ML_PUBLISHED_MODEL_ID")
    if published and (not published_user or not published_device):
        raise RuntimeError("published inference requires configured owner and device IDs")
    if published and published_model_id:
        try:
            metadata = store.ready_metadata(published_model_id, published_user, published_device)
        except FileNotFoundError:
            published = None
        else:
            if (metadata["feature_names"] != list(published.names) or metadata["threshold"] != published.threshold
                    or metadata["feature_profile"] != published.profile):
                raise RuntimeError("published artifact differs from the configured owned model")
    training_lock = threading.Lock()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

    @app.after_request
    def add_security_headers(response):
        apply_security_headers(response.headers, policy=API_CSP, secure=request.is_secure)
        return response

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

    def body(fields: set[str], optional: set[str] | None = None) -> dict:
        value = request.get_json()
        if not isinstance(value, dict) or not fields.issubset(value) or not set(value).issubset(fields | (optional or set())):
            raise ValueError("request fields do not match the endpoint contract")
        return value

    def remember(result: dict[str, object]) -> dict[str, object]:
        account.save_result(g.user_id, g.device_id, result)
        return result

    @app.get("/health")
    def health():
        checks: dict[str, str] = {}
        for name, check in (("database", account.check_available), ("model_store", store.check_available)):
            try:
                check()
            except (OSError, sqlite3.Error):
                checks[name] = "unavailable"
            else:
                checks[name] = "available"
        healthy = all(status == "available" for status in checks.values())
        response = jsonify(state="ok" if healthy else "degraded", **checks)
        return response, 200 if healthy else 503

    def published_model() -> PublishedModel:
        if published is None:
            raise RuntimeError("published model is unavailable")
        if g.user_id != published_user:
            raise PermissionError("published model belongs to another account or device")
        if published_model_id:
            store.metadata(published_model_id, g.user_id, g.device_id)
        elif g.device_id != published_device:
            raise PermissionError("published model belongs to another account or device")
        return published

    @app.get("/model")
    def published_metadata():
        return jsonify(published_model().metadata())

    @app.post("/score")
    def published_score():
        model = published_model()
        return jsonify(remember(model.score(body({"window"})["window"], g.device_id)))

    def validate_training_request(value: dict[str, object]) -> tuple[bool, object]:
        profile = value.get("profile", "all")
        if not isinstance(profile, str) or profile not in {"all", "cadence-overlap", *LABELED_PROFILES}:
            raise ValueError("unknown feature profile")
        if "device_ids" in value and not isinstance(value["device_ids"], list):
            raise ValueError("device_ids must be an explicit list")
        labeled = profile in LABELED_PROFILES
        if labeled:
            if any(field in value for field in ("validation_session_id", "validation_session_ids")):
                raise ValueError("labeled profiles use all supplied data; omit validation session fields")
            if not isinstance(value.get("impostor_windows"), list):
                raise ValueError("labeled-overlap requires an impostor_windows list")
            sessions = None
        else:
            if ("validation_session_id" in value) == ("validation_session_ids" in value):
                raise ValueError("provide exactly one of validation_session_id or validation_session_ids")
            sessions = value.get("validation_session_id", value.get("validation_session_ids"))
            if not isinstance(sessions, (str, list)):
                raise ValueError("validation sessions must be session IDs")
        if not isinstance(value["windows"], list):
            raise TypeError("windows must be a list")
        return labeled, sessions

    def execute_training(
        value: dict[str, object], sessions: object, user_id: str, device_id: str, model_id: str | None = None,
    ) -> dict[str, object]:
        try:
            metadata = store.train(
                value["windows"], user_id, device_id, sessions,
                profile=value.get("profile", "all"),
                impostor_values=value.get("impostor_windows"),
                model_id=model_id,
                device_ids=value.get("device_ids"),
            )
        except Exception:
            if model_id is not None:
                account.set_model_job_state(user_id, device_id, model_id, "failed")
            raise
        account.register_model_version(user_id, device_id, metadata, active=False)
        return metadata

    @app.post("/models/train")
    def train():
        value = body({"windows"}, {"validation_session_id", "validation_session_ids", "profile", "impostor_windows", "device_ids"})
        _, sessions = validate_training_request(value)
        if not training_lock.acquire(blocking=False):
            return jsonify(error="training is busy; retry later"), 503
        if os.environ.get("ML_TRAIN_ASYNC") != "1":
            try:
                return jsonify(execute_training(value, sessions, g.user_id, g.device_id)), 201
            finally:
                training_lock.release()
        user_id, device_id = g.user_id, g.device_id
        try:
            model_id = store.reserve_training_job(user_id, device_id)
            account.register_model_version(user_id, device_id, {"model_id": model_id, "state": "queued"}, active=False)
        except BaseException:
            training_lock.release()
            raise

        def run_job() -> None:
            try:
                execute_training(value, sessions, user_id, device_id, model_id)
            except Exception:
                app.logger.exception("training job %s failed", model_id)
            finally:
                training_lock.release()

        queued = store.metadata(model_id, user_id, device_id)
        threading.Thread(target=run_job, daemon=True).start()
        return jsonify(queued), 202

    @app.get("/models/active")
    def active_metadata():
        return jsonify(store.metadata(store.active_model_id(g.user_id, g.device_id), g.user_id, g.device_id))

    @app.post("/windows")
    def save_window():
        window = validate_window(body({"window"})["window"])
        if window["device_id"] != g.device_id:
            raise PermissionError("window belongs to another device")
        result = None
        if window["mode"] == "monitoring":
            try:
                model_id = store.active_model_id(g.user_id, g.device_id)
            except FileNotFoundError:
                return jsonify(error="monitoring requires an active model for this account and device"), 409
            # Score before saving so a scoring failure leaves nothing stored and the window can be retried.
            try:
                result = store.score(model_id, window, g.user_id, g.device_id)
            except (EOFError, KeyError, OSError, RuntimeError, ValueError) as error:
                return jsonify(error=f"scoring failed: {error}"), 502
        if not account.save_window(g.user_id, g.device_id, window):
            return jsonify(error="This device/session/sequence window already exists"), 409
        if result is None:
            return jsonify(ok=True), 201
        return jsonify(remember(result)), 201

    @app.post("/models/active/score")
    def active_score():
        model_id = store.active_model_id(g.user_id, g.device_id)
        return jsonify(remember(store.score(model_id, body({"window"})["window"], g.user_id, g.device_id)))

    @app.put("/models/<model_id>/active")
    def activate(model_id: str):
        metadata = store.activate(model_id, g.user_id, g.device_id)
        account.register_model_version(g.user_id, g.device_id, metadata, active=True)
        return jsonify(metadata)

    @app.get("/models/<model_id>")
    def metadata(model_id: str):
        return jsonify(store.metadata(model_id, g.user_id, g.device_id))

    @app.post("/models/<model_id>/score")
    def score(model_id: str):
        return jsonify(remember(store.score(model_id, body({"window"})["window"], g.user_id, g.device_id)))

    @app.post("/models/active/deactivate")
    def deactivate():
        store.deactivate(g.user_id, g.device_id)
        return jsonify(active=False)

    @app.post("/enrollment/reset")
    def reset_enrollment():
        deleted_windows = account.reset_enrollment(g.user_id, g.device_id)
        removed_model = None
        try:
            removed_model = store.active_model_id(g.user_id, g.device_id)
        except FileNotFoundError:
            removed_model = None
        if removed_model is not None:
            store.delete(removed_model, g.user_id, g.device_id)
        return jsonify(ok=True, deleted_windows=deleted_windows, removed_model=removed_model)

    @app.delete("/me/data")
    def delete_my_data():
        removed_models = store.delete_user(g.user_id)
        account.delete_user(g.user_id)
        return jsonify(ok=True, models=removed_models)

    @app.get("/retention")
    def get_retention():
        return jsonify(monitoring_retention_seconds=account.retention_seconds())

    @app.put("/retention")
    def put_retention():
        value = body({"monitoring_retention_seconds"})["monitoring_retention_seconds"]
        return jsonify(monitoring_retention_seconds=account.set_retention(value))

    @app.post("/retention/purge")
    def purge_monitoring():
        if request.get_json(silent=True) not in (None, {}):
            raise ValueError("request fields do not match the endpoint contract")
        return jsonify(account.purge_monitoring(datetime.now(UTC)))

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
        return jsonify(error="requested model operation is unavailable on this service"), 503

    return app
