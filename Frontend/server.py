"""InputPrint Arcade: Flask host for opt-in, aggregate-only game telemetry."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, cast

from flask import Flask, jsonify, render_template, request
from jsonschema import Draft202012Validator, FormatChecker

import hosted

ROOT = Path(__file__).parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
from backend.account.store import AccountStore, retention_seconds_from_env

import security

LOCAL_USER_ID = "local-user"
LOCAL_DEVICE_ID = "local-device"
DB_PATH = Path(os.environ.get("INPUTPRINT_DB", ROOT / "inputprint.sqlite3"))
AGENT_FEATURE_VALIDATORS = {
    version: Draft202012Validator(
        json.loads((ROOT.parent / "shared" / f"feature-window-v{version}.schema.json").read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )
    for version in (1, 2, 3, 4)
}
FEATURE_VALIDATORS = {version: AGENT_FEATURE_VALIDATORS[version] for version in (1, 2)}
SAMPLE_FEATURE_WINDOW = json.loads(
    (ROOT.parent / "shared" / "fixtures" / "feature-window-v2.json").read_text(encoding="utf-8")
)["valid"]["enrollment"]
app = Flask(__name__, static_folder="static", template_folder="templates")
security.install(app)
account: AccountStore | None = None


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def open_account() -> AccountStore:
    global account
    artifact_root = Path(os.environ.get("INQUISITOR_ARTIFACT_DIR", DB_PATH.parent / "account-artifacts"))
    account = AccountStore(DB_PATH.parent / "account.sqlite3", artifact_root, retention_seconds_from_env())
    return account


def initialize():
    open_account()
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, game TEXT NOT NULL,
            duration_ms INTEGER NOT NULL, input_count INTEGER NOT NULL,
            keyboard_count INTEGER NOT NULL, controller_count INTEGER NOT NULL,
            mean_interval_ms REAL NOT NULL, interval_std_ms REAL NOT NULL,
            mean_hold_ms REAL NOT NULL, score INTEGER NOT NULL, source TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS feature_windows (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, device_id TEXT NOT NULL,
            session_id TEXT NOT NULL, sequence INTEGER NOT NULL, mode TEXT NOT NULL,
            window_json TEXT NOT NULL, UNIQUE(device_id, session_id, sequence)
        )""")


def _validated_feature_window_error(
    value: object,
    validators: dict[int, Draft202012Validator],
    version_error: str,
) -> str | None:
    version = value.get("schema_version") if isinstance(value, dict) else None
    validator = validators.get(version) if isinstance(version, int) else None
    if validator is None:
        return version_error
    error = next(validator.iter_errors(value), None)
    if error:
        return f"Feature window does not match schema v{version}: {error.message}"
    window = cast(dict[str, object], value)
    features = cast(dict[str, int | float], window["features"])
    mouse = cast(dict[str, int | float], window.get("mouse_features", {}))
    try:
        if not all(math.isfinite(number) for number in [*features.values(), *mouse.values()]):
            return "Feature values must be finite numbers"
        duration = cast(int, window["duration_ms"])
        if duration > 600_000:
            return "Window duration cannot exceed 10 minutes"
        if any(number > duration for name, number in [*features.items(), *mouse.items()] if name.endswith("_ms")):
            return "Timing features cannot exceed window duration"
        if features.get("presses_per_second", 0) > 50:
            return "Press rate is implausible"
    except OverflowError:
        return "Feature values must be finite numbers"
    return None


def feature_window_error(value: object) -> str | None:
    return _validated_feature_window_error(
        value, FEATURE_VALIDATORS, "Feature window schema_version must be 1 or 2",
    )


def agent_feature_window_error(value: object) -> str | None:
    return _validated_feature_window_error(
        value, AGENT_FEATURE_VALIDATORS, "Feature window schema_version must be 1, 2, 3, or 4",
    )


