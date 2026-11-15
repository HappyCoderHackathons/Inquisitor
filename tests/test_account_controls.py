import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.account.store import AccountStore
from backend.ml.app import create_app
from scripts.generate_sample_windows import generate_windows


class AccountControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        token_file = directory / "token"
        token_file.write_text("test-only-" + "a" * 32, encoding="utf-8")
        self.data_dir = directory / "models"
        self.environment = patch.dict(os.environ, {
            "ML_TOKEN_FILE": str(token_file),
            "ML_DATA_DIR": str(self.data_dir),
            "INQUISITOR_MONITORING_RETENTION_SECONDS": "604800",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = create_app().test_client()
        self.headers = {
            "Authorization": "Bearer " + token_file.read_text(encoding="utf-8"),
            "X-User-ID": "synthetic-user",
            "X-Device-ID": "synthetic-device",
        }
        self.account = AccountStore(
            self.data_dir / "account.sqlite3", self.data_dir / "account-artifacts", 604800,
        )

    def plant_model(self, user_id: str, device_id: str) -> tuple[str, Path]:
        model_id = uuid4().hex
        path = self.data_dir / model_id
        path.mkdir()
        (path / "model.joblib").write_bytes(b"artifact")
        (path / "metadata.json").write_text(json.dumps({
            "model_id": model_id, "user_id": user_id, "device_id": device_id,
        }), encoding="utf-8")
        return model_id, path

    def window(
        self, *, mode: str = "enrollment", session_id: str = "synthetic-session",
        sequence: int = 0, device_id: str = "synthetic-device",
    ):
        value = generate_windows(49, 1)[0]
        value["mode"] = mode
        value["session_id"] = session_id
        value["sequence"] = sequence
        value["device_id"] = device_id
        return value

    def test_delete_me_removes_rows_and_artifact_files_for_that_account(self) -> None:
        model_id, artifact = self.plant_model("synthetic-user", "synthetic-device")
        other_id, other_artifact = self.plant_model("other-user", "other-device")
        self.assertEqual(self.client.put(
            f"/models/{model_id}/active", headers=self.headers,
        ).status_code, 200)
        other_headers = {**self.headers, "X-User-ID": "other-user", "X-Device-ID": "other-device"}
        self.assertEqual(self.client.put(f"/models/{other_id}/active", headers=other_headers).status_code, 200)
        self.assertEqual(self.client.post(
            "/windows", headers=self.headers, json={"window": self.window()},
        ).status_code, 201)
        self.assertEqual(self.client.post(
            "/windows", headers=self.headers, json={"window": self.window()},
        ).status_code, 409)
        monitoring = self.window(mode="monitoring", session_id="monitor", sequence=1)
        self.assertEqual(self.client.post(
            "/windows", headers=self.headers, json={"window": monitoring},
        ).status_code, 201)
        self.assertEqual(self.client.post(
            "/windows", headers=other_headers,
            json={"window": self.window(session_id="other", device_id="other-device")},
        ).status_code, 201)
        self.account.save_result("synthetic-user", "synthetic-device", {"model_id": model_id, "state": "UNUSUAL"})
        self.account.save_result("other-user", "other-device", {"model_id": other_id, "state": "NORMAL"})
        self.account.save_result("synthetic-user", "retired-device", {"model_id": model_id, "state": "UNUSUAL"})

        response = self.client.delete("/me/data", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["models"], [model_id])
        self.assertFalse(artifact.exists())
        self.assertEqual(self.client.get("/models/active", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get("/models/active", headers=other_headers).status_code, 200)
        self.assertEqual(self.account.counts("synthetic-user"), {
            "windows": 0, "enrollment": 0, "monitoring": 0, "inference_results": 0,
            "alerts": 0, "models": 0, "active_models": 0,
        })
        self.assertTrue((other_artifact / "model.joblib").is_file())
        self.assertEqual(self.account.counts("other-user")["windows"], 1)
        self.assertEqual(self.account.counts("other-user")["inference_results"], 1)
        self.assertEqual(self.account.counts("other-user")["alerts"], 0)
        published = Path(__file__).resolve().parents[1] / "backend/artifacts/labeled-overlap-v2.json"
        self.assertTrue(published.is_file())

    def test_reset_clears_enrollment_and_active_model_only(self) -> None:
        active_id, active_path = self.plant_model("synthetic-user", "synthetic-device")
        _inactive_id, inactive_path = self.plant_model("synthetic-user", "synthetic-device")
        self.assertEqual(self.client.put(f"/models/{active_id}/active", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post(
            "/windows", headers=self.headers, json={"window": self.window()},
        ).status_code, 201)
        self.assertEqual(self.client.post(
            "/windows", headers=self.headers,
            json={"window": self.window(mode="monitoring", session_id="monitor", sequence=2)},
        ).status_code, 201)
        other_device = {**self.headers, "X-Device-ID": "other-device"}
        kept_id, kept_path = self.plant_model("synthetic-user", "other-device")
        self.assertEqual(self.client.put(f"/models/{kept_id}/active", headers=other_device).status_code, 200)
        self.assertEqual(self.client.post(
            "/windows", headers=other_device,
            json={"window": self.window(session_id="device-two", device_id="other-device")},
        ).status_code, 201)

        response = self.client.post("/enrollment/reset", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["removed_model"], active_id)
        self.assertFalse(active_path.exists())
        self.assertTrue((inactive_path / "model.joblib").is_file())
        self.assertTrue((kept_path / "model.joblib").is_file())
        self.assertEqual(self.client.get("/models/active", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get("/models/active", headers=other_device).status_code, 200)
        self.assertEqual(self.account.counts("synthetic-user")["enrollment"], 1)
        self.assertEqual(self.account.counts("synthetic-user")["monitoring"], 1)

    def test_deactivate_keeps_the_model_file(self) -> None:
        model_id, artifact = self.plant_model("synthetic-user", "synthetic-device")
        self.client.put(f"/models/{model_id}/active", headers=self.headers)
        self.client.post("/windows", headers=self.headers, json={"window": self.window()})
        response = self.client.post("/models/active/deactivate", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"active": False})
        self.assertTrue((artifact / "model.joblib").is_file())
        self.assertEqual(self.client.get(f"/models/{model_id}", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/models/active", headers=self.headers).status_code, 404)
        self.assertEqual(self.account.counts("synthetic-user")["windows"], 1)

    def test_retention_period_is_configurable_and_purges_old_monitoring_rows(self) -> None:
        self.assertEqual(self.client.get("/retention", headers=self.headers).json, {
            "monitoring_retention_seconds": 604800,
        })
        rejected = self.client.put("/retention", headers=self.headers, json={"monitoring_retention_seconds": 0})
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(self.client.put(
            "/retention", headers=self.headers, json={"monitoring_retention_seconds": 60},
        ).status_code, 200)
        old = datetime.now(UTC) - timedelta(minutes=5)
        recent = datetime.now(UTC)
        self.assertTrue(self.account.save_window(
            "synthetic-user", "synthetic-device", self.window(mode="monitoring", session_id="old", sequence=1),
            created_at=old,
        ))
        self.assertTrue(self.account.save_window(
            "synthetic-user", "synthetic-device", self.window(), created_at=old,
        ))
        self.assertTrue(self.account.save_window(
            "synthetic-user", "synthetic-device",
            self.window(mode="monitoring", session_id="new", sequence=2), created_at=recent,
        ))
        self.account.save_result(
            "synthetic-user", "synthetic-device", {"model_id": "score", "state": "UNUSUAL"}, created_at=old,
        )
        self.account.save_result(
            "synthetic-user", "synthetic-device", {"model_id": "score", "state": "NORMAL"}, created_at=recent,
        )

        purged = self.client.post("/retention/purge", headers=self.headers)
        self.assertEqual(purged.status_code, 200, purged.json)
        self.assertEqual(purged.json["windows"], 1)
        self.assertEqual(purged.json["inference_results"], 1)
        self.assertEqual(purged.json["alerts"], 1)
        self.assertEqual(self.account.counts("synthetic-user")["monitoring"], 1)
        self.assertEqual(self.account.counts("synthetic-user")["enrollment"], 1)
        self.assertEqual(self.account.counts("synthetic-user")["inference_results"], 1)
        self.assertEqual(self.account.counts("synthetic-user")["alerts"], 0)

    def test_invalid_retention_env_refuses_to_start(self) -> None:
        with (
            patch.dict(os.environ, {"INQUISITOR_MONITORING_RETENTION_SECONDS": "0"}),
            self.assertRaises(RuntimeError),
        ):
            create_app()
