"""Compare models on consented aggregate sessions or explicitly labeled synthetic fixtures."""

from __future__ import annotations

import argparse
import json
import tempfile
from math import ceil
from pathlib import Path
from typing import cast

import numpy as np

from .evaluation import MedianMAD, split_sessions, window_metrics
from .model import FEATURE_NAMES, ModelStore, validate_window


def evaluate(
    values: list[object], validation_session_id: str, holdout_session_ids: set[str],
    impostor_values: list[object], data_kind: str,
) -> dict[str, object]:
    if data_kind not in {"synthetic", "consented"}:
        raise ValueError("data kind must be synthetic or consented")
    training, validation, holdout = split_sessions(values, validation_session_id, holdout_session_ids)
    device_id = cast(str, training[0]["device_id"])
    impostor = [validate_window(value) for value in impostor_values]
    genuine_sessions = {window["session_id"] for window in training + validation + holdout}
    if any(window["device_id"] != device_id or window["session_id"] in genuine_sessions for window in impostor):
        raise ValueError("impostor windows require the same device and separate session IDs")
    identities = [(window["session_id"], window["sequence"]) for window in impostor]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate session/sequence in impostor data")
    with tempfile.TemporaryDirectory(prefix="inquisitor-evaluation-") as directory:
        store = ModelStore(Path(directory))
        metadata = store.train(training + validation, "evaluation", device_id, validation_session_id)
        baseline = MedianMAD.fit(training)
        scores = baseline.scores(validation)
        threshold = float(np.sort(scores)[ceil(0.95 * len(scores)) - 1])
        results = {}
        for name in ("IsolationForest", "MedianMAD"):
            groups = {}
            for label, windows in (("genuine", holdout), ("impostor", impostor)):
                states = []
                for window in windows:
                    if name == "IsolationForest":
                        scored = store.score(cast(str, metadata["model_id"]), window, "evaluation", device_id)
                        state = cast(str, scored["state"])
                    elif cast(dict[str, int | float], window["features"])["key_count"] < 30:
                        state = "INSUFFICIENT_DATA"
                    else:
                        state = "UNUSUAL" if baseline.scores([window])[0] > threshold else "NORMAL"
                    states.append(state)
                groups[label] = window_metrics(states, impostor=label == "impostor")
            results[name] = {
                "threshold": metadata["threshold"] if name == "IsolationForest" else threshold, **groups,
            }
        return {
            "data_kind": data_kind,
            "sessions": {
                "training": metadata["training_sessions"], "validation": [validation_session_id],
                "holdout": sorted(holdout_session_ids),
                "impostor": sorted({cast(str, window["session_id"]) for window in impostor}),
            },
            "training_windows": len(training), "validation_windows": len(validation),
            "dropped_features": metadata["dropped_features"], "versions": metadata["versions"],
            "baseline": {
                "features": [name for name, keep in zip(FEATURE_NAMES, baseline.mask) if keep],
                "median": baseline.median.tolist(), "scale": baseline.scale.tolist(),
            },
            "results": results,
        }


def render_report(report: dict[str, object]) -> str:
    results = cast(dict[str, dict[str, object]], report["results"])
    lines = [
        "# Preliminary model evaluation", "",
        f"Data: **{report['data_kind']}**. Results are preliminary and are not identity probabilities.", "",
        "Synthetic results verify execution only and do not establish detection accuracy.", "",
        "Complete sessions are separated for training, validation, and holdout. Only validation calibrates",
        "the 95th-percentile thresholds. Both models use the same windows and training-only feature mask.", "",
        f"Training windows: {report['training_windows']}. Validation windows: {report['validation_windows']}.", "",
        "| Model | Genuine usable/total | FRR | Impostor usable/total | FAR | Recall |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    def rate(value: object) -> str:
        return "Unavailable" if value is None else f"{cast(float, value):.4f}"

    for name, result in results.items():
        genuine = cast(dict[str, int | float | None], result["genuine"])
        impostor = cast(dict[str, int | float | None], result["impostor"])
        lines.append(
            f"| {name} | {genuine['usable']}/{genuine['total']} | {rate(genuine['frr'])} | "
            f"{impostor['usable']}/{impostor['total']} | {rate(impostor['far'])} | {rate(impostor['recall'])} |"
        )
    lines.extend([
        "", "FRR is the fraction of usable genuine windows marked unusual. FAR is the fraction of usable",
        "impostor windows marked normal; recall is the fraction marked unusual. Insufficient-data windows",
        "are excluded from rates and counted in the details below. Correlated windows are not independent samples.",
        "No session-level verification or user-switch latency is measured by this window-level comparison.", "",
        "Median/MAD scores average absolute robust z-scores (scale = 1.4826 × MAD). Varying features with",
        "zero MAD use training standard deviation. Constant features are dropped. Scores from different",
        "algorithms are not directly comparable. Threshold ties are normal, matching the service.", "",
        "## Reproduction details", "", "```json", json.dumps(report, indent=2, allow_nan=False), "```", "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("windows", type=Path, help="genuine aggregate JSONL sessions")
    parser.add_argument("--validation-session", required=True)
    parser.add_argument("--holdout-session", action="append", required=True)
    parser.add_argument("--impostor", type=Path, help="consenting second-user aggregate JSONL sessions")
    parser.add_argument("--data-kind", required=True, choices=("synthetic", "consented"))
    parser.add_argument("--output", type=Path, default=Path("docs/model-evaluation.md"))
    args = parser.parse_args()
    if args.output.resolve() in {args.windows.resolve(), args.impostor.resolve() if args.impostor else None}:
        parser.error("output must not overwrite an input dataset")

    def load(path: Path) -> list[object]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    try:
        report = evaluate(
            load(args.windows), args.validation_session, set(args.holdout_session),
            load(args.impostor) if args.impostor else [], args.data_kind,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_report(report), encoding="utf-8")
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f"Evaluation failed: {error}\n")
    print(f"Wrote preliminary evaluation to {args.output}")


if __name__ == "__main__":
    main()
