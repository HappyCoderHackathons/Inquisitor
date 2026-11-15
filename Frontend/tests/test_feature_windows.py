import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import server


def valid_window():
    return {
        "schema_version": 1,
        "device_id": "local-device",
        "session_id": "local-session",
        "sequence": 0,
        "mode": "enrollment",
        "started_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "duration_ms": 30_000,
        "features": {
            "key_count": 40,
            "presses_per_second": 2.0,
            "hold_mean_ms": 100.0,
            "hold_std_ms": 20.0,
            "press_interval_mean_ms": 500.0,
            "press_interval_std_ms": 50.0,
            "press_interval_p90_ms": 550.0,
            "pause_count": 0,
            "pause_fraction": 0.0,
            "burst_count": 1,
            "press_overlap_fraction": 0.2,
        },
    }


class FeatureWindowApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.previous_database_path = server.DB_PATH
        server.DB_PATH = Path(self.temporary_directory.name) / "test.sqlite3"
        server.initialize()
        self.client = server.app.test_client()

    def tearDown(self):
        server.DB_PATH = self.previous_database_path
        self.temporary_directory.cleanup()

    def test_saves_and_exports_shared_schema_window_as_jsonl(self):
        window = valid_window()
        response = self.client.post("/api/feature-windows", json=window)

        self.assertEqual(response.status_code, 201)
        summary = self.client.get("/api/feature-windows").get_json()
        self.assertEqual(summary, {"windows": 1, "sessions": 1, "modes": {"enrollment": 1}})
        export = self.client.get("/api/feature-windows/export")
        self.assertEqual(export.mimetype, "application/x-ndjson")
        self.assertEqual(json.loads(export.get_data(as_text=True)), window)

    def test_rejects_non_contract_fields_and_duplicate_sequences(self):
        window = valid_window()
        invalid = {**window, "key_events": ["a"]}
        self.assertEqual(self.client.post("/api/feature-windows", json=invalid).status_code, 422)

        self.assertEqual(self.client.post("/api/feature-windows", json=window).status_code, 201)
        duplicate = self.client.post("/api/feature-windows", json=window)
        self.assertEqual(duplicate.status_code, 409)

    def test_rejects_non_finite_feature_values(self):
        window = valid_window()
        window["features"]["hold_mean_ms"] = float("nan")
        self.assertEqual(self.client.post("/api/feature-windows", json=window).status_code, 422)

    def test_rejects_forged_payloads_without_persisting(self):
        cases = {
            "wrong type": ("key_count", "40"),
            "extra feature": ("key_name", 1),
            "out of range": ("pause_fraction", 1.5),
            "implausible rate": ("presses_per_second", 500),
        }
        for name, (field, value) in cases.items():
            window = valid_window()
            window["features"][field] = value
            with self.subTest(name):
                self.assertEqual(self.client.post("/api/feature-windows", json=window).status_code, 422)
        for name, started in (("future", timedelta(hours=1)), ("stale replay", -timedelta(days=2))):
            window = valid_window()
            window["started_at"] = (datetime.now(timezone.utc) + started).isoformat()
            with self.subTest(name):
                self.assertEqual(self.client.post("/api/feature-windows", json=window).status_code, 422)
        too_long = copy.deepcopy(valid_window())
        too_long["duration_ms"] = 600_001
        self.assertEqual(self.client.post("/api/feature-windows", json=too_long).status_code, 422)
        self.assertEqual(self.client.get("/api/feature-windows").get_json()["windows"], 0)

    def test_accepts_mouse_window_v2_and_rejects_pointer_positions(self):
        mouse = {
            "move_count": 50, "speed_mean_px_s": 600.0, "speed_std_px_s": 200.0, "acceleration_mean_px_s2": 9000.0,
            "acceleration_std_px_s2": 4000.0, "turn_angle_mean_rad": 0.4, "click_count": 3,
            "click_interval_mean_ms": 2000.0, "click_interval_std_ms": 500.0,
        }
        window = {**valid_window(), "schema_version": 2, "mouse_features": mouse}
        self.assertEqual(self.client.post("/api/feature-windows", json=window).status_code, 201)
        forged = {**window, "sequence": 1, "mouse_features": {**mouse, "x": 100, "y": 200}}
        self.assertEqual(self.client.post("/api/feature-windows", json=forged).status_code, 422)
        self.assertEqual(self.client.post("/api/feature-windows", json={**window, "schema_version": 3}).status_code, 422)

    def test_local_data_delete_clears_feature_windows(self):
        self.client.post("/api/feature-windows", json=valid_window())
        response = self.client.delete("/api/local-data")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/feature-windows").get_json()["windows"], 0)


if __name__ == "__main__":
    unittest.main()
