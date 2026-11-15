import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.scan_privacy_payloads import scan_paths, scan_value

ROOT = Path(__file__).resolve().parents[1]


class PrivacyScanTests(unittest.TestCase):
    def test_allowlisted_fixture_windows_pass(self) -> None:
        paths = [
            ROOT / "shared" / "fixtures" / "tray-capture.v4.json",
            ROOT / "sample-data" / "windows.jsonl",
        ]
        self.assertEqual(scan_paths(paths), [])

    def test_forbidden_field_names_are_rejected(self) -> None:
        payload = {
            "device_id": "device",
            "event": "agent_window_ingestion",
            "outcome": "accepted",
            "window_title": "Secrets",
        }
        findings = scan_value(payload)
        self.assertTrue(any("window_title" in finding.path for finding in findings))

    def test_audit_records_stay_metadata_only(self) -> None:
        audit = {
            "device_id": "device-1",
            "event": "agent_window_ingestion",
            "mode": "monitoring",
            "outcome": "accepted",
            "schema_version": 4,
            "sequence": 2,
        }
        self.assertEqual(scan_value(audit), [])

    def test_feature_window_extra_fields_fail(self) -> None:
        window = self.window()
        window["features"]["text"] = "hello"
        findings = scan_value(window)
        self.assertTrue(any("invalid feature window" in finding.reason for finding in findings))

    def window(self) -> dict[str, object]:
        return json.loads((ROOT / "shared" / "fixtures" / "tray-capture.v4.json").read_text())[0]

    def test_persisted_documents_reject_unlisted_keys(self) -> None:
        for key, value in {
            "keys": [65], "vk": 65, "x": 10, "y": 20, "title": "Secrets", "key_codes": [65, 66],
        }.items():
            with self.subTest(key=key):
                document = {"window": self.window(), key: value}
                findings = scan_value({"persisted_documents": {"monitoring_windows": [document]}})
                self.assertTrue(any(
                    finding.path.endswith(f".{key}") and finding.reason == "field is outside the allowlist"
                    for finding in findings
                ), findings)

    def test_feature_values_require_valid_numeric_types(self) -> None:
        for key, value in {"key_count": "65", "hold_mean_ms": [65, 66]}.items():
            with self.subTest(key=key):
                window = self.window()
                window["features"][key] = value
                findings = scan_value({"window": window})
                self.assertTrue(any("invalid feature window" in finding.reason for finding in findings))
        for window in ({"schema_version": 4, "features": []}, {"features": {}}):
            with self.subTest(window=window):
                self.assertTrue(scan_value({"window": window}))

    def test_audit_strings_and_nested_values_are_checked(self) -> None:
        audit = {"event": "agent_window_ingestion", "outcome": "accepted"}
        for extra in ({"window_title": "Secrets"}, {"reason": {"key_code": 65}}):
            with self.subTest(extra=extra):
                record = audit | extra
                self.assertTrue(scan_value(json.dumps(record)))
                for value in (record, json.dumps(record)):
                    findings = scan_value({"audit_log_lines": [value]})
                    self.assertTrue(any("allowlist" in finding.reason for finding in findings), findings)
        self.assertEqual(scan_value({"audit_log_lines": [json.dumps(audit)]}), [])

    def test_persisted_enrollment_and_monitoring_metadata_pass(self) -> None:
        document = {
            "created_at": "2026-01-01T00:00:00Z", "device_id": "device", "user_id": "user",
            "session_id": "session", "sequence": 0, "window": self.window(),
        }
        capture = {"persisted_documents": {
            "windows": [document | {"onboarding_id": "onboarding", "game": "test", "usable": True}],
            "monitoring_windows": [document | {"result": {
                "model_id": "model", "state": "NORMAL", "score": -0.4, "threshold": 0.1, "score_delta": -0.5,
            }}],
        }}
        self.assertEqual(scan_value(capture), [])

    def test_non_window_arrays_are_rejected(self) -> None:
        for value in ([{"a": 1}], [[1, 2]], [65, 66], ["safe", {"a": 1}]):
            with self.subTest(value=value):
                self.assertTrue(scan_value(value))
                findings = scan_value({"result": {"score": value}})
                self.assertTrue(any("array" in finding.reason for finding in findings), findings)

    def test_cli_exits_nonzero_on_violation(self) -> None:
        with self.subTest("clean"):
            clean = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "scan_privacy_payloads.py"),
                    str(ROOT / "sample-data" / "windows.jsonl"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertIn("ok", clean.stdout)
        with self.subTest("dirty"), tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"key_code": 65}, handle)
            handle.flush()
            dirty = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "scan_privacy_payloads.py"), handle.name],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(dirty.returncode, 1)
            self.assertIn("privacy scan: forbidden data detected", dirty.stderr)
            self.assertIn(f"{handle.name}:key_code: field is outside the allowlist", dirty.stderr)