def started_at_error(window: dict[str, object], now: datetime) -> str | None:
    # The schema's RFC 3339 format check guarantees a UTC offset here.
    ended = datetime.fromisoformat(cast(str, window["started_at"])) +timedelta(milliseconds=cast(int, window["duration_ms"]))
    if ended > now + timedelta(minutes=5) or ended < now - timedelta(days=1):
        return "started_at is outside the accepted ingestion window"
    return None


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/arcade")
def arcade():
    return render_template("arcade.html")


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.get("/demo")
def demo():
    return render_template("demo.html")


@app.get("/devices")
def devices():
    return render_template("devices.html")


@app.get("/privacy")
def privacy():
    return render_template("privacy.html", sample_payload=json.dumps(SAMPLE_FEATURE_WINDOW, indent=2))


@app.get("/api/summary")
def summary():
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) n FROM samples").fetchone()["n"]
        recent = conn.execute("SELECT game, score, created_at, input_count FROM samples ORDER BY id DESC LIMIT 8").fetchall()
        games = conn.execute("SELECT game, COUNT(*) n FROM samples GROUP BY game").fetchall()
    return jsonify({"samples": total, "recent": [dict(x) for x in recent], "games": {x["game"]: x["n"] for x in games}})


@app.get("/api/feature-windows")
def feature_window_summary():
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) n FROM feature_windows").fetchone()["n"]
        sessions = conn.execute("SELECT COUNT(DISTINCT session_id) n FROM feature_windows").fetchone()["n"]
        modes = conn.execute("SELECT mode, COUNT(*) n FROM feature_windows GROUP BY mode").fetchall()
    return jsonify({"windows": total, "sessions": sessions, "modes": {row["mode"]: row["n"] for row in modes}})


@app.get("/api/feature-windows/export")
def export_feature_windows():
    with db() as conn:
        rows = conn.execute("SELECT window_json FROM feature_windows ORDER BY id").fetchall()
    payload = "".join(f"{row['window_json']}\n" for row in rows)
    response = app.response_class(payload, mimetype="application/x-ndjson")
    response.headers["Content-Disposition"] = "attachment; filename=feature-windows-v1.jsonl"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/feature-windows")
