"""MongoDB collections for arcade game samples and browser feature windows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

Document = dict[str, Any]
WINDOW_META_FIELDS = frozenset({"_id", "created_at"})


def ensure_indexes(database: Database) -> None:
    database.arcade_samples.create_index([("created_at", ASCENDING)])
    database.arcade_feature_windows.create_index(
        [("device_id", ASCENDING), ("session_id", ASCENDING), ("sequence", ASCENDING)],
        unique=True,
    )
    database.arcade_feature_windows.create_index([("mode", ASCENDING), ("created_at", ASCENDING)])


def export_window_line(document: Document) -> str:
    payload = {key: value for key, value in document.items() if key not in WINDOW_META_FIELDS}
    return json.dumps(payload, allow_nan=False, separators=(",", ":"))


def parse_created_at(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)