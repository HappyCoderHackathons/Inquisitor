import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import mongomock

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
        self.previous_database = server._arcade_database
        self.previous_database_path = server.DB_PATH
        server.set_arcade_database(mongomock.MongoClient(tz_aware=True).db)
        server.DB_PATH = Path(self.temporary_directory.name) / "inputprint.sqlite3"
        server.account = None
        server.initialize()
        self.client = server.app.test_client()

    def tearDown(self):
        server.set_arcade_database(self.previous_database)
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

    def stored_counts(self):
        database = server.arcade_database()
        samples = database.arcade_samples.count_documents({})
        windows = database.arcade_feature_windows.count_documents({})
        enrollment = database.arcade_feature_windows.count_documents({"mode": "enrollment"})
        monitoring = database.arcade_feature_windows.count_documents({"mode": "monitoring"})
        return samples, windows, enrollment, monitoring

    def save_sample_and_windows(self):
        sample = {
            "game": "typing", "duration_ms": 1000, "input_count": 10, "keyboard_count": 10,
            "controller_count": 0, "mean_interval_ms": 100.0, "interval_std_ms": 10.0,
            "mean_hold_ms": 80.0, "score": 1, "source": "test",
        }
        monitoring = {**valid_window(), "session_id": "monitor-session", "sequence": 1, "mode": "monitoring"}
        self.assertEqual(self.client.post("/api/samples", json=sample).status_code, 201)
        self.assertEqual(self.client.post("/api/feature-windows", json=valid_window()).status_code, 201)
        self.assertEqual(self.client.post("/api/feature-windows", json=monitoring).status_code, 201)

    def account(self):
        self.assertIsNotNone(server.account)
        return server.account

    def test_local_data_delete_leaves_no_rows(self):
        self.save_sample_and_windows()
        store = self.account()
        model_id = uuid4().hex
        other_id = uuid4().hex
        artifact = store.add_model(server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID, model_id, active=True)
        (artifact / "model.bin").write_bytes(b"weights")
        other = store.add_model("other-user", "other-device", other_id, active=True)
        (other / "model.bin").write_bytes(b"keep")
        window = valid_window()
        self.assertTrue(store.save_window(server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID, window))
        self.assertTrue(store.save_window(
            "other-user", "other-device", {**window, "device_id": "other-device", "session_id": "other-session"},
        ))
        store.save_result(server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID, {"model_id": model_id, "state": "UNUSUAL"})
        store.save_result("other-user", "other-device", {"model_id": other_id, "state": "UNUSUAL"})
        self.assertEqual(self.stored_counts(), (1, 2, 1, 1))

        rejected = self.client.delete("/me/data", json={"confirmation": "delete"})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.stored_counts(), (1, 2, 1, 1))
        self.assertTrue((artifact / "model.bin").is_file())

        response = self.client.delete("/me/data", json={"confirmation": "DELETE"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored_counts(), (0, 0, 0, 0))
        self.assertEqual(store.counts(server.LOCAL_USER_ID), {
            "windows": 0, "enrollment": 0, "monitoring": 0, "inference_results": 0,
            "alerts": 0, "models": 0, "active_models": 0,
        })
        self.assertFalse(artifact.exists())
        self.assertTrue((other / "model.bin").is_file())
        self.assertEqual(store.counts("other-user")["windows"], 1)
        self.assertEqual(store.counts("other-user")["alerts"], 1)
        self.assertEqual(store.counts("other-user")["models"], 1)
        self.assertEqual(self.client.get("/api/feature-windows").get_json()["windows"], 0)
        self.assertEqual(self.client.get("/api/feature-windows/export").get_data(), b"")
        self.assertEqual(self.client.get("/api/summary").get_json()["samples"], 0)

    def test_reset_removes_enrollment_and_active_model(self):
        self.save_sample_and_windows()
        store = self.account()
        inactive = store.add_model(server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID, uuid4().hex, active=False)
        active = store.add_model(server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID, uuid4().hex, active=True)
        (inactive / "model.bin").write_bytes(b"old")
        (active / "model.bin").write_bytes(b"current")
        monitoring = {**valid_window(), "session_id": "account-monitor", "sequence": 3, "mode": "monitoring"}
        self.assertTrue(store.save_window(server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID, valid_window()))
        self.assertTrue(store.save_window(server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID, monitoring))
        rejected = self.client.post("/api/enrollment/reset", json={})
        self.assertEqual(rejected.status_code, 400)
        self.assertTrue((active / "model.bin").is_file())

        response = self.client.post("/api/enrollment/reset", json={"confirmation": "RESET"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["deleted"], 2)
        self.assertEqual(self.stored_counts(), (1, 1, 0, 1))
        self.assertFalse(active.exists())
        self.assertTrue((inactive / "model.bin").is_file())
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["enrollment"], 0)
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["monitoring"], 1)
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["active_models"], 0)
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["models"], 1)

    def test_deactivate_keeps_rows_and_model_files(self):
        self.save_sample_and_windows()
        store = self.account()
        rejected = self.client.post("/api/model/deactivate")
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.stored_counts(), (1, 2, 1, 1))

        artifact = store.add_model(server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID, uuid4().hex, active=True)
        (artifact / "model.bin").write_bytes(b"weights")
        response = self.client.post("/api/model/deactivate", json={"confirmation": "DEACTIVATE"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "active": False, "stored": True})
        self.assertTrue((artifact / "model.bin").is_file())
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["models"], 1)
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["active_models"], 0)
        self.assertEqual(self.stored_counts(), (1, 2, 1, 1))

    def test_retention_is_configurable_and_purges_old_monitoring_data(self):
        self.save_sample_and_windows()
        store = self.account()
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        recent = datetime.now(timezone.utc)
        server.arcade_database().arcade_feature_windows.update_many(
            {"mode": "monitoring"},
            {"$set": {"created_at": old.isoformat()}},
        )
        self.assertTrue(store.save_window(
            server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID,
            {**valid_window(), "session_id": "old-monitor", "sequence": 4, "mode": "monitoring"}, created_at=old,
        ))
        self.assertTrue(store.save_window(
            server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID, valid_window(), created_at=old,
        ))
        self.assertTrue(store.save_window(
            server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID,
            {**valid_window(), "session_id": "new-monitor", "sequence": 5, "mode": "monitoring"}, created_at=recent,
        ))
        store.save_result(
            server.LOCAL_USER_ID, server.LOCAL_DEVICE_ID,
            {"model_id": "score", "state": "UNUSUAL"}, created_at=old,
        )
        self.assertEqual(self.client.put("/api/retention", json={"monitoring_retention_seconds": 0}).status_code, 422)
        self.assertEqual(
            self.client.put("/api/retention", json={"monitoring_retention_seconds": 3600}).status_code, 200,
        )
        self.assertEqual(self.client.get("/api/retention").get_json(), {"monitoring_retention_seconds": 3600})

        purged = self.client.post("/api/retention/purge")
        self.assertEqual(purged.status_code, 200)
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["monitoring"], 1)
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["enrollment"], 1)
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["inference_results"], 0)
        self.assertEqual(store.counts(server.LOCAL_USER_ID)["alerts"], 0)
        self.assertEqual(self.stored_counts()[2:], (1, 0))


if __name__ == "__main__":
    unittest.main()