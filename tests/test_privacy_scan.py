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
        window = {
            "schema_version": 4,
            "device_id": "device",
            "session_id": "session",
            "sequence": 0,
            "mode": "monitoring",
            "started_at": "2000-01-01T00:00:00Z",
            "duration_ms": 30000,
            "features": {
                "key_count": 40,
                "hold_mean_ms": 100.0,
                "hold_std_ms": 20.0,
                "press_interval_mean_ms": 250.0,
                "press_interval_std_ms": 50.0,
                "hold_sample_count": 40,
                "mouse_sample_count": 10,
                "mouse_click_count": 2,
                "mouse_speed_mean_counts_s": 100.0,
                "mouse_speed_std_counts_s": 20.0,
                "mouse_click_interval_mean_ms": 300.0,
                "mouse_click_interval_std_ms": 20.0,
                "mouse_turn_mean_rad": 1.0,
                "mouse_turn_std_rad": 0.2,
                "mouse_path_straightness": 0.8,
                "text": "hello",
            },
        }
        findings = scan_value(window)
        self.assertTrue(any("allowlist" in finding.reason for finding in findings))

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