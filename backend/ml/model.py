from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import threading
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import joblib
import numpy as np
from jsonschema import Draft202012Validator, FormatChecker
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "shared" / "feature-window-v1.schema.json"
SCHEMA_BYTES = SCHEMA_PATH.read_bytes()
SCHEMA_HASH = hashlib.sha256(SCHEMA_BYTES).hexdigest()
SCHEMA = json.loads(SCHEMA_BYTES)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
FEATURE_NAMES = tuple(SCHEMA["properties"]["features"]["required"])
MAX_DURATION_MS = 600_000
MAX_PRESSES_PER_SECOND = 50


def validate_window(value: object) -> dict[str, object]:
    if not VALIDATOR.is_valid(value):
        raise ValueError("window does not match feature-window schema v1")
    window = cast(dict[str, object], value)
    features = cast(dict[str, int | float], window["features"])
    try:
        finite = all(math.isfinite(number) for number in features.values())
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("features must contain finite numbers")
    duration = cast(int, window["duration_ms"])
    if duration > MAX_DURATION_MS:
        raise ValueError("window duration exceeds 10 minutes")
    if any(features[name] > duration for name in FEATURE_NAMES if name.endswith("_ms")):
        raise ValueError("timing features cannot exceed window duration")
    if features["presses_per_second"] > MAX_PRESSES_PER_SECOND:
        raise ValueError("press rate is implausible")
    return window


def valid_windows(values: list[object]) -> tuple[list[dict[str, object]], int]:
    windows = []
    for value in values:
        try:
            windows.append(validate_window(value))
        except ValueError:
            pass
    return windows, len(values) - len(windows)


def feature_row(window: dict[str, object]) -> list[float]:
    features = cast(dict[str, int | float], window["features"])
    return [float(features[name]) for name in FEATURE_NAMES]


def labeled_features(windows: list[dict[str, object]]) -> np.ndarray:
    rows = []
    for window in windows:
        features = cast(dict[str, float], window["features"])
        rows.append([features["hold_std_ms"] / max(features["hold_mean_ms"], 1e-8),
                     features["press_overlap_fraction"]])
    matrix = np.asarray(rows)
    if not np.isfinite(matrix).all():
        raise ValueError("derived features must be finite")
    return matrix


LABELED_FEATURES = ("hold_cv", "press_overlap_fraction")
FEATURE_TEXT = {"hold_cv": "Hold-time variability", "press_overlap_fraction": "Key overlap"}


