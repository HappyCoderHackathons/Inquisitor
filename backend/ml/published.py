"""Inference from the published numeric artifact without fitting or unpickling."""

import hashlib
import json
from pathlib import Path

import numpy as np

from .model import (
    LABELED_PROFILES,
    MULTIMODAL_SCHEMA,
    SCHEMA,
    TRAY_PROFILES,
    TRAY_SCHEMA,
    explain,
    labeled_features,
    sufficient_activity,
    validate_window,
)


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class PublishedModel:
    def __init__(self, path: Path) -> None:
        self.artifact = json.loads(path.read_text(encoding="utf-8"))
        artifact = self.artifact
        if artifact["format_version"] != 1 or artifact["algorithm"] != "LogisticRegression":
            raise ValueError("unsupported published model format")
        self.profile = artifact.get("feature_profile", "labeled-overlap")
        if self.profile not in LABELED_PROFILES:
            raise ValueError("unsupported published feature profile")
        self.names = LABELED_PROFILES[self.profile]
        if artifact["feature_names"] != list(self.names):
            raise ValueError("published model feature order is incompatible")
        schema = (MULTIMODAL_SCHEMA if self.profile == "tray-multimodal" else
                  TRAY_SCHEMA if self.profile in TRAY_PROFILES else SCHEMA)
        if artifact["schema_sha256"] != canonical_hash(schema):
            raise ValueError("published model schema is incompatible")
        self.mean = np.asarray(artifact["scaler_mean"], dtype=float)
        self.scale = np.asarray(artifact["scaler_scale"], dtype=float)
        self.coefficient = np.asarray(artifact["coefficient"], dtype=float)
        shape = (len(self.names),)
        if any(vector.shape != shape or not np.isfinite(vector).all() for vector in (self.mean, self.scale, self.coefficient)):
            raise ValueError("published model requires finite vectors matching the feature order")
        if not (self.scale > 0).all():
            raise ValueError("published model scale must be positive")
        self.baseline = artifact.get("genuine_baseline")
        if self.baseline is not None:
            median = np.asarray(self.baseline["median"], dtype=float)
            scale = np.asarray(self.baseline["scale"], dtype=float)
            if median.shape != shape or scale.shape != shape or not np.isfinite([median, scale]).all() or (scale < 0).any():
                raise ValueError("published baseline must match feature order with finite median and nonnegative scale")
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
        if window["schema_version"] == 3 and self.profile not in (*TRAY_PROFILES, "tray-multimodal"):
            raise ValueError("tray windows require a tray model profile")
        if window["schema_version"] == 4 and self.profile != "tray-multimodal":
            raise ValueError("schema v4 requires the tray-multimodal profile")
        result = {"model_version": self.artifact["model_version"], "artifact_sha256": self.digest,
                  "threshold": self.threshold}
        if (self.profile == "tray-multimodal" and window["schema_version"] != 4) or not sufficient_activity(window):
            return {**result, "state": "INSUFFICIENT_DATA", "score": None, "score_delta": None}
        raw = labeled_features([window], self.profile)[0]
        features = (np.log1p(raw) - self.mean) / self.scale
        score = float(features @ self.coefficient + self.intercept)
        if not np.isfinite(score):
            raise ValueError("model score must be finite")
        if score <= self.threshold:
            return {**result, "state": "NORMAL", "score": score, "score_delta": score - self.threshold}
        return {**result, "state": "UNUSUAL", "score": score, "score_delta": score - self.threshold,
                "explanation": explain(raw, features, self.coefficient, self.baseline, self.names)}
