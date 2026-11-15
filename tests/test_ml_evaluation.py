import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.ml.evaluate import evaluate, main, render_report
from backend.ml.evaluation import MedianMAD, split_sessions, window_metrics
from backend.ml.model import FEATURE_NAMES
from scripts.generate_sample_windows import generate_windows


def synthetic_sessions():
    windows = []
    for seed, session, count in ((49, "train", 20), (50, "validation", 10), (51, "holdout", 5)):
        group = generate_windows(seed, count)
        for window in group:
            window["session_id"] = session
        windows.extend(group)
    return windows


class EvaluationTests(unittest.TestCase):
    def test_session_split_and_invalid_partitions(self):
        windows = synthetic_sessions()
        training, validation, holdout = split_sessions(windows, "validation", {"holdout"})
        self.assertEqual([len(group) for group in (training, validation, holdout)], [20, 10, 5])
        self.assertEqual({window["session_id"] for window in training}, {"train"})
        for calibration, test in (("validation", {"validation"}), ("missing", {"holdout"}),
                                  ("validation", set()), ("validation", {"train", "holdout"})):
            with self.subTest(calibration=calibration, test=test), self.assertRaises(ValueError):
                split_sessions(windows, calibration, test)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            split_sessions(windows + [windows[0]], "validation", {"holdout"})
        mixed = copy.deepcopy(windows)
        mixed[0]["device_id"] = "other-device"
        with self.assertRaisesRegex(ValueError, "one device"):
            split_sessions(mixed, "validation", {"holdout"})

    def test_metrics_exclude_insufficient_and_leave_missing_rates_unavailable(self):
        self.assertEqual(window_metrics(["NORMAL", "UNUSUAL", "INSUFFICIENT_DATA"])["frr"], 0.5)
        metrics = window_metrics(["NORMAL", "UNUSUAL", "UNUSUAL", "INSUFFICIENT_DATA"], impostor=True)
        self.assertEqual(metrics["far"], 1 / 3)
        self.assertEqual(metrics["recall"], 2 / 3)
        self.assertEqual(metrics["insufficient"], 1)
        self.assertIsNone(window_metrics([], impostor=True)["far"])
        self.assertIsNone(window_metrics(["INSUFFICIENT_DATA"])["frr"])
        with self.assertRaises(ValueError):
            window_metrics(["OFFLINE"])

    def test_robust_deviations_and_aggregate_score(self):
        mask = np.zeros(len(FEATURE_NAMES), dtype=bool)
        mask[:2] = True
        baseline = MedianMAD(np.array([40., 2.]), np.array([10., 0.5]), mask)
        window = generate_windows(49, 1)[0]
        window["features"].update(key_count=60, presses_per_second=1)
        np.testing.assert_allclose(baseline.deviations([window]), [[2., 2.]])
        np.testing.assert_allclose(baseline.scores([window]), [2.])
        with patch.dict(os.environ, {"ML_ALLOW_TRAINING": "0"}), self.assertRaises(RuntimeError):
            MedianMAD.fit([window])

    def test_comparison_keeps_holdout_out_of_fitting_and_calibration(self):
        windows = synthetic_sessions()
        windows[-1]["features"]["key_count"] = 2
        training, validation, holdout = split_sessions(windows, "validation", {"holdout"})
        mask = np.ones(len(FEATURE_NAMES), dtype=bool)
        baseline = MedianMAD(np.zeros(len(FEATURE_NAMES)), np.ones(len(FEATURE_NAMES)), mask)
        metadata = {
            "model_id": "test-model", "threshold": 0.5, "training_sessions": ["train"],
            "dropped_features": [], "versions": {},
        }
        with patch("backend.ml.evaluate.ModelStore.train", return_value=metadata) as train, \
             patch("backend.ml.evaluate.MedianMAD.fit", return_value=baseline) as fit, \
             patch.object(baseline, "scores", return_value=np.array([0.5])) as scores, \
             patch("backend.ml.evaluate.ModelStore.score", side_effect=[
                 {"state": "NORMAL"}, {"state": "UNUSUAL"}, {"state": "NORMAL"},
                 {"state": "NORMAL"}, {"state": "INSUFFICIENT_DATA"},
             ]):
            report = evaluate(windows, "validation", {"holdout"}, [], "synthetic")
        self.assertEqual(train.call_args.args[0], training + validation)
        fit.assert_called_once_with(training)
        self.assertEqual(scores.call_args_list[0].args[0], validation)
        self.assertEqual([call.args[0] for call in scores.call_args_list[1:]], [[w] for w in holdout[:-1]])
        self.assertEqual(report["results"]["MedianMAD"]["genuine"]["frr"], 0)
        self.assertEqual(report["results"]["IsolationForest"]["genuine"]["frr"], 0.25)
        rendered = render_report(report)
        self.assertIn("synthetic", rendered)
        self.assertIn("Unavailable", rendered)
        self.assertIn("4/5", rendered)

    def test_impostor_sessions_cannot_overlap_genuine_or_change_device(self):
        windows = synthetic_sessions()
        for field, value in (("session_id", "holdout"), ("device_id", "other-device")):
            impostor = generate_windows(52, 1)
            impostor[0]["session_id"] = "guest"
            impostor[0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "same device and separate"):
                evaluate(windows, "validation", {"holdout"}, impostor, "synthetic")

    def test_cli_writes_report_and_refuses_to_overwrite_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "genuine.jsonl"
            dataset.write_text("{}\n", encoding="utf-8")
            output = Path(directory) / "report.md"
            argv = ["evaluate", str(dataset), "--validation-session", "validation",
                    "--holdout-session", "holdout", "--data-kind", "synthetic", "--output", str(output)]
            with patch("sys.argv", argv), patch("backend.ml.evaluate.evaluate", return_value={}) as run, \
                 patch("backend.ml.evaluate.render_report", return_value="preliminary report\n"), patch("builtins.print"):
                main()
            run.assert_called_once_with([{}], "validation", {"holdout"}, [], "synthetic")
            self.assertEqual(output.read_text(encoding="utf-8"), "preliminary report\n")
            argv[-1] = str(dataset)
            with patch("sys.argv", argv), patch("sys.stderr"), self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(dataset.read_text(encoding="utf-8"), "{}\n")

    @unittest.skipUnless(os.environ.get("ML_ALLOW_TRAINING") == "1", "fitting runs on designated host only")
    def test_synthetic_fit_and_comparison(self):
        windows = synthetic_sessions()
        for window in windows[:20]:
            window["features"]["burst_count"] = 1
            window["features"]["pause_count"] = 0
        windows[0]["features"]["pause_count"] = 1
        baseline = MedianMAD.fit(windows[:20])
        self.assertFalse(baseline.mask[FEATURE_NAMES.index("burst_count")])
        self.assertTrue(np.isfinite(baseline.scores(windows[20:])).all())
        median = baseline.median.copy()
        baseline.scores(windows[20:])
        np.testing.assert_array_equal(baseline.median, median)
        guest = generate_windows(52, 5)
        for window in guest:
            window["session_id"] = "guest"
        report = evaluate(windows, "validation", {"holdout"}, guest, "synthetic")
        for result in report["results"].values():
            self.assertEqual(result["genuine"]["usable"], 5)
            self.assertEqual(result["impostor"]["usable"], 5)
        self.assertIn("Preliminary", render_report(report))


if __name__ == "__main__":
    unittest.main()
