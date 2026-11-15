import unittest

from scripts.evaluate_predictions import summarize


class EvaluationTests(unittest.TestCase):
    def test_impostor_positive_label_and_rates(self) -> None:
        rows = [
            {"label": label, "prediction": prediction, "score": score, "session_id": str(label)}
            for label, prediction, score in ((0, 0, 0.1), (0, 1, 0.9), (1, 0, 0.2), (1, 1, 0.8))
        ]
        metrics = summarize(rows)
        self.assertEqual(metrics["f1"], 0.5)
        self.assertEqual(metrics["auroc"], 0.5)
        self.assertEqual(metrics["false_rejection_rate"], 0.5)
        self.assertEqual(metrics["false_acceptance_rate"], 0.5)

    def test_missing_class_and_shared_session_are_rejected(self) -> None:
        genuine = {"label": 0, "prediction": 0, "score": 0.1, "session_id": "session"}
        with self.assertRaises(ValueError):
            summarize([genuine])
        with self.assertRaises(ValueError):
            summarize([genuine, {**genuine, "label": 1, "score": 0.9}])


if __name__ == "__main__":
    unittest.main()
