"""Evaluate frozen tray weights on new sessions, without fitting or changing the cutoff."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID

from scripts.evaluate_predictions import summarize

from .published import PublishedModel
from .tray_csv import csv_windows

MINIMUM_TARGETS = {"accuracy": .95, "precision": .85, "recall": .85, "f1": .85,
                   "auroc": .85, "average_precision": .85}


def capture_digest(text: str) -> str:
    return hashlib.sha256(text.removeprefix("\ufeff").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def evaluate(
    model_path: Path, manifest_path: Path, genuine: list[Path], impostor: list[Path],
    score_window: Callable[[dict[str, object]], dict[str, object]],
) -> dict[str, object]:
    model = PublishedModel(model_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("artifact_sha256") != model.digest:
        raise ValueError("manifest does not match the frozen model artifact")
    training_sources = manifest.get("training_capture_sha256")
    if (not isinstance(training_sources, list) or not training_sources
            or any(not isinstance(item, str) or len(item) != 64 for item in training_sources)):
        raise ValueError("manifest must identify the original training captures")
    device_id = manifest.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("manifest requires an authorized device ID")
    if not genuine or not impostor:
        raise ValueError("evaluation requires new genuine and impostor recordings")
    seen = set(training_sources)
    predictions = []
    counts = []
    for label, paths in ((0, genuine), (1, impostor)):
        for path in paths:
            text = path.read_text(encoding="utf-8-sig")
            digest = capture_digest(text)
            if digest in seen:
                raise ValueError("evaluation recording was used in training or supplied more than once")
            seen.add(digest)
            windows = csv_windows(io.StringIO(text), device_id, digest)
            insufficient = 0
            for window in windows:
                result = score_window(window)
                if result["threshold"] != model.threshold:
                    raise ValueError("scoring service differs from the frozen threshold")
                if result["state"] == "INSUFFICIENT_DATA":
                    insufficient += 1
                    continue
                predictions.append({"label": label, "prediction": int(result["state"] == "UNUSUAL"),
                                    "score": result["score"], "session_id": digest})
            counts.append({"label": label, "capture_sha256": digest, "windows": len(windows),
                           "usable": len(windows) - insufficient, "insufficient": insufficient})
    both_classes = {row["label"] for row in predictions} == {0, 1}
    metrics = summarize(predictions) if both_classes else None
    gates = {name: bool(metrics is not None and metrics[name] >= cutoff) for name, cutoff in MINIMUM_TARGETS.items()}
    gates["false_positive_rate"] = bool(metrics is not None and metrics["false_rejection_rate"] <= .02)
    return {
        "role": "new-session evaluation of a frozen model; no fitting or cutoff selection",
        "model_version": model.artifact["model_version"], "artifact_sha256": model.digest,
        "threshold": model.threshold, "metrics": metrics, "captures": counts,
        "target_checks": gates, "empirical_targets_met": all(gates.values()),
        "targets": {**MINIMUM_TARGETS, "false_positive_rate_max": .02},
        "limitations": "Small, correlated window counts are not population guarantees. "
                       "Use the same PC, peripherals, collector, and activity as enrollment. "
                       "Do not reuse these test recordings for tuning and then report them as independent tests.",
    }


def remote_scorer(manifest: dict[str, object]) -> Callable[[dict[str, object]], dict[str, object]]:
    url = os.environ["ML_API_URL"].rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("use HTTPS or the verified loopback SSH tunnel for model requests")
    model_id = manifest["model_id"]
    if not isinstance(model_id, str) or UUID(model_id).hex != model_id:
        raise ValueError("manifest requires the frozen service model ID")
    token = Path(os.environ["ML_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    if any(not isinstance(manifest.get(name), str) or not manifest[name] for name in ("user_id", "device_id")):
        raise ValueError("manifest requires the authorized account and device IDs")
    headers = {"Authorization": "Bearer " + token, "X-User-ID": manifest["user_id"],
               "X-Device-ID": manifest["device_id"], "Content-Type": "application/json"}
    endpoint = url + "/models/" + model_id
    with urlopen(Request(endpoint, headers=headers), timeout=20) as response:
        metadata = json.loads(response.read())
    if (metadata["threshold"] != manifest["threshold"] or metadata["parameters"] != manifest["parameters"]
            or metadata["feature_profile"] != manifest["feature_profile"]
            or metadata["feature_names"] != manifest["feature_names"]):
        raise ValueError("service model metadata differs from the frozen candidate")

    def score(window: dict[str, object]) -> dict[str, object]:
        payload = json.dumps({"window": window}, allow_nan=False).encode("utf-8")
        with urlopen(Request(endpoint + "/score", headers=headers, data=payload), timeout=20) as response:
            result = json.loads(response.read())
        if result["model_id"] != model_id:
            raise ValueError("scoring service returned another model")
        return result

    return score


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True, help="private frozen-model provenance manifest")
    parser.add_argument("--genuine", type=Path, action="append", required=True)
    parser.add_argument("--impostor", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = evaluate(args.model, args.manifest, args.genuine, args.impostor, remote_scorer(manifest))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"Evaluation failed: {error}\n")
    print(json.dumps({"model_version": report["model_version"], "metrics": report["metrics"],
                      "empirical_targets_met": report["empirical_targets_met"]}))


if __name__ == "__main__":
    main()
