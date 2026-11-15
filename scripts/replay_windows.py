"""Replay recorded aggregate windows through hosted monitoring ingestion. Output is labeled replay, not live."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

LABEL = "REPLAY (recorded windows, not live input)"


def replay_window(window: dict[str, object], device_id: str, session_id: str, sequence: int,
                  now: datetime) -> dict[str, object]:
    duration = cast(int, window["duration_ms"])
    # Restamp so the window ends now and passes the ingestion freshness check.
    started = (now - timedelta(milliseconds=duration)).isoformat().replace("+00:00", "Z")
    return {**window, "device_id": device_id, "session_id": session_id, "sequence": sequence,
            "mode": "monitoring", "started_at": started}


def post(url: str, token: str, window: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url.rstrip("/") + "/api/agent/windows", data=json.dumps(window).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("windows", type=Path, help="aggregate-only JSONL windows from one consenting session")
    parser.add_argument("--url", required=True, help="hosted app base URL")
    parser.add_argument("--device-id", required=True, help="paired device ID that owns INQUISITOR_DEVICE_TOKEN")
    parser.add_argument("--speed", type=float, default=1.0, help="playback multiplier; 1 is real time")
    args = parser.parse_args()
    token = os.environ.get("INQUISITOR_DEVICE_TOKEN", "")
    if not token:
        parser.error("set INQUISITOR_DEVICE_TOKEN to the paired device token")
    if not args.speed > 0:
        parser.error("--speed must be positive")
    windows = [json.loads(line) for line in args.windows.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not windows:
        parser.error("no windows to replay")
    if any(window.get("schema_version") != 4 for window in windows):
        # The agent endpoint accepts schema v4 only; tray_csv.py converts a tray capture to v4.
        parser.error("every window must be schema v4; convert tray captures with backend.ml.tray_csv")
    session_id = f"replay-{uuid4().hex[:12]}"
    print(f"{LABEL}: session {session_id}, {len(windows)} windows at {args.speed}x", flush=True)
    for sequence, window in enumerate(windows):
        try:
            result = post(args.url, token, replay_window(window, args.device_id, session_id, sequence,
                                                         datetime.now(UTC)))
        except urllib.error.HTTPError as error:
            message = json.loads(error.read() or b"{}").get("error", error.reason)
            sys.exit(f"{LABEL}: window {sequence} rejected with {error.code}: {message}")
        except (urllib.error.URLError, ValueError) as error:
            sys.exit(f"{LABEL}: window {sequence} failed: {error}")
        print(f"{LABEL} {sequence + 1}/{len(windows)}: {result.get('state')} score={result.get('score')}",
              flush=True)
        if sequence < len(windows) - 1:
            time.sleep(cast(int, window["duration_ms"]) / 1000 / args.speed)


if __name__ == "__main__":
    main()
