import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.ml.evaluate_tray import capture_digest, evaluate, remote_scorer
from backend.ml.published import PublishedModel
from backend.ml.tray_csv import HEADER


def capture() -> str:
    rows = [",".join(HEADER)]
    for index in range(62):
        start = index * .5
        rows.append(f"2026-01-01T00:00:00Z,{start},keyboard,kbd0,press,key,,,,,")
        rows.append(f"2026-01-01T00:00:00Z,{start + .1},keyboard,kbd0,release,key,100,,,,")
    return "\n".join(rows)


class TrayEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.artifact = Path(__file__).resolve().parents[1] / "backend/artifacts/tray-two-device-v1.json"
        self.model = PublishedModel(self.artifact)
        self.manifest = self.folder / "manifest.json"
        self.manifest.write_text(json.dumps({"artifact_sha256": PublishedModel(self.artifact).digest,
                                             "training_capture_sha256": ["0" * 64], "device_id": "synthetic-pc",
                                             "user_id": "synthetic-user", "model_id": "0" * 31 + "1",
                                             "threshold": self.model.threshold,
                                             "feature_profile": self.model.profile, "feature_names": list(self.model.names),
                                             "parameters": self.model.artifact["parameters"]}))
        self.genuine = self.folder / "genuine.csv"
        self.impostor = self.folder / "impostor.csv"
        self.genuine.write_text(capture())
        self.impostor.write_text(capture().replace(",100,", ",200,").replace("2026-01-01", "2026-01-02"))

    def score(self, window: dict[str, object]) -> dict[str, object]:
        return self.model.score(window, "synthetic-pc")

    def test_frozen_evaluation_does_not_fit_or_change_the_cutoff(self) -> None:
        original = self.artifact.read_bytes()
        with patch("sklearn.linear_model.LogisticRegression.fit", side_effect=AssertionError("test must not fit")):
            report = evaluate(self.artifact, self.manifest, [self.genuine], [self.impostor], self.score)
        self.assertEqual(self.artifact.read_bytes(), original)
        self.assertEqual(report["threshold"], PublishedModel(self.artifact).threshold)
        self.assertEqual(report["metrics"]["genuine_sessions"], 1)
        self.assertEqual(report["metrics"]["impostor_sessions"], 1)
        self.assertEqual(len(report["target_checks"]), 7)
        self.assertEqual(report["empirical_targets_met"], all(report["target_checks"].values()))

    def test_training_recordings_duplicate_inputs_and_wrong_artifact_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "supplied more than once"):
            evaluate(self.artifact, self.manifest, [self.genuine], [self.genuine], self.score)
        value = json.loads(self.manifest.read_text())
        value["training_capture_sha256"] = [capture_digest(self.genuine.read_text())]
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "used in training"):
            evaluate(self.artifact, self.manifest, [self.genuine], [self.impostor], self.score)
        value["artifact_sha256"] = "wrong"
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "frozen model"):
            evaluate(self.artifact, self.manifest, [self.genuine], [self.impostor], self.score)

    def test_insufficient_activity_cannot_pass_the_targets(self) -> None:
        self.genuine.write_text(capture().replace(",release,key,100,", ",release,key,,"))
        report = evaluate(self.artifact, self.manifest, [self.genuine], [self.impostor], self.score)
        self.assertFalse(report["empirical_targets_met"])
        self.assertIsNone(report["metrics"])
        self.assertEqual(report["captures"][0]["insufficient"], 1)

    def test_remote_scorer_checks_the_model_and_sends_only_aggregate_windows(self) -> None:
        manifest = json.loads(self.manifest.read_text())
        token = self.folder / "token"
        token.write_text("synthetic-test-token")
        response = {"model_id": manifest["model_id"], "threshold": self.model.threshold, "state": "NORMAL", "score": -1}
        with (patch.dict(os.environ, {"ML_API_URL": "http://127.0.0.1:8765", "ML_TOKEN_FILE": str(token)}),
              patch("backend.ml.evaluate_tray.urlopen", side_effect=[io.BytesIO(json.dumps(manifest).encode()),
                                                                     io.BytesIO(json.dumps(response).encode())]) as request):
            scorer = remote_scorer(manifest)
            window = {"schema_version": 3, "features": {"key_count": 30}}
            self.assertEqual(scorer(window), response)
            self.assertEqual(json.loads(request.call_args.args[0].data), {"window": window})
            self.assertTrue(request.call_args.args[0].full_url.endswith("/models/" + manifest["model_id"] + "/score"))
        with (patch.dict(os.environ, {"ML_API_URL": "http://unprotected.example", "ML_TOKEN_FILE": str(token)}),
              self.assertRaisesRegex(ValueError, "HTTPS")):
            remote_scorer(manifest)


if __name__ == "__main__":
    unittest.main()
