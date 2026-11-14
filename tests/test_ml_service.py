import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.ml.app import create_app
from backend.ml.model import ModelStore, validate_window
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
        self.assertEqual(self.client.get("/health", headers=self.headers).json, {"state": "ok"})
        with patch.dict(os.environ, {"ML_TOKEN_FILE": str(Path(self.temporary.name) / "missing")}):
            with self.assertRaises(FileNotFoundError):
                create_app()

    def test_nonfinite_and_impossible_timing(self) -> None:
        for field, value in (
            ("presses_per_second", float("nan")), ("hold_mean_ms", 90_000), ("key_count", 10 ** 400),
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
