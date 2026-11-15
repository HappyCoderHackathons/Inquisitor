"""Scan JSON and log files for prohibited input content in agent payloads and audit output."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ml.model import validate_window

URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
SAFE_AUDIT_KEYS = frozenset({
    "device_count", "device_id", "event", "mode", "outcome", "reason",
    "schema_version", "sequence", "user_id",
})
# Frontend.hosted inserts into windows and monitoring_windows; _id is excluded by the capture.
SAFE_DOCUMENT_KEYS = frozenset({
    "created_at", "device_id", "game", "onboarding_id", "result", "sequence",
    "session_id", "usable", "user_id", "window",
})
SAFE_RESULT_KEYS = frozenset({"model_id", "state", "score", "threshold", "score_delta"})
CAPTURE_KEYS = frozenset({
    "enrollment_uploads", "monitoring_uploads", "persisted_documents", "audit_log_lines", "upload_request",
})
LIST_FIELDS = frozenset({
    "enrollment_uploads", "monitoring_uploads", "windows", "monitoring_windows", "audit_log_lines",
})


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    reason: str

    def format(self, source: str) -> str:
        return f"{source}: {self.path}: {self.reason}"


def _check_string(path: str, value: str, findings: list[Finding]) -> None:
    if URL_PATTERN.search(value):
        findings.append(Finding(path, "string contains a URL"))


def _walk(value: object, path: str, findings: list[Finding], field: str = "") -> None:
    if isinstance(value, str) and (field == "audit_log_lines" or (not field and value.lstrip().startswith(("{", "[")))):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            findings.append(Finding(path, "audit log line is not JSON"))
            return
    if field in LIST_FIELDS and not isinstance(value, Mapping):
        findings.append(Finding(path, "capture collection entry must be an object"))
        return
    if field in {"window", "upload_request", "enrollment_uploads", "monitoring_uploads"} or (
        not field and isinstance(value, Mapping) and ("features" in value or "schema_version" in value)
        and "event" not in value
    ):
        try:
            validate_window(value)
        except ValueError as exc:
            findings.append(Finding(path, f"invalid feature window: {exc}"))
        if isinstance(value, Mapping):
            for key, child in value.items():
                if isinstance(child, str):
                    _check_string(f"{path}.{key}", child, findings)
        return
    if isinstance(value, Mapping):
        if field == "audit_log_lines" or ("event" in value and "outcome" in value):
            allowed = SAFE_AUDIT_KEYS
        elif field in {"windows", "monitoring_windows"}:
            allowed = SAFE_DOCUMENT_KEYS
        elif field == "persisted_documents":
            allowed = frozenset({"windows", "monitoring_windows"})
        elif field == "result":
            allowed = SAFE_RESULT_KEYS
        elif not field:
            allowed = CAPTURE_KEYS if CAPTURE_KEYS.intersection(value) else SAFE_DOCUMENT_KEYS
        else:
            allowed = frozenset()
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key not in allowed:
                findings.append(Finding(child_path, "field is outside the allowlist"))
            if key in LIST_FIELDS and key in allowed and isinstance(child, list):
                for index, item in enumerate(child):
                    _walk(item, f"{child_path}[{index}]", findings, str(key))
            else:
                _walk(child, child_path, findings, str(key) if key in allowed else "unknown")
        return
    if isinstance(value, list):
        if not field and all(isinstance(item, Mapping) for item in value):
            for index, item in enumerate(value):
                _walk(item, f"{path}[{index}]", findings)
            return
        if any(isinstance(item, (dict, list, int, float)) for item in value):
            findings.append(Finding(path, "non-window structured or numeric array"))
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", findings, "unknown")
    elif isinstance(value, str):
        _check_string(path, value, findings)


def scan_value(value: object, *, source: str = "<memory>") -> list[Finding]:
    findings: list[Finding] = []
    _walk(value, "", findings)
    prefixed: list[Finding] = []
    for finding in findings:
        location = f"{source}:{finding.path}" if finding.path else source
        prefixed.append(Finding(location, finding.reason))
    return prefixed


def _load_file(path: Path) -> list[object]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if path.suffix == ".log":
        items: list[object] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            payload = line.split(" ", 3)[-1] if " " in line else line
            try:
                items.append(json.loads(payload))
            except json.JSONDecodeError:
                if "{" in line:
                    start = line.index("{")
                    items.append(json.loads(line[start:]))
                else:
                    raise ValueError(f"{path}:{line_number}: not JSON") from None
        return items
    loaded = json.loads(text)
    return [loaded]


def scan_paths(paths: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        values = _load_file(path)
        multi = len(values) > 1
        for index, value in enumerate(values):
            if path.suffix == ".jsonl" or multi:
                label = f"{path}[{index}]"
            else:
                label = str(path)
            findings.extend(scan_value(value, source=label))
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="JSON, JSONL, or JSON audit log files")
    args = parser.parse_args()
    findings = scan_paths(args.paths)
    if not findings:
        print("privacy scan: ok")
        return
    print("privacy scan: forbidden data detected", file=sys.stderr)
    for finding in findings:
        print(f"{finding.path}: {finding.reason}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()