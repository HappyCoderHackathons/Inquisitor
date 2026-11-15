"""Evaluate labeled held-out predictions; 1 means impostor/anomaly, 0 genuine."""

import argparse
import json
import math
from pathlib import Path

from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def summarize(rows: list[dict]) -> dict:
    if not rows or any(
        type(row.get("label")) is not int or row["label"] not in (0, 1)
        or type(row.get("prediction")) is not int or row["prediction"] not in (0, 1)
        or type(row.get("score")) not in (int, float) or not math.isfinite(row["score"])
        or not isinstance(row.get("session_id"), str) or not row["session_id"]
        for row in rows
    ):
        raise ValueError("predictions require binary labels/predictions, finite scores, and session IDs")
    labels = [row["label"] for row in rows]
    if set(labels) != {0, 1}:
        raise ValueError("F1/AUROC evaluation requires genuine and impostor test examples")
    if {row["session_id"] for row in rows if row["label"] == 0} & {
        row["session_id"] for row in rows if row["label"] == 1
    }:
        raise ValueError("genuine and impostor examples must use separate collection sessions")
    predicted = [row["prediction"] for row in rows]
    scores = [row["score"] for row in rows]
    tn, fp, fn, tp = (int(value) for value in confusion_matrix(labels, predicted, labels=[0, 1]).ravel())
    return {
        "positive_class": "impostor",
        "genuine_windows": labels.count(0), "impostor_windows": labels.count(1),
        "genuine_sessions": len({row["session_id"] for row in rows if row["label"] == 0}),
        "impostor_sessions": len({row["session_id"] for row in rows if row["label"] == 1}),
        "f1": float(f1_score(labels, predicted, zero_division=0)),
        "auroc": float(roc_auc_score(labels, scores)),
        "average_precision": float(average_precision_score(labels, scores)),
        "precision": float(precision_score(labels, predicted, zero_division=0)),
        "recall": float(recall_score(labels, predicted, zero_division=0)),
        "accuracy": float(accuracy_score(labels, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(labels, predicted)),
        "false_rejection_rate": fp / (tn + fp),
        "false_acceptance_rate": fn / (tp + fn),
        "confusion_matrix": {"true_genuine": tn, "false_alarms": fp, "missed_impostors": fn, "detected_impostors": tp},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines()]
    report = summarize(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, allow_nan=False)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