def robust_baseline(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    median = np.median(matrix, axis=0)
    scale = 1.4826 * np.median(np.abs(matrix - median), axis=0)
    # A discrete feature can vary while its MAD is zero. Use its training standard deviation.
    return median, np.where(scale > 1e-8, scale, np.std(matrix, axis=0))


def explain(
    raw: np.ndarray, standardized: np.ndarray, coefficient: np.ndarray, baseline: dict[str, list[float]] | None,
) -> dict[str, object]:
    contributions = standardized * coefficient
    order = [int(index) for index in np.argsort(-contributions)]
    features = []
    for index in order:
        item: dict[str, object] = {"feature": LABELED_FEATURES[index], "contribution": float(contributions[index])}
        if baseline is not None:
            scale = baseline["scale"][index]
            item["baseline_z"] = float((raw[index] - baseline["median"][index]) / scale) if scale > 1e-8 else None
        features.append(item)
    top, label = features[0], FEATURE_TEXT[cast(str, features[0]["feature"])]
    if top.get("baseline_z") is not None:
        z = cast(float, top["baseline_z"])
        summary = (f"{label} was {abs(z):.1f} robust deviations {'above' if z > 0 else 'below'} "
                   "the enrolled baseline and contributed most to this alert.")
    else:
        direction = "higher" if standardized[order[0]] > 0 else "lower"
        summary = f"{label} contributed most to this alert ({direction} than the model's training average)."
    return {"features": features, "summary": summary}


class ModelStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._loaded: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()

    def _path(self, model_id: str) -> Path:
        try:
            parsed = UUID(model_id)
        except ValueError as error:
            raise FileNotFoundError("model not found") from error
        if parsed.hex != model_id:
            raise FileNotFoundError("model not found")
        return self.directory / model_id

    def metadata(self, model_id: str, user_id: str, device_id: str) -> dict[str, object]:
        with self._lock:
            metadata = json.loads((self._path(model_id) / "metadata.json").read_text(encoding="utf-8"))
            if metadata["user_id"] != user_id or metadata["device_id"] != device_id:
                raise PermissionError("model belongs to another account or device")
            return metadata

    def _active_path(self, user_id: str, device_id: str) -> Path:
        owner = hashlib.sha256(json.dumps([user_id, device_id]).encode()).hexdigest()
        return self.directory / "active" / f"{owner}.json"

    def activate(self, model_id: str, user_id: str, device_id: str) -> dict[str, object]:
        with self._lock:
            metadata = self.metadata(model_id, user_id, device_id)
            path = self._active_path(user_id, device_id)
            path.parent.mkdir(exist_ok=True, mode=0o700)
            staging = path.with_suffix(".tmp")
            staging.write_text(json.dumps({"model_id": model_id}), encoding="utf-8")
            os.replace(staging, path)
            return metadata

    def active_model_id(self, user_id: str, device_id: str) -> str:
        with self._lock:
            try:
                return json.loads(self._active_path(user_id, device_id).read_text(encoding="utf-8"))["model_id"]
            except FileNotFoundError as error:
                raise FileNotFoundError("no active model for this account and device") from error

    def train(
        self,
        values: list[object],
        user_id: str,
        device_id: str,
        validation_session_id: str | list[str] | None,
        min_train_windows: int = 20,
        min_validation_windows: int = 10,
        profile: str = "all",
        impostor_values: list[object] | None = None,
    ) -> dict[str, object]:
        if os.environ.get("ML_ALLOW_TRAINING") != "1":
            raise RuntimeError("training is disabled; enable it only on the designated training host")
        if profile == "labeled-overlap":
            if validation_session_id is not None:
                raise ValueError("labeled-overlap fits all supplied data; omit validation session fields")
            return self._train_labeled(values, impostor_values or [], user_id, device_id)
        if impostor_values is not None:
            raise ValueError("impostor windows require the labeled-overlap profile")
        if profile not in ("all", "cadence-overlap"):
            raise ValueError("unknown feature profile")
        if validation_session_id is None:
            raise ValueError("validation sessions are required")
        validation_sessions = [validation_session_id] if isinstance(validation_session_id, str) else validation_session_id
        if not validation_sessions or any(not isinstance(session, str) or not session for session in validation_sessions):
            raise ValueError("validation sessions must be nonempty session IDs")
        if len(set(validation_sessions)) != len(validation_sessions):
            raise ValueError("validation session IDs must be unique")
        windows, rejected = valid_windows(values)
        if any(window["device_id"] != device_id or window["mode"] != "enrollment" for window in windows):
            raise ValueError("training requires enrollment windows for the authorized device")
        if any(cast(dict[str, int | float], window["features"])["key_count"] < 30 for window in windows):
            raise ValueError("training windows require at least 30 presses")
        identities = [(window["session_id"], window["sequence"]) for window in windows]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate session/sequence in enrollment")
        if not set(validation_sessions).issubset({window["session_id"] for window in windows}):
            raise ValueError("each validation session must have windows")
        training = [window for window in windows if window["session_id"] not in validation_sessions]
        validation = [window for window in windows if window["session_id"] in validation_sessions]
        if len(training) < min_train_windows or len(validation) < min_validation_windows:
            raise ValueError(
                f"need {min_train_windows} training windows and {min_validation_windows} windows "
                "from a separate validation session"
            )
        selected = ("presses_per_second", "press_overlap_fraction") if profile == "cadence-overlap" else FEATURE_NAMES
        transform = "log1p" if profile == "cadence-overlap" else "identity"
        matrix = np.asarray([feature_row(window) for window in training])
        if transform == "log1p":
            matrix = np.log1p(matrix)
        mask = (np.std(matrix, axis=0) > 1e-8) & np.asarray([name in selected for name in FEATURE_NAMES])
        if not mask.any():
            raise ValueError("all training features are constant")
        parameters = {"n_estimators": 200 if profile == "cadence-overlap" else 100,
                      "max_samples": 16 if profile == "cadence-overlap" else "auto",
                      "random_state": 49, "n_jobs": 1}
        model = IsolationForest(**parameters)
        model.fit(matrix[:, mask])
        validation_matrix = np.asarray([feature_row(window) for window in validation])
        if transform == "log1p":
            validation_matrix = np.log1p(validation_matrix)
        calibration_scores = -model.score_samples(validation_matrix[:, mask])
        threshold = float(np.sort(calibration_scores)[math.ceil(0.95 * len(calibration_scores)) - 1])
        model_id = uuid4().hex
        metadata = {
            "model_id": model_id,
            "state": "ready",
            "user_id": user_id,
            "device_id": device_id,
            "algorithm": "IsolationForest",
            "schema_version": 1,
            "schema_sha256": SCHEMA_HASH,
            "feature_names": list(FEATURE_NAMES),
            "feature_profile": profile,
            "transform": transform,
            "selected_features": [name for name, keep in zip(FEATURE_NAMES, mask) if keep],
            "dropped_features": [name for name, keep in zip(FEATURE_NAMES, mask) if not keep],
            "training_window_count": len(training),
            "rejected_window_count": rejected,
            "validation_window_count": len(validation),
            "training_sessions": sorted({cast(str, window["session_id"]) for window in training}),
            "validation_session": validation_sessions[0] if len(validation_sessions) == 1 else None,
            "validation_sessions": sorted(validation_sessions),
            "threshold": threshold,
            "threshold_percentile": 95,
            "threshold_rule": "empirical 95% genuine order statistic; alert strictly above threshold",
            "calibration_false_rejection_rate": float(np.mean(calibration_scores > threshold)),
            "parameters": parameters,
            "trained_at": datetime.now(UTC).isoformat(),
            "versions": {name: version(name) for name in ("scikit-learn", "numpy", "joblib")},
        }
        return self._publish({"model": model, "mask": mask, "transform": transform}, metadata)

    def _publish(self, bundle: dict[str, object], metadata: dict[str, object]) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix=".training-", dir=self.directory) as temporary:
            staging = Path(temporary)
            joblib.dump(bundle, staging / "model.joblib")
            (staging / "metadata.json").write_text(json.dumps(metadata, allow_nan=False), encoding="utf-8")
            staging.rename(self.directory / cast(str, metadata["model_id"]))
        return metadata

    def _train_labeled(self, values: list[object], impostor_values: list[object], user_id: str, device_id: str) -> dict[str, object]:
        genuine, rejected_genuine = valid_windows(values)
        impostor, rejected_impostor = valid_windows(impostor_values)
        windows = genuine + impostor
        if len(genuine) < 20 or len(impostor) < 20:
            raise ValueError("labeled training needs at least 20 genuine and 20 impostor windows")
        genuine_sessions = {cast(str, window["session_id"]) for window in genuine}
        impostor_sessions = {cast(str, window["session_id"]) for window in impostor}
        if len(genuine_sessions) < 2 or len(impostor_sessions) < 2 or genuine_sessions & impostor_sessions:
            raise ValueError("labeled training needs at least two disjoint sessions per class")
        identities = [(window["session_id"], window["sequence"]) for window in windows]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate session/sequence in labeled enrollment")
        if any(window["device_id"] != device_id for window in windows):
            raise ValueError("labeled windows must belong to the authorized device")
        if any(cast(dict[str, float], window["features"])["key_count"] < 30 for window in windows):
            raise ValueError("labeled training windows require at least 30 presses")
        matrix = labeled_features(windows)
        if not (np.std(matrix, axis=0) > 1e-8).any():
            raise ValueError("all selected training features are constant")
        labels = np.r_[np.zeros(len(genuine)), np.ones(len(impostor))].astype(int)
        parameters = {"C": 0.1, "class_weight": "balanced", "random_state": 49, "max_iter": 1000}
        model = make_pipeline(FunctionTransformer(np.log1p), StandardScaler(), LogisticRegression(**parameters))
        model.fit(matrix, labels)
        genuine_matrix = labeled_features(genuine)
        baseline_median, baseline_scale = robust_baseline(genuine_matrix)
        genuine_scores = model.decision_function(genuine_matrix)
        threshold = float(np.sort(genuine_scores)[math.ceil(.98 * len(genuine_scores)) - 1])
        metadata = {
            "model_id": uuid4().hex, "state": "ready", "user_id": user_id, "device_id": device_id,
            "algorithm": "LogisticRegression", "schema_version": 1, "schema_sha256": SCHEMA_HASH,
            "feature_profile": "labeled-overlap", "feature_names": ["hold_cv", "press_overlap_fraction"],
            "selected_features": ["hold_cv", "press_overlap_fraction"], "dropped_features": [],
            "transform": "log1p then StandardScaler fitted on training data",
            "training_window_count": len(windows), "genuine_training_window_count": len(genuine),
            "impostor_training_window_count": len(impostor), "validation_window_count": 0,
            "rejected_window_count": rejected_genuine + rejected_impostor,
            "training_sessions": sorted(genuine_sessions | impostor_sessions),
            "genuine_training_sessions": sorted(genuine_sessions), "impostor_training_sessions": sorted(impostor_sessions),
            "validation_sessions": [], "validation_session": None,
            "threshold": threshold, "threshold_percentile": 98,
            "threshold_rule": "empirical 98% genuine training score; fresh independent evaluation required",
            "threshold_data_role": "in-sample genuine training data; not independent calibration",
            "calibration_false_rejection_rate": float(np.mean(genuine_scores > threshold)),
            "training_false_rejection_rate": float(np.mean(genuine_scores > threshold)),
            "genuine_baseline": {"median": baseline_median.tolist(), "scale": baseline_scale.tolist()},
            "parameters": parameters, "trained_at": datetime.now(UTC).isoformat(),
            "versions": {name: version(name) for name in ("scikit-learn", "numpy", "joblib")},
        }
        return self._publish({"model": model, "score_kind": "impostor_margin"}, metadata)

    def score(self, model_id: str, value: object, user_id: str, device_id: str) -> dict[str, object]:
        window = validate_window(value)
        if window["device_id"] != device_id:
            raise PermissionError("window belongs to another device")
        with self._lock:
            metadata = self.metadata(model_id, user_id, device_id)
            if metadata["schema_sha256"] != SCHEMA_HASH:
                raise ValueError("model schema differs from the running service")
            if metadata["versions"] != {name: version(name) for name in ("scikit-learn", "numpy", "joblib")}:
                raise ValueError("model dependency versions differ from the running service")
            if cast(dict[str, int | float], window["features"])["key_count"] < 30:
                return {"model_id": model_id, "state": "INSUFFICIENT_DATA", "score": None,
                        "threshold": metadata["threshold"], "score_delta": None}
            if model_id not in self._loaded:
                if len(self._loaded) >= 32:
                    self._loaded.pop(next(iter(self._loaded)))
                self._loaded[model_id] = joblib.load(self._path(model_id) / "model.joblib")
            bundle = self._loaded[model_id]
        explanation = None
        if bundle.get("score_kind") == "impostor_margin":
            pipeline = bundle["model"]
            raw = labeled_features([window])
            score = float(pipeline.decision_function(raw)[0])
            if score > cast(float, metadata["threshold"]):
                standardized = pipeline[:-1].transform(raw)[0]
                explanation = explain(raw[0], standardized, pipeline[-1].coef_[0],
                                      cast(dict[str, list[float]] | None, metadata.get("genuine_baseline")))
        else:
            model = cast(IsolationForest, bundle["model"])
            matrix = np.asarray([feature_row(window)])
            if bundle.get("transform") == "log1p":
                matrix = np.log1p(matrix)
            matrix = matrix[:, bundle["mask"]]
            score = float(-model.score_samples(matrix)[0])
        threshold = cast(float, metadata["threshold"])
        result: dict[str, object] = {
            "model_id": model_id,
            "state": "UNUSUAL" if score > threshold else "NORMAL",
            "score": score,
            "threshold": threshold,
            "score_delta": score - threshold,
        }
        if explanation is not None:
            result["explanation"] = explanation
        return result

    def delete(self, model_id: str, user_id: str, device_id: str) -> None:
        with self._lock:
            self.metadata(model_id, user_id, device_id)
            shutil.rmtree(self._path(model_id))
            self._loaded.pop(model_id, None)
            active = self._active_path(user_id, device_id)
            if active.exists() and json.loads(active.read_text(encoding="utf-8"))["model_id"] == model_id:
                active.unlink()
