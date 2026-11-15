"""Inference from the published numeric artifact without fitting or unpickling."""

import hashlib
import json
from pathlib import Path
from typing import cast

import numpy as np

from .model import SCHEMA, explain, labeled_features, validate_window


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class PublishedModel:
    def __init__(self, path: Path) -> None:
        self.artifact = json.loads(path.read_text(encoding="utf-8"))
        artifact = self.artifact
        if artifact["format_version"] != 1 or artifact["algorithm"] != "LogisticRegression":
            raise ValueError("unsupported published model format")
        if artifact["feature_names"] != ["hold_cv", "press_overlap_fraction"]:
            raise ValueError("published model feature order is incompatible")
        if artifact["schema_sha256"] != canonical_hash(SCHEMA):
            raise ValueError("published model schema is incompatible")
        self.mean = np.asarray(artifact["scaler_mean"], dtype=float)
        self.scale = np.asarray(artifact["scaler_scale"], dtype=float)
        self.coefficient = np.asarray(artifact["coefficient"], dtype=float)
        if any(vector.shape != (2,) or not np.isfinite(vector).all() for vector in (self.mean, self.scale, self.coefficient)):
            raise ValueError("published model requires finite two-feature vectors")
        if not (self.scale > 0).all():
            raise ValueError("published model scale must be positive")
        self.baseline = artifact.get("genuine_baseline")
        if self.baseline is not None:
            median = np.asarray(self.baseline["median"], dtype=float)
            scale = np.asarray(self.baseline["scale"], dtype=float)
            if median.shape != (2,) or scale.shape != (2,) or not np.isfinite([median, scale]).all() or (scale < 0).any():
                raise ValueError("published baseline requires finite two-feature median and nonnegative scale")
        self.intercept = float(artifact["intercept"])
        self.threshold = float(artifact["threshold"])
        if not np.isfinite([self.intercept, self.threshold]).all():
            raise ValueError("published model intercept and threshold must be finite")
        self.digest = canonical_hash(artifact)

    def metadata(self) -> dict[str, object]:
        return {**{key: self.artifact[key] for key in (
            "model_version", "algorithm", "feature_names", "training_summary", "threshold",
        )}, "artifact_sha256": self.digest, "state": "ready"}

    def score(self, value: object, device_id: str) -> dict[str, object]:
        window = validate_window(value)
        if window["device_id"] != device_id:
            raise PermissionError("window belongs to another device")
        result = {"model_version": self.artifact["model_version"], "artifact_sha256": self.digest,
                  "threshold": self.threshold}
        if cast(dict[str, float], window["features"])["key_count"] < 30:
            return {**result, "state": "INSUFFICIENT_DATA", "score": None, "score_delta": None}
        raw = labeled_features([window])[0]
        features = (np.log1p(raw) - self.mean) / self.scale
        score = float(features @ self.coefficient + self.intercept)
        if not np.isfinite(score):
            raise ValueError("model score must be finite")
        if score <= self.threshold:
            return {**result, "state": "NORMAL", "score": score, "score_delta": score - self.threshold}
        return {**result, "state": "UNUSUAL", "score": score, "score_delta": score - self.threshold,
                "explanation": explain(raw, features, self.coefficient, self.baseline)}
