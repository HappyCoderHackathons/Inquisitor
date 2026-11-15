"""Check a capture from agent_link_test_server.py: every agent request and socket message must fit
the aggregate-only protocol. Anything outside these shapes fails."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ml.model import validate_window

SOCKET_KEYS = {"v", "type", "paused", "agent_version"}


def problems(record: dict[str, object]) -> list[str]:
    if record.get("kind") == "ws":
        message = json.loads(str(record["message"]))
        extra = set(message) - SOCKET_KEYS
        return [f"socket message has fields {sorted(extra)}"] if extra else []
    method, path, body = record["method"], str(record["path"]), str(record["body"])
    if method == "GET":
        return [] if not body else [f"GET {path} has a body"]
    data = json.loads(body) if body else {}
    if path == "/api/agent/pair":
        if set(data) != {"code", "name"} or not all(isinstance(data[key], str) for key in data):
            return [f"pair body has fields {sorted(data)}"]
        return []
    if path == "/api/agent/windows":
        try:
            validate_window(data)
        except ValueError as exc:
            return [f"upload is not a valid aggregate window: {exc}"]
        return []
    if path in ("/api/agent/heartbeat",):
        return [] if not data else [f"heartbeat body has fields {sorted(data)}"]
    return [f"unexpected agent request {method} {path}"]


def main() -> None:
    records = [json.loads(line) for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line]
    found = [f"{index}: {problem}" for index, record in enumerate(records) for problem in problems(record)]
    kinds = {kind: sum(1 for item in records if (item.get("kind"), item.get("path")) == kind)
             for kind in {(item.get("kind"), item.get("path")) for item in records}}
    for (kind, path), count in sorted(kinds.items(), key=str):
        print(f"{kind} {path or ''}: {count}")
    if found:
        print("agent traffic check: failed", *found, sep="\n", file=sys.stderr)
        raise SystemExit(1)
    print(f"agent traffic check: ok ({len(records)} records)")


if __name__ == "__main__":
    main()
