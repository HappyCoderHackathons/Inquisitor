"""Account-scoped behavioral documents and the retention policy that deletes them."""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from pymongo import ASCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError

RETENTION_ENV = "INQUISITOR_MONITORING_RETENTION_SECONDS"
DEFAULT_MONITORING_RETENTION_SECONDS = 7 * 24 * 60 * 60


def retention_seconds_from_env() -> int:
    raw = os.environ.get(RETENTION_ENV)
    if raw is None or raw == "":
        return DEFAULT_MONITORING_RETENTION_SECONDS
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{RETENTION_ENV} must be a positive integer") from error
    if value <= 0:
        raise RuntimeError(f"{RETENTION_ENV} must be a positive integer")
    return value


def _plain(value: dict[str, object]) -> dict[str, Any]:
    return json.loads(json.dumps(value, allow_nan=False))


class AccountStore:
    def __init__(self, database: Database, prefix: str, artifact_root: Path, retention_seconds: int) -> None:
        if retention_seconds <= 0:
            raise ValueError("monitoring retention must be a positive number of seconds")
        self.windows = database[f"{prefix}windows"]
        self.inference_results = database[f"{prefix}inference_results"]
        self.alerts = database[f"{prefix}alerts"]
        self.models = database[f"{prefix}models"]
        self.retention_policy = database[f"{prefix}retention_policy"]
        self.artifact_root = artifact_root
        self._lock = threading.RLock()
        self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.windows.create_index(
            [("user_id", ASCENDING), ("device_id", ASCENDING), ("session_id", ASCENDING), ("sequence", ASCENDING)],
            unique=True,
        )
        for collection in (self.inference_results, self.alerts, self.models):
            collection.create_index([("user_id", ASCENDING), ("device_id", ASCENDING)])
        self.retention_policy.update_one(
            {"_id": 1}, {"$setOnInsert": {"monitoring_retention_seconds": retention_seconds}}, upsert=True,
        )

    def check_available(self) -> None:
        self.retention_seconds()

    def _artifact_path(self, model_id: str) -> Path:
        try:
            if UUID(model_id).hex != model_id:
                raise ValueError
        except ValueError as error:
            raise ValueError("model id is not a stored artifact id") from error
        root = self.artifact_root.resolve()
        path = (root / model_id).resolve()
        if path.parent != root:
            raise ValueError("artifact path escapes the artifact root")
        return path

    def save_window(
        self, user_id: str, device_id: str, window: dict[str, object], created_at: datetime | None = None,
    ) -> bool:
        if window.get("device_id") != device_id or window.get("mode") not in ("enrollment", "monitoring"):
            raise ValueError("window does not belong to this device")
        try:
            self.windows.insert_one({
                "user_id": user_id, "device_id": device_id, "session_id": window["session_id"],
                "sequence": window["sequence"], "mode": window["mode"], "created_at": self._stamp(created_at),
                "window": _plain(window),
            })
        except DuplicateKeyError:
            return False
        return True

    def save_result(
        self, user_id: str, device_id: str, result: dict[str, object], created_at: datetime | None = None,
    ) -> None:
        model_id = result.get("model_id", result.get("model_version"))
        state = result.get("state")
        if type(model_id) is not str or type(state) is not str:
            raise ValueError("inference result requires model_id and state")
        stamp = self._stamp(created_at)
        payload = _plain(result)
        self.inference_results.insert_one({
            "user_id": user_id, "device_id": device_id, "model_id": model_id, "created_at": stamp, "result": payload,
        })
        if state == "UNUSUAL":
            self.alerts.insert_one({"user_id": user_id, "device_id": device_id, "created_at": stamp, "alert": payload})

    def register_model_version(
        self, user_id: str, device_id: str, metadata: dict[str, object], *, active: bool,
    ) -> Path:
        model_id = metadata.get("model_id")
        if type(model_id) is not str:
            raise ValueError("model metadata requires model_id")
        state = metadata.get("state", "ready")
        if type(state) is not str:
            raise ValueError("model metadata requires a string state")
        algorithm = metadata.get("algorithm")
        path = self._artifact_path(model_id)
        payload = _plain(metadata)
        with self._lock:
            if active:
                self.models.update_many({"user_id": user_id, "device_id": device_id}, {"$set": {"active": False}})
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.models.update_one(
                {"_id": model_id},
                {
                    "$set": {
                        "active": active, "job_state": state, "metadata": payload,
                        "algorithm": str(algorithm) if algorithm is not None else None,
                    },
                    "$setOnInsert": {"user_id": user_id, "device_id": device_id, "created_at": self._stamp(None)},
                },
                upsert=True,
            )
        return path

    def add_model(self, user_id: str, device_id: str, model_id: str, *, active: bool) -> Path:
        return self.register_model_version(
            user_id, device_id, {"model_id": model_id, "state": "ready"}, active=active,
        )

    def set_model_job_state(self, user_id: str, device_id: str, model_id: str, state: str) -> None:
        if state not in {"queued", "running", "ready", "failed"}:
            raise ValueError("job state must be queued, running, ready, or failed")
        updated = self.models.update_one(
            {"_id": model_id, "user_id": user_id, "device_id": device_id}, {"$set": {"job_state": state}},
        )
        if updated.matched_count != 1:
            raise FileNotFoundError("model version not found for this account and device")

    def deactivate(self, user_id: str, device_id: str) -> bool:
        updated = self.models.update_many(
            {"user_id": user_id, "device_id": device_id, "active": True}, {"$set": {"active": False}},
        )
        return updated.modified_count > 0

    def reset_enrollment(self, user_id: str, device_id: str) -> int:
        scope = {"user_id": user_id, "device_id": device_id}
        with self._lock:
            deleted = self.windows.delete_many({**scope, "mode": "enrollment"}).deleted_count
            active = {**scope, "active": True}
            for row in self.models.find(active, {"_id": 1}):
                self._remove_artifact(row["_id"])
            self.models.delete_many(active)
        return deleted

    def delete_device(self, user_id: str, device_id: str) -> None:
        self._delete_devices({"user_id": user_id, "device_id": device_id})

    def delete_user(self, user_id: str) -> None:
        self._delete_devices({"user_id": user_id})

    def _delete_devices(self, scope: dict[str, str]) -> None:
        with self._lock:
            for row in self.models.find(scope, {"_id": 1}):
                self._remove_artifact(row["_id"])
            for collection in (self.windows, self.inference_results, self.alerts, self.models):
                collection.delete_many(scope)

    def retention_seconds(self) -> int:
        policy = self.retention_policy.find_one({"_id": 1})
        if policy is None:
            raise PyMongoError("account database retention policy is unavailable")
        return int(policy["monitoring_retention_seconds"])

    def set_retention(self, seconds: int) -> int:
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            raise ValueError("monitoring_retention_seconds must be a positive integer")
        self.retention_policy.update_one({"_id": 1}, {"$set": {"monitoring_retention_seconds": seconds}}, upsert=True)
        return seconds

    def purge_monitoring(self, now: datetime) -> dict[str, int]:
        cutoff = {"created_at": {"$lt": self._stamp(now) - timedelta(seconds=self.retention_seconds())}}
        return {
            "windows": self.windows.delete_many({"mode": "monitoring", **cutoff}).deleted_count,
            "inference_results": self.inference_results.delete_many(cutoff).deleted_count,
            "alerts": self.alerts.delete_many(cutoff).deleted_count,
        }

    def counts(self, user_id: str) -> dict[str, int]:
        scope = {"user_id": user_id}
        return {
            "windows": self.windows.count_documents(scope),
            "enrollment": self.windows.count_documents({**scope, "mode": "enrollment"}),
            "monitoring": self.windows.count_documents({**scope, "mode": "monitoring"}),
            "inference_results": self.inference_results.count_documents(scope),
            "alerts": self.alerts.count_documents(scope),
            "models": self.models.count_documents(scope),
            "active_models": self.models.count_documents({**scope, "active": True}),
        }

    def _remove_artifact(self, model_id: str) -> None:
        path = self._artifact_path(model_id)
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def _stamp(value: datetime | None) -> datetime:
        stamp = value or datetime.now(UTC)
        if stamp.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return stamp.astimezone(UTC)
