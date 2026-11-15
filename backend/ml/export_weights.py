"""Export this service's trusted labeled model as portable numeric JSON."""

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from .model import SCHEMA
from .published import PublishedModel, canonical_hash


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True, help="trusted service-created artifact directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="labeled-overlap-v1")
    args = parser.parse_args()
    metadata = json.loads((args.model_dir / "metadata.json").read_text(encoding="utf-8"))
    if metadata["feature_profile"] != "labeled-overlap":
        parser.error("only labeled-overlap models can be exported by this command")
    # Only the operator's own service-created artifacts are trusted pickle inputs.
    pipeline = joblib.load(args.model_dir / "model.joblib")["model"]
    transform = pipeline.named_steps["functiontransformer"]
    scaler = pipeline.named_steps["standardscaler"]
    model = pipeline.named_steps["logisticregression"]
    if not isinstance(model, LogisticRegression) or not isinstance(scaler, StandardScaler) or transform.func is not np.log1p:
        parser.error("model pipeline is incompatible with the published format")
    if model.classes_.tolist() != [0, 1] or model.coef_.shape != (1, 2):
        parser.error("model must use genuine=0 and impostor=1 with two features")
    artifact = {
        "format_version": 1, "model_version": args.version, "algorithm": "LogisticRegression",
        "schema_version": 1, "schema_sha256": canonical_hash(SCHEMA),
        "feature_names": ["hold_cv", "press_overlap_fraction"],
        "feature_transform": "hold_cv=hold_std_ms/max(hold_mean_ms,1e-8); log1p; standard scaling",
        "scaler_mean": scaler.mean_.tolist(), "scaler_scale": scaler.scale_.tolist(),
        "coefficient": model.coef_[0].tolist(), "intercept": float(model.intercept_[0]),
        "threshold": metadata["threshold"], "threshold_percentile": metadata["threshold_percentile"],
        "parameters": metadata["parameters"],
        "training_summary": {"genuine_windows": metadata["genuine_training_window_count"],
                             "impostor_windows": metadata["impostor_training_window_count"],
                             "genuine_sessions": len(metadata["genuine_training_sessions"]),
                             "impostor_sessions": len(metadata["impostor_training_sessions"])},
        **({"genuine_baseline": metadata["genuine_baseline"]} if "genuine_baseline" in metadata else {}),
        "limitations": "Per-user labeled pilot. Model-selection CV is not an independent final test. No performance guarantee for other users or devices.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(artifact, output, indent=2, allow_nan=False)
        output.write("\n")
    published = PublishedModel(args.output)
    print(json.dumps({"model_version": args.version, "artifact_sha256": published.digest}))


if __name__ == "__main__":
    main()
