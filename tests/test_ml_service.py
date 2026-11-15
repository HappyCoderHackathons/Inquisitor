import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.ml.app import create_app
from backend.ml.model import ModelStore, validate_window
from backend.ml.published import PublishedModel
from scripts.generate_sample_windows import generate_windows


class ModelServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        token_file = directory / "token"
        token_file.write_text("test-only-" + "a" * 32, encoding="utf-8")
        self.environment = patch.dict(os.environ, {
            "ML_TOKEN_FILE": str(token_file),
            "ML_DATA_DIR": str(directory / "models"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = create_app().test_client()
        self.headers = {
            "Authorization": "Bearer " + token_file.read_text(encoding="utf-8"),
            "X-User-ID": "synthetic-user",
            "X-Device-ID": "synthetic-device",
        }

    def test_authentication_and_health(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 401)
        self.assertEqual(self.client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.client.get("/health", headers=self.headers).json, {
            "state": "ok", "database": "available", "model_store": "available",
        })
        with (
            patch.dict(os.environ, {"ML_TOKEN_FILE": str(Path(self.temporary.name) / "missing")}),
            self.assertRaises(FileNotFoundError),
        ):
            create_app()

    def test_health_reports_each_dependency_failure(self) -> None:
        with patch("backend.ml.app.AccountStore.check_available", side_effect=sqlite3.OperationalError("offline")):
            response = create_app().test_client().get("/health", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {
            "state": "degraded", "database": "unavailable", "model_store": "available",
        })

        with patch("backend.ml.app.ModelStore.check_available", side_effect=OSError("offline")):
            response = create_app().test_client().get("/health", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {
            "state": "degraded", "database": "available", "model_store": "unavailable",
        })

    def test_shared_schema_fixtures(self) -> None:
        for version in (1, 2):
            path = Path(__file__).resolve().parents[1] / f"shared/fixtures/feature-window-v{version}.json"
            fixtures = json.loads(path.read_text(encoding="utf-8"))
            for name, window in fixtures["valid"].items():
                with self.subTest(version=version, name=name):
                    validate_window(window)
            for name, window in fixtures["invalid"].items():
                with self.subTest(version=version, name=name), self.assertRaises(ValueError):
                    validate_window(window)
        mouse = json.loads((Path(__file__).resolve().parents[1] / "shared/fixtures/feature-window-v2.json").read_text(
            encoding="utf-8"))["valid"]["enrollment"]
        for field, value in (("click_interval_mean_ms", 90_000), ("speed_mean_px_s", float("inf"))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_window({**mouse, "mouse_features": {**mouse["mouse_features"], field: value}})

    def test_nonfinite_and_impossible_timing(self) -> None:
        for field, value in (
            ("presses_per_second", float("nan")), ("hold_mean_ms", 90_000), ("key_count", 10 ** 400),
            ("presses_per_second", 51), ("key_count", "40"),
        ):
            window = generate_windows(49, 1)[0]
            window["features"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_window(window)

    def test_training_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {"ML_ALLOW_TRAINING": "0"}):
            store = ModelStore(Path(self.temporary.name) / "disabled")
            with self.assertRaises(RuntimeError):
                store.train([], "user", "device", "validation")

    def test_published_api_auth_ownership_and_score_contract(self) -> None:
        artifact = Path(__file__).resolve().parents[1] / "backend/artifacts/labeled-overlap-v2.json"
        with patch.dict(os.environ, {"ML_PUBLISHED_MODEL_FILE": str(artifact),
                                    "ML_PUBLISHED_USER_ID": "synthetic-user", "ML_PUBLISHED_DEVICE_ID": "synthetic-device"}):
            client = create_app().test_client()
        self.assertEqual(client.get("/model").status_code, 401)
        self.assertEqual(client.get("/model", headers=self.headers).json["model_version"], "labeled-overlap-v2")
        unusual = generate_windows(49, 1)[0]
        unusual["features"]["press_overlap_fraction"] = 0.9
        explanation = client.post("/score", headers=self.headers, json={"window": unusual}).json["explanation"]
        self.assertIsNotNone(explanation["features"][0]["baseline_z"])
        window = generate_windows(49, 1)[0]
        response = client.post("/score", headers=self.headers, json={"window": window})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertAlmostEqual(response.json["score_delta"], response.json["score"] - response.json["threshold"])
        with_mouse = {**window, "schema_version": 2, "mouse_features": {
            "move_count": 0, "speed_mean_px_s": 0, "speed_std_px_s": 0, "acceleration_mean_px_s2": 0,
            "acceleration_std_px_s2": 0, "turn_angle_mean_rad": 0, "click_count": 0,
            "click_interval_mean_ms": 0, "click_interval_std_ms": 0}}
        self.assertEqual(client.post("/score", headers=self.headers, json={"window": with_mouse}).json, response.json)
        self.assertEqual(client.post("/score", headers={**self.headers, "X-User-ID": "another-user"}, json={"window": window}).status_code, 403)
        window["device_id"] = "other-device"
        self.assertEqual(client.post("/score", headers=self.headers, json={"window": window}).status_code, 403)
        window["device_id"] = "synthetic-device"
        window["features"]["key_count"] = 1
        self.assertEqual(client.post("/score", headers=self.headers, json={"window": window}).json["state"], "INSUFFICIENT_DATA")

    def test_published_unusual_window_is_explained(self) -> None:
        artifact = Path(__file__).resolve().parents[1] / "backend/artifacts/labeled-overlap-v1.json"
        window = generate_windows(49, 1)[0]
        window["features"]["press_overlap_fraction"] = 0.9
        result = PublishedModel(artifact).score(window, "synthetic-device")
        self.assertEqual(result["state"], "UNUSUAL")
        self.assertEqual(result["explanation"]["features"][0]["feature"], "press_overlap_fraction")
        self.assertIn("Key overlap contributed most", result["explanation"]["summary"])
        value = json.loads(artifact.read_text(encoding="utf-8"))
        value["genuine_baseline"] = {"median": [0.3, 0.1], "scale": [0.1, 0.2]}
        with_baseline = Path(self.temporary.name) / "baseline.json"
        with_baseline.write_text(json.dumps(value), encoding="utf-8")
        explained = PublishedModel(with_baseline).score(window, "synthetic-device")["explanation"]
        self.assertAlmostEqual(explained["features"][0]["baseline_z"], 4.0)
        self.assertIn("4.0 robust deviations above the enrolled baseline", explained["summary"])
        window["features"]["press_overlap_fraction"] = 0.1
        self.assertNotIn("explanation", PublishedModel(artifact).score(window, "synthetic-device"))
        value["genuine_baseline"]["scale"][0] = -1
        with_baseline.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            PublishedModel(with_baseline)

    def test_active_model_selection_and_ownership(self) -> None:
        model_id = "0" * 31 + "1"
        model_dir = Path(self.temporary.name) / "models" / model_id
        model_dir.mkdir()
        (model_dir / "metadata.json").write_text(json.dumps(
            {"model_id": model_id, "user_id": "synthetic-user", "device_id": "synthetic-device"}), encoding="utf-8")
        other = {**self.headers, "X-User-ID": "another-user"}
        self.assertEqual(self.client.get("/models/active", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.put(f"/models/{model_id}/active", headers=other).status_code, 403)
        self.assertEqual(self.client.put(f"/models/{model_id}/active", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/models/active", headers=self.headers).json["model_id"], model_id)
        self.assertEqual(self.client.get("/models/active", headers=other).status_code, 404)
        self.assertEqual(create_app().test_client().get("/models/active", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.delete(f"/models/{model_id}", headers=self.headers).status_code, 204)
        self.assertEqual(self.client.get("/models/active", headers=self.headers).status_code, 404)

    def test_published_artifact_validation_and_portable_hash(self) -> None:
        artifact = Path(__file__).resolve().parents[1] / "backend/artifacts/labeled-overlap-v1.json"
        original = PublishedModel(artifact)
        copy_path = Path(self.temporary.name) / "weights.json"
        value = json.loads(artifact.read_text(encoding="utf-8"))
        copy_path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        self.assertEqual(original.digest, PublishedModel(copy_path).digest)
        value["scaler_scale"][0] = 0
        copy_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            PublishedModel(copy_path)

    @unittest.skipUnless(os.environ.get("ML_ALLOW_TRAINING") == "1", "training checks run on the designated host only")
    def test_labeled_training_uses_both_classes_and_persists_preprocessing(self) -> None:
        genuine, impostor = [], []
        for prefix, target, seeds in (("genuine", genuine, (49, 50)), ("impostor", impostor, (51, 52))):
            for seed in seeds:
                rows = generate_windows(seed, 10)
                for row in rows:
                    row["session_id"] = f"{prefix}-{seed}"
                    if prefix == "impostor":
                        row["features"]["press_overlap_fraction"] = 0.8
                target.extend(rows)
        rejected = copy.deepcopy(genuine[0])
        rejected["features"]["pause_fraction"] = 2
        payload = {"profile": "labeled-overlap", "windows": [*genuine, rejected], "impostor_windows": impostor}
        response = self.client.post("/models/train", headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        metadata = response.json
        self.assertEqual(metadata["algorithm"], "LogisticRegression")
        self.assertEqual(metadata["training_window_count"], 40)
        self.assertEqual(metadata["impostor_training_window_count"], 20)
        self.assertEqual(metadata["validation_window_count"], 0)
        self.assertEqual(metadata["rejected_window_count"], 1)
        self.assertLessEqual(metadata["training_false_rejection_rate"], .02)
        url = "/models/" + metadata["model_id"] + "/score"
        normal = self.client.post(url, headers=self.headers, json={"window": genuine[0]}).json
        guest = create_app().test_client().post(url, headers=self.headers, json={"window": impostor[0]}).json
        self.assertGreater(guest["score"], normal["score"])
        self.assertEqual(len(metadata["genuine_baseline"]["median"]), 2)
        self.assertEqual(guest["state"], "UNUSUAL")
        self.assertIn("baseline_z", guest["explanation"]["features"][0])
        self.client.put("/models/" + metadata["model_id"] + "/active", headers=self.headers)
        active = self.client.post("/models/active/score", headers=self.headers, json={"window": impostor[0]}).json
        self.assertEqual(active, guest)
        invalid = copy.deepcopy(payload)
        invalid["impostor_windows"][0]["device_id"] = "other-device"
        self.assertEqual(self.client.post("/models/train", headers=self.headers, json=invalid).status_code, 422)

    @unittest.skipUnless(os.environ.get("ML_ALLOW_TRAINING") == "1", "training checks run on the designated host only")
    def test_cadence_profile_and_multiple_calibration_sessions(self) -> None:
        windows = []
        for session, seed, count in (("train", 49, 20), ("calibration-a", 50, 10), ("calibration-b", 51, 10)):
            rows = generate_windows(seed, count)
            for row in rows:
                row["session_id"] = session
            windows.extend(rows)
        response = self.client.post("/models/train", headers=self.headers, json={
            "windows": windows, "validation_session_ids": ["calibration-a", "calibration-b"],
            "profile": "cadence-overlap",
        })
        self.assertEqual(response.status_code, 201, response.json)
        metadata = response.json
        self.assertEqual(metadata["selected_features"], ["presses_per_second", "press_overlap_fraction"])
        self.assertEqual(metadata["transform"], "log1p")
        self.assertEqual(metadata["validation_sessions"], ["calibration-a", "calibration-b"])
        self.assertLessEqual(metadata["calibration_false_rejection_rate"], 0.05)
        url = "/models/" + metadata["model_id"] + "/score"
        prediction = self.client.post(url, headers=self.headers, json={"window": windows[-1]}).json
        changed = copy.deepcopy(windows[-1])
        changed["features"]["hold_mean_ms"] = 5000
        self.assertEqual(prediction, create_app().test_client().post(url, headers=self.headers, json={"window": changed}).json)

    @unittest.skipUnless(os.environ.get("ML_ALLOW_TRAINING") == "1", "training checks run on the designated host only")
    def test_train_reload_score_and_ownership(self) -> None:
        training = generate_windows(49, 20)
        validation = generate_windows(50, 10)
        for window in training:
            window["session_id"] = "training"
            window["features"]["press_overlap_fraction"] = 0.1
        for window in validation:
            window["session_id"] = "validation"
        response = self.client.post("/models/train", headers=self.headers, json={
            "windows": training + validation, "validation_session_id": "validation",
        })
        self.assertEqual(response.status_code, 201, response.json)
        metadata = response.json
        self.assertEqual(metadata["training_sessions"], ["training"])
        self.assertEqual(metadata["validation_session"], "validation")
        self.assertIn("press_overlap_fraction", metadata["dropped_features"])
        model_url = "/models/" + metadata["model_id"]
        score_url = model_url + "/score"
        scored = self.client.post(score_url, headers=self.headers, json={"window": validation[0]})
        self.assertEqual(scored.status_code, 200, scored.json)
        restarted = create_app().test_client()
        self.assertEqual(
            scored.json,
            restarted.post(score_url, headers=self.headers, json={"window": validation[0]}).json,
        )
        shifted = copy.deepcopy(validation[0])
        for feature in ("hold_mean_ms", "hold_std_ms", "press_interval_mean_ms", "press_interval_std_ms"):
            shifted["features"][feature] = 5000
        shifted_score = self.client.post(score_url, headers=self.headers, json={"window": shifted})
        self.assertGreater(shifted_score.json["score"], scored.json["score"])
        wrong_owner = {**self.headers, "X-User-ID": "another-user"}
        self.assertEqual(self.client.get(model_url, headers=wrong_owner).status_code, 403)
        self.assertEqual(self.client.post(score_url, headers=wrong_owner, json={"window": validation[0]}).status_code, 403)
        self.assertEqual(self.client.delete(model_url, headers=wrong_owner).status_code, 403)
        low_activity = copy.deepcopy(validation[0])
        low_activity["features"]["key_count"] = 2
        self.assertEqual(
            self.client.post(score_url, headers=self.headers, json={"window": low_activity}).json["state"],
            "INSUFFICIENT_DATA",
        )
        self.assertEqual(self.client.post("/models/train", headers=self.headers, json={
            "windows": training, "validation_session_id": "training",
        }).status_code, 422)
        self.assertEqual(self.client.delete(model_url, headers=self.headers).status_code, 204)
        self.assertEqual(self.client.get(model_url, headers=self.headers).status_code, 404)


if __name__ == "__main__":
    unittest.main()
