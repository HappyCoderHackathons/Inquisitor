import copy
import os
import unittest
from unittest.mock import patch

import numpy as np

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


if __name__ == "__main__":
    unittest.main()
