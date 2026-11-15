"""Atlas collections for model versions, inference results, and alerts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING
from pymongo.database import Database

Document = dict[str, Any]


def ensure_indexes(database: Database) -> None:
    for collection, keys in (
        (database.model_versions, [("device_id", ASCENDING), ("created_at", ASCENDING)]),
        (database.inference_results, [("device_id", ASCENDING), ("created_at", ASCENDING)]),
        (database.alerts, [("device_id", ASCENDING), ("status", ASCENDING), ("created_at", ASCENDING)]),
    ):
        collection.create_index(keys)


def record_model_version(
    database: Database,
    *,
    user_id: str,
    device_id: str,
    metadata: Document,
    active: bool,
    created_at: datetime,
) -> None:
    model_id = metadata["model_id"]
    database.model_versions.update_one(
        {"_id": model_id},
        {"$set": {
            "user_id": user_id,
            "device_id": device_id,
            "state": metadata.get("state", "ready"),
            "algorithm": metadata.get("algorithm"),
            "active": active,
            "metadata": metadata,
            "created_at": created_at,
            "updated_at": created_at,
        }},
        upsert=True,
    )
    if active:
        database.model_versions.update_many(
            {"user_id": user_id, "device_id": device_id, "_id": {"$ne": model_id}},
            {"$set": {"active": False}},
        )


def activate_model_version(
    database: Database, *, user_id: str, device_id: str, model_id: str, activated_at: datetime,
) -> None:
    database.model_versions.update_many(
        {"user_id": user_id, "device_id": device_id, "_id": {"$ne": model_id}}, {"$set": {"active": False}},
    )
    database.model_versions.update_one(
        {"_id": model_id, "user_id": user_id, "device_id": device_id},
        {"$set": {"active": True, "updated_at": activated_at}},
    )


def record_inference(
    database: Database,
    *,
    user_id: str,
    device_id: str,
    window: Document,
    result: Document,
    created_at: datetime,
) -> str:
    inference_id = uuid4().hex
    database.inference_results.insert_one({
        "_id": inference_id,
        "user_id": user_id,
        "device_id": device_id,
        "session_id": window["session_id"],
        "sequence": window["sequence"],
        "model_id": result.get("model_id"),
        "classification": result.get("state"),
        "score": result.get("score"),
        "threshold": result.get("threshold"),
        "result": result,
        "created_at": created_at,
    })
    # A single unusual window is not an alert. The caller applies the persistence rule.
    return inference_id


def delete_device_records(database: Database, user_id: str, device_id: str) -> None:
    database.model_versions.delete_many({"user_id": user_id, "device_id": device_id})
    database.inference_results.delete_many({"user_id": user_id, "device_id": device_id})
    database.alerts.delete_many({"user_id": user_id, "device_id": device_id})
    database.windows.delete_many({"user_id": user_id, "device_id": device_id})
    database.monitoring_windows.delete_many({"user_id": user_id, "device_id": device_id})