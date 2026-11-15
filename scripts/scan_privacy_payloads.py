"""Scan JSON and log files for prohibited input content in agent payloads and audit output."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "shared"

URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)

FORBIDDEN_KEY = re.compile(
    r"(?i)(^|_)(vk_code|virtual_key|key_code|scan_code|key_name|key_char|unicode_char|"
    r"typed_text|keystroke|clipboard|"
    r"screen_x|screen_y|pointer_x|pointer_y|client_x|client_y|cursor_x|cursor_y|"
    r"absolute_x|absolute_y|click_x|click_y|pos_x|pos_y|"
    r"window_title|hwnd|click_target|"
    r"raw_events|key_events|mouse_events)(_|$)"
    r"|^(text|typed|character|url|href|events)$"
)

SAFE_AUDIT_KEYS = frozenset(
    {
        "device_count",
        "device_id",
        "event",
        "mode",
        "outcome",
        "reason",
        "schema_version",
        "sequence",
        "user_id",
    }
)

EVENT_ROW_HINTS = frozenset({"io_type", "vk", "scan_code", "monotonic_s", "delta_x", "delta_y"})


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    reason: str

    def format(self, source: str) -> str:
        return f"{source}: {self.path}: {self.reason}"


def _schema_property_paths(schema_version: int) -> frozenset[str]:
    path = SCHEMA_DIR / f"feature-window-v{schema_version}.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    paths: set[str] = set()

    def walk(properties: Mapping[str, Any], prefix: str) -> None:
        for name, spec in properties.items():
            current = f"{prefix}.{name}" if prefix else name
            paths.add(current)
            if spec.get("type") == "object" and isinstance(spec.get("properties"), Mapping):
                walk(spec["properties"], current)

    root = schema.get("properties")
    if isinstance(root, Mapping):
        walk(root, "")
    return frozenset(paths)


FEATURE_WINDOW_ALLOWLIST: dict[int, frozenset[str]] = {
    version: _schema_property_paths(version) for version in (1, 2, 3, 4)
}


def _is_feature_window(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    features = value.get("features")
    return (
        isinstance(value.get("schema_version"), int)
        and isinstance(value.get("device_id"), str)
        and isinstance(value.get("session_id"), str)
        and isinstance(features, Mapping)
    )


def _relative_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _audit_record(value: Mapping[str, Any]) -> bool:
    return isinstance(value.get("event"), str) and isinstance(value.get("outcome"), str)


def _check_string(path: str, value: str, findings: list[Finding]) -> None:
    if URL_PATTERN.search(value):
        findings.append(Finding(path, "string contains a URL"))


def _check_event_stream(path: str, value: Sequence[object], findings: list[Finding]) -> None:
    if len(value) < 2:
        return
    hits = 0
    for item in value[:8]:
        if not isinstance(item, Mapping):
            continue
        if len(EVENT_ROW_HINTS.intersection(item)) >= 2:
            hits += 1
    if hits >= 2:
        findings.append(Finding(path, "ordered raw input event list"))


def _check_feature_window(path: str, window: Mapping[str, Any], findings: list[Finding]) -> None:
    version = window["schema_version"]
    allowed = FEATURE_WINDOW_ALLOWLIST.get(version)
    if allowed is None:
        findings.append(Finding(path, f"unsupported feature window schema_version {version}"))
        return

    def walk(obj: Mapping[str, Any], schema_prefix: str, report_prefix: str) -> None:
        for key, child in obj.items():
            schema_path = _relative_path(schema_prefix, str(key))
            report_path = _relative_path(report_prefix, str(key))
            if schema_path not in allowed:
                findings.append(Finding(report_path, "field is outside the schema allowlist"))
            if isinstance(child, Mapping):
                walk(child, schema_path, report_path)
            elif isinstance(child, str):
                _check_string(report_path, child, findings)

    walk(window, "", path)


def _walk(value: Any, path: str, findings: list[Finding]) -> None:
    if isinstance(value, Mapping):
        if _is_feature_window(value):
            _check_feature_window(path, value, findings)
            return
        if _audit_record(value):
            for key in value:
                if key not in SAFE_AUDIT_KEYS:
                    findings.append(
                        Finding(_relative_path(path, str(key)), "unexpected audit log field")
                    )
            for key, child in value.items():
                child_path = _relative_path(path, str(key))
                if isinstance(child, str):
                    _check_string(child_path, child, findings)
            return

        for key, child in value.items():
            key_path = _relative_path(path, str(key))
            if FORBIDDEN_KEY.search(str(key)):
                findings.append(Finding(key_path, "forbidden field name"))
            if key == "window" and _is_feature_window(child):
                _check_feature_window(key_path, child, findings)
                continue
            _walk(child, key_path, findings)
        return

    if isinstance(value, str):
        _check_string(path, value, findings)
        return
    if isinstance(value, list):
        _check_event_stream(path, value, findings)
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", findings)


def scan_value(value: Any, *, source: str = "<memory>") -> list[Finding]:
    findings: list[Finding] = []
    _walk(value, "", findings)
    prefixed: list[Finding] = []
    for finding in findings:
        location = f"{source}:{finding.path}" if finding.path else source
        prefixed.append(Finding(location, finding.reason))
    return prefixed


def _load_file(path: Path) -> list[Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if path.suffix == ".log":
        items: list[Any] = []
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
    if isinstance(loaded, list):
        return loaded
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