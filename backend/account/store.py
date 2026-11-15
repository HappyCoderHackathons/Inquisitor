"""Account-scoped behavioral rows and the retention policy that deletes them."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

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


class AccountStore:
    def __init__(self, database: Path, artifact_root: Path, retention_seconds: int) -> None:
        if retention_seconds <= 0:
            raise ValueError("monitoring retention must be a positive number of seconds")
        self.database = database
        self.artifact_root = artifact_root
        self._lock = threading.RLock()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock, closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS windows (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    window_json TEXT NOT NULL,
                    UNIQUE(user_id, device_id, session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS inference_results (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    alert_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS models (
                    model_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS retention_policy (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    monitoring_retention_seconds INTEGER NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO retention_policy (id, monitoring_retention_seconds) VALUES (1, ?)",
                (retention_seconds,),
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def check_available(self) -> None:
        uri = f"{self.database.resolve().as_uri()}?mode=ro"
        with self._lock, closing(sqlite3.connect(uri, uri=True, timeout=1)) as conn:
            policy = conn.execute(
                "SELECT monitoring_retention_seconds FROM retention_policy WHERE id = 1"
            ).fetchone()
            if policy is None:
                raise sqlite3.DatabaseError("account database retention policy is unavailable")

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
        stamp = self._stamp(created_at)
        with self._lock, closing(self._connect()) as conn:
            try:
                conn.execute(
                    """INSERT INTO windows
                    (user_id, device_id, session_id, sequence, mode, created_at, window_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id, device_id, window["session_id"], window["sequence"], window["mode"], stamp,
                        json.dumps(window, allow_nan=False, separators=(",", ":")),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
            conn.commit()
        return True

    def save_result(
        self, user_id: str, device_id: str, result: dict[str, object], created_at: datetime | None = None,
    ) -> None:
        model_id = result.get("model_id", result.get("model_version"))
        state = result.get("state")
        if type(model_id) is not str or type(state) is not str:
            raise ValueError("inference result requires model_id and state")
        stamp = self._stamp(created_at)
        payload = json.dumps(result, allow_nan=False, separators=(",", ":"))
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO inference_results (user_id, device_id, model_id, created_at, result_json)
                VALUES (?, ?, ?, ?, ?)""",
                (user_id, device_id, model_id, stamp, payload),
            )
            if state == "UNUSUAL":
                conn.execute(
                    "INSERT INTO alerts (user_id, device_id, created_at, alert_json) VALUES (?, ?, ?, ?)",
                    (user_id, device_id, stamp, payload),
                )
            conn.commit()

    def add_model(self, user_id: str, device_id: str, model_id: str, *, active: bool) -> Path:
        path = self._artifact_path(model_id)
        stamp = self._stamp(None)
        with self._lock, closing(self._connect()) as conn:
            if active:
                conn.execute(
                    "UPDATE models SET active = 0 WHERE user_id = ? AND device_id = ?",
                    (user_id, device_id),
                )
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            conn.execute(
                """INSERT INTO models (model_id, user_id, device_id, active, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (model_id, user_id, device_id, int(active), stamp),
            )
            conn.commit()
        return path

    def deactivate(self, user_id: str, device_id: str) -> bool:
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                "UPDATE models SET active = 0 WHERE user_id = ? AND device_id = ? AND active = 1",
                (user_id, device_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def reset_enrollment(self, user_id: str, device_id: str) -> int:
        with self._lock, closing(self._connect()) as conn:
            deleted = conn.execute(
                "DELETE FROM windows WHERE user_id = ? AND device_id = ? AND mode = 'enrollment'",
                (user_id, device_id),
            ).rowcount
            active = conn.execute(
                "SELECT model_id FROM models WHERE user_id = ? AND device_id = ? AND active = 1",
                (user_id, device_id),
            ).fetchall()
            for row in active:
                self._remove_artifact(cast(str, row["model_id"]))
            conn.execute(
                "DELETE FROM models WHERE user_id = ? AND device_id = ? AND active = 1",
                (user_id, device_id),
            )
            conn.commit()
            return deleted

    def delete_user(self, user_id: str) -> None:
        with self._lock, closing(self._connect()) as conn:
            model_ids = [
                cast(str, row["model_id"])
                for row in conn.execute("SELECT model_id FROM models WHERE user_id = ?", (user_id,))
            ]
            for model_id in model_ids:
                self._remove_artifact(model_id)
            conn.execute("DELETE FROM windows WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM inference_results WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM alerts WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM models WHERE user_id = ?", (user_id,))
            conn.commit()

    def retention_seconds(self) -> int:
        with self._lock, closing(self._connect()) as conn:
            return int(conn.execute("SELECT monitoring_retention_seconds n FROM retention_policy").fetchone()["n"])

    def set_retention(self, seconds: int) -> int:
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            raise ValueError("monitoring_retention_seconds must be a positive integer")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("UPDATE retention_policy SET monitoring_retention_seconds = ?", (seconds,))
            conn.commit()
        return seconds

    def purge_monitoring(self, now: datetime) -> dict[str, int]:
        self._stamp(now)
        cutoff = (now.astimezone(UTC) - timedelta(seconds=self.retention_seconds())).isoformat(timespec="microseconds")
        with self._lock, closing(self._connect()) as conn:
            removed = {
                "windows": conn.execute(
                    "DELETE FROM windows WHERE mode = 'monitoring' AND created_at < ?", (cutoff,)
                ).rowcount,
                "inference_results": conn.execute(
                    "DELETE FROM inference_results WHERE created_at < ?", (cutoff,)
                ).rowcount,
                "alerts": conn.execute("DELETE FROM alerts WHERE created_at < ?", (cutoff,)).rowcount,
            }
            conn.commit()
        return removed

    def counts(self, user_id: str) -> dict[str, int]:
        with self._lock, closing(self._connect()) as conn:
            def count(sql: str) -> int:
                return int(conn.execute(sql, (user_id,)).fetchone()["n"])

            return {
                "windows": count("SELECT COUNT(*) n FROM windows WHERE user_id = ?"),
                "enrollment": count("SELECT COUNT(*) n FROM windows WHERE user_id = ? AND mode = 'enrollment'"),
                "monitoring": count("SELECT COUNT(*) n FROM windows WHERE user_id = ? AND mode = 'monitoring'"),
                "inference_results": count("SELECT COUNT(*) n FROM inference_results WHERE user_id = ?"),
                "alerts": count("SELECT COUNT(*) n FROM alerts WHERE user_id = ?"),
                "models": count("SELECT COUNT(*) n FROM models WHERE user_id = ?"),
                "active_models": count("SELECT COUNT(*) n FROM models WHERE user_id = ? AND active = 1"),
            }

    def _remove_artifact(self, model_id: str) -> None:
        path = self._artifact_path(model_id)
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def _stamp(value: datetime | None) -> str:
        stamp = value or datetime.now(UTC)
        if stamp.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return stamp.astimezone(UTC).isoformat(timespec="microseconds")