def save_feature_window():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Expected one feature-window JSON object"), 400
    error = feature_window_error(data) or started_at_error(data, datetime.now(timezone.utc))
    if error:
        return jsonify(error=error), 422
    with db() as conn:
        try:
            conn.execute(
                """INSERT INTO feature_windows
                (created_at, device_id, session_id, sequence, mode, window_json)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    data["device_id"],
                    data["session_id"],
                    data["sequence"],
                    data["mode"],
                    json.dumps(data, allow_nan=False, separators=(",", ":")),
                ),
            )
        except sqlite3.IntegrityError:
            return jsonify(error="This device/session/sequence window already exists"), 409
    return jsonify(ok=True), 201


@app.post("/api/samples")
def save_sample():
    data = request.get_json(silent=True) or {}
    # Whitelist numerical aggregates only. Raw input events, key labels, and text are rejected.
    required = ("game", "duration_ms", "input_count", "keyboard_count", "controller_count", "mean_interval_ms", "interval_std_ms", "mean_hold_ms", "score", "source")
    if any(k not in data for k in required):
        return jsonify(error="Missing aggregate fields"), 400
    if data["game"] not in {"aim", "tetris", "typing", "arena", "switch", "wiki"}:
        return jsonify(error="Unknown activity"), 400
    try:
        values = [int(data[k]) for k in ("duration_ms", "input_count", "keyboard_count", "controller_count", "score")]
        floats = [float(data[k]) for k in ("mean_interval_ms", "interval_std_ms", "mean_hold_ms")]
        if any(v < 0 for v in values + floats) or values[0] > 600_000 or values[1] > 100_000:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(error="Invalid aggregate values"), 400
    with db() as conn:
        conn.execute("INSERT INTO samples VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)", (
            datetime.now(timezone.utc).isoformat(), data["game"], *values[:4], *floats, values[4], str(data["source"])[:20]
        ))
    return jsonify(ok=True), 201


@app.delete("/api/samples")
def delete_samples():
    with db() as conn:
        conn.execute("DELETE FROM samples")
    return jsonify(ok=True)


def confirmation_rejected(phrase: str):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"confirmation"} or data.get("confirmation") != phrase:
        return jsonify(error=f"Type {phrase} to confirm this action"), 400
    return None


def current_account() -> AccountStore:
    return account if account is not None else open_account()


def erase_local_account() -> None:
    with db() as conn:
        conn.execute("DELETE FROM samples")
        conn.execute("DELETE FROM feature_windows")
    current_account().delete_user(LOCAL_USER_ID)


def purge_monitoring_data(now: datetime | None = None) -> dict[str, int]:
    moment = now or datetime.now(timezone.utc)
    store = current_account()
    removed = store.purge_monitoring(moment)
    cutoff = moment - timedelta(seconds=store.retention_seconds())
    with db() as conn:
        rows = conn.execute("SELECT id, created_at FROM feature_windows WHERE mode = 'monitoring'").fetchall()
        stale = [row["id"] for row in rows if datetime.fromisoformat(row["created_at"]) < cutoff]
        if stale:
            conn.executemany("DELETE FROM feature_windows WHERE id = ?", [(row_id,) for row_id in stale])
        removed["collector_windows"] = len(stale)
    return removed


@app.delete("/api/local-data")
@app.delete("/me/data")
def delete_local_data():
    rejected = confirmation_rejected("DELETE")
    if rejected:
        return rejected
    erase_local_account()
    return jsonify(ok=True)


@app.post("/api/enrollment/reset")
def reset_enrollment():
    rejected = confirmation_rejected("RESET")
    if rejected:
        return rejected
    with db() as conn:
        deleted = conn.execute("DELETE FROM feature_windows WHERE mode = 'enrollment'").rowcount
    deleted += current_account().reset_enrollment(LOCAL_USER_ID, LOCAL_DEVICE_ID)
    return jsonify(ok=True, deleted=deleted)


@app.post("/api/model/deactivate")
def deactivate_model():
    rejected = confirmation_rejected("DEACTIVATE")
    if rejected:
        return rejected
    stored = current_account().deactivate(LOCAL_USER_ID, LOCAL_DEVICE_ID)
    return jsonify(ok=True, active=False, stored=stored)


@app.get("/api/retention")
def get_retention():
    return jsonify(monitoring_retention_seconds=current_account().retention_seconds())


@app.put("/api/retention")
def put_retention():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"monitoring_retention_seconds"}:
        return jsonify(error="monitoring_retention_seconds must be a positive integer"), 422
    try:
        seconds = current_account().set_retention(data["monitoring_retention_seconds"])
    except ValueError as error:
        return jsonify(error=str(error)), 422
    return jsonify(monitoring_retention_seconds=seconds)


@app.post("/api/retention/purge")
def purge_retention():
    if request.get_json(silent=True) not in (None, {}):
        return jsonify(error="purge takes no fields"), 422
    return jsonify(purge_monitoring_data())


def onboarding_window_error(window: dict[str, object]) -> str | None:
    return agent_feature_window_error(window) or started_at_error(window, datetime.now(timezone.utc))


if hosted.configured():
    from pymongo import MongoClient

    ml_token = Path(os.environ["ML_TOKEN_FILE"]).read_text().strip()
    trainer, scorer, deleter, activator, comparer, resetter, deactivator = hosted.ml_client(
        os.environ["ML_SERVICE_URL"], ml_token,
    )
    hosted.register(
        app,
        MongoClient(os.environ["MONGODB_URI"], tz_aware=True)[os.environ["MONGODB_DB"]],
        trainer,
        scorer,
        onboarding_window_error,
        deleter,
        activator,
        comparer,
        resetter,
        deactivator,
    )
else:
    @app.get("/health")
    def health():
        return jsonify(state="ok")

    @app.get("/onboarding")
    def onboarding_unavailable():
        return render_template("auth-unavailable.html")


if __name__ == "__main__":
    initialize()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
