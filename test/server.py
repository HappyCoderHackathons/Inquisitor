"""InputPrint Arcade: Flask host for opt-in, aggregate-only game telemetry."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).parent
DB_PATH = Path(os.environ.get("INPUTPRINT_DB", ROOT / "inputprint.sqlite3"))
app = Flask(__name__, static_folder="static", template_folder="templates")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, game TEXT NOT NULL,
            duration_ms INTEGER NOT NULL, input_count INTEGER NOT NULL,
            keyboard_count INTEGER NOT NULL, controller_count INTEGER NOT NULL,
            mean_interval_ms REAL NOT NULL, interval_std_ms REAL NOT NULL,
            mean_hold_ms REAL NOT NULL, score INTEGER NOT NULL, source TEXT NOT NULL
        )""")


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/summary")
def summary():
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) n FROM samples").fetchone()["n"]
        recent = conn.execute("SELECT game, score, created_at, input_count FROM samples ORDER BY id DESC LIMIT 8").fetchall()
        games = conn.execute("SELECT game, COUNT(*) n FROM samples GROUP BY game").fetchall()
    return jsonify({"samples": total, "recent": [dict(x) for x in recent], "games": {x["game"]: x["n"] for x in games}})


@app.post("/api/samples")
def save_sample():
    data = request.get_json(silent=True) or {}
    # Whitelist numerical aggregates only. Raw input events, key labels, and text are rejected.
    required = ("game", "duration_ms", "input_count", "keyboard_count", "controller_count", "mean_interval_ms", "interval_std_ms", "mean_hold_ms", "score", "source")
    if any(k not in data for k in required):
        return jsonify(error="Missing aggregate fields"), 400
    if data["game"] not in {"aim", "tetris", "typing", "wiki"}:
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


if __name__ == "__main__":
    initialize()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
