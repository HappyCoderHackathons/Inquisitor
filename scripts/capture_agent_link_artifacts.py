"""Exercise the agent link test server in-process and write captured payloads for privacy scans."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "Frontend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(FRONTEND))

import hosted

from scripts.agent_link_test_server import (
    PAIRING_CODE,
    create_app,
    no_session,
)


def monitoring_window(device_id: str, session_id: str, sequence: int) -> dict[str, object]:
    window = json.loads((ROOT / "shared" / "fixtures" / "tray-capture.v4.json").read_text(encoding="utf-8"))[0]
    window.update(
        device_id=device_id, session_id=session_id, sequence=sequence,
        started_at=(datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
    )
    return window


class _CollectLogs(logging.Handler):
    def __init__(self, sink: list[str]) -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record.getMessage())


def capture(output: Path) -> dict[str, object]:
    audit_lines: list[str] = []

    with patch("hosted.requests.request", side_effect=no_session):
        app = create_app()
        logger = logging.getLogger(hosted.__name__)
        handler = _CollectLogs(audit_lines)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        client = app.test_client()
        paired = client.post("/api/agent/pair", json={"code": PAIRING_CODE, "name": "privacy-capture"})
        assert paired.status_code == 201, paired.get_data(as_text=True)
        body = paired.get_json()
        device_id, token = body["device_id"], body["token"]
        client.post(f"/__test/ready/{device_id}")
        window = monitoring_window(device_id, "privacy-capture-session", 0)
        headers = {"Authorization": f"Bearer {token}"}
        uploaded = client.post("/api/agent/windows", headers=headers, json=window)
        assert uploaded.status_code == 201, uploaded.get_data(as_text=True)
        artifacts = client.get("/__test/artifacts").get_json()
        logger.removeHandler(handler)
        artifacts["audit_log_lines"] = [json.loads(line) for line in audit_lines]
        artifacts["upload_request"] = window
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(artifacts, indent=2, default=str), encoding="utf-8")
        return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    capture(args.output)
    print(f"captured artifacts written to {args.output}")


if __name__ == "__main__":
    main()