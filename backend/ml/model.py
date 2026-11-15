from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
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
# v2 adds mouse_features; models use keyboard features only, so they stay pinned to the v1 hash.
VALIDATORS = {1: VALIDATOR, 2: Draft202012Validator(
    json.loads((SCHEMA_PATH.parent / "feature-window-v2.schema.json").read_bytes()), format_checker=FormatChecker()
)}
TRAY_SCHEMA = json.loads((SCHEMA_PATH.parent / "feature-window-v3.schema.json").read_bytes())
TRAY_SCHEMA_HASH = hashlib.sha256((SCHEMA_PATH.parent / "feature-window-v3.schema.json").read_bytes()).hexdigest()
VALIDATORS[3] = Draft202012Validator(TRAY_SCHEMA, format_checker=FormatChecker())
MULTIMODAL_SCHEMA_BYTES = (SCHEMA_PATH.parent / "feature-window-v4.schema.json").read_bytes()
MULTIMODAL_SCHEMA = json.loads(MULTIMODAL_SCHEMA_BYTES)
MULTIMODAL_SCHEMA_HASH = hashlib.sha256(MULTIMODAL_SCHEMA_BYTES).hexdigest()
VALIDATORS[4] = Draft202012Validator(MULTIMODAL_SCHEMA, format_checker=FormatChecker())
MAX_DURATION_MS = 600_000
MAX_PRESSES_PER_SECOND = 50


def validate_window(value: object) -> dict[str, object]:
    version = value.get("schema_version") if isinstance(value, dict) else None
    validator = VALIDATORS.get(version) if isinstance(version, int) else None
    if validator is None or not validator.is_valid(value):
        raise ValueError("window does not match feature-window schema v1, v2, v3, or v4")
    window = cast(dict[str, object], value)
    features = cast(dict[str, int | float], window["features"])
    mouse = cast(dict[str, int | float], window.get("mouse_features", {}))
    try:
        finite = all(math.isfinite(number) for number in [*features.values(), *mouse.values()])
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("features must contain finite numbers")
    duration = cast(int, window["duration_ms"])
    if duration > MAX_DURATION_MS:
        raise ValueError("window duration exceeds 10 minutes")
    if any(number > duration for name, number in [*features.items(), *mouse.items()] if name.endswith("_ms")):
        raise ValueError("timing features cannot exceed window duration")
    if features.get("presses_per_second", features["key_count"] * 1000 / duration) > MAX_PRESSES_PER_SECOND:
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
    if window["schema_version"] in (3, 4):
        raise ValueError("tray windows require a matching tray model profile")
    features = cast(dict[str, int | float], window["features"])
    return [float(features[name]) for name in FEATURE_NAMES]


def sufficient_activity(window: dict[str, object]) -> bool:
    features = cast(dict[str, int | float], window["features"])
    if window["schema_version"] == 4 and features["mouse_sample_count"] >= 10:
        return True
    return features["key_count"] >= 30 and features.get("hold_sample_count", 1) > 0


def labeled_features(windows: list[dict[str, object]], profile: str = "labeled-overlap") -> np.ndarray:
    rows = []
    for window in windows:
        features = cast(dict[str, float], window["features"])
        if profile == "tray-multimodal":
            if window["schema_version"] != 4:
                raise ValueError("tray-multimodal requires schema v4 mouse and keyboard aggregates")
            rows.append([
                *[features[name] for name in TRAY_FULL_FEATURES[:4]],
                features["hold_std_ms"] / max(features["hold_mean_ms"], 1e-8),
                features["press_interval_std_ms"] / max(features["press_interval_mean_ms"], 1e-8),
                features["key_count"] * 1000 / cast(int, window["duration_ms"]),
                features["mouse_click_count"] * 1000 / cast(int, window["duration_ms"]),
                features["mouse_click_interval_std_ms"] / max(features["mouse_click_interval_mean_ms"], 1e-8),
                features["mouse_speed_std_counts_s"] / max(features["mouse_speed_mean_counts_s"], 1e-8),
                features["mouse_turn_mean_rad"], features["mouse_turn_std_rad"], features["mouse_path_straightness"],
            ])
            continue
        if window["schema_version"] == 4:
            raise ValueError("schema v4 requires the tray-multimodal profile")
        if profile == "labeled-overlap" and window["schema_version"] == 3:
            raise ValueError("tray windows require the tray-timing model profile")
        second = (features["press_interval_std_ms"] / max(features["press_interval_mean_ms"], 1e-8)
                  if profile in TRAY_PROFILES else features["press_overlap_fraction"])
        derived = [features["hold_std_ms"] / max(features["hold_mean_ms"], 1e-8), second]
        if profile == "tray-timing-full":
            derived = [features[name] for name in TRAY_FULL_FEATURES[:4]] + derived
        rows.append(derived)
    matrix = np.asarray(rows)
    if not np.isfinite(matrix).all():
        raise ValueError("derived features must be finite")
    return matrix


LABELED_FEATURES = ("hold_cv", "press_overlap_fraction")
TRAY_FEATURES = ("hold_cv", "press_interval_cv")
TRAY_FULL_FEATURES = ("hold_mean_ms", "hold_std_ms", "press_interval_mean_ms", "press_interval_std_ms",
                      "hold_cv", "press_interval_cv")
TRAY_PROFILES = ("tray-timing", "tray-timing-full")
MULTIMODAL_FEATURES = (*TRAY_FULL_FEATURES, "key_rate", "click_rate", "click_interval_cv", "speed_cv",
                       "turn_mean", "turn_std", "straightness")
LABELED_PROFILES = {"labeled-overlap": LABELED_FEATURES, "tray-timing": TRAY_FEATURES,
                    "tray-timing-full": TRAY_FULL_FEATURES, "tray-multimodal": MULTIMODAL_FEATURES}
FEATURE_TEXT = {"hold_cv": "Hold-time variability", "press_overlap_fraction": "Key overlap",
                "press_interval_cv": "Inter-press timing variability", "hold_mean_ms": "Average hold time",
                "hold_std_ms": "Hold-time spread", "press_interval_mean_ms": "Average inter-press time",
                "press_interval_std_ms": "Inter-press timing spread"}
FEATURE_TEXT.update({"key_rate": "Keyboard press rate", "click_rate": "Mouse click rate",
                     "click_interval_cv": "Click timing variability", "speed_cv": "Mouse speed variability",
                     "turn_mean": "Average mouse turning", "turn_std": "Mouse turning spread",
                     "straightness": "Mouse path straightness"})


def robust_baseline(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    median = np.median(matrix, axis=0)
    scale = 1.4826 * np.median(np.abs(matrix - median), axis=0)
    # A discrete feature can vary while its MAD is zero. Use its training standard deviation.
    return median, np.where(scale > 1e-8, scale, np.std(matrix, axis=0))


def explain(
    raw: np.ndarray, standardized: np.ndarray, coefficient: np.ndarray, baseline: dict[str, list[float]] | None,
    feature_names: tuple[str, ...] = LABELED_FEATURES,
) -> dict[str, object]:
    contributions = standardized * coefficient
    order = [int(index) for index in np.argsort(-contributions)]
    features = []
    for index in order:
        item: dict[str, object] = {"feature": feature_names[index], "contribution": float(contributions[index])}
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

    def check_available(self) -> None:
        with self._lock:
            if not self.directory.is_dir():
                raise FileNotFoundError("model store directory is unavailable")
            with os.scandir(self.directory) as entries:
                next(entries, None)

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
            if metadata["user_id"] != user_id or device_id not in metadata.get("device_ids", [metadata["device_id"]]):
                raise PermissionError("model belongs to another account or device")
            return metadata

    def _write_metadata(self, model_path: Path, metadata: dict[str, object]) -> None:
        staging = model_path / "metadata.json.tmp"
        staging.write_text(json.dumps(metadata, allow_nan=False), encoding="utf-8")
        os.replace(staging, model_path / "metadata.json")

    def reserve_training_job(self, user_id: str, device_id: str) -> str:
        if os.environ.get("ML_ALLOW_TRAINING") != "1":
            raise RuntimeError("training is disabled; enable it only on the designated training host")
        model_id = uuid4().hex
        path = self.directory / model_id
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = {
            "model_id": model_id,
            "user_id": user_id,
            "device_id": device_id,
            "state": "queued",
            "trained_at": None,
        }
        with self._lock:
            self._write_metadata(path, metadata)
        return model_id

    def set_job_state(
        self, model_id: str, user_id: str, device_id: str, state: str, **extra: object,
    ) -> dict[str, object]:
        if state not in {"queued", "running", "ready", "failed"}:
            raise ValueError("job state must be queued, running, ready, or failed")
        with self._lock:
            path = self._path(model_id)
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            if metadata["user_id"] != user_id or metadata["device_id"] != device_id:
                raise PermissionError("model belongs to another account or device")
            metadata["state"] = state
            metadata.update(extra)
            self._write_metadata(path, metadata)
            return metadata

    def fail_job(self, model_id: str, user_id: str, device_id: str, error: str) -> dict[str, object]:
        return self.set_job_state(model_id, user_id, device_id, "failed", error=error)

    def _active_path(self, user_id: str, device_id: str) -> Path:
        owner = hashlib.sha256(json.dumps([user_id, device_id]).encode()).hexdigest()
        return self.directory / "active" / f"{owner}.json"

    def ready_metadata(self, model_id: str, user_id: str, device_id: str) -> dict[str, object]:
        metadata = self.metadata(model_id, user_id, device_id)
        if metadata.get("state", "ready") != "ready":
            raise ValueError(f"model is {metadata['state']}, not ready")
        return metadata

    def activate(self, model_id: str, user_id: str, device_id: str) -> dict[str, object]:
        with self._lock:
            metadata = self.ready_metadata(model_id, user_id, device_id)
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
        model_id: str | None = None,
        device_ids: list[str] | None = None,
    ) -> dict[str, object]:
        if os.environ.get("ML_ALLOW_TRAINING") != "1":
            raise RuntimeError("training is disabled; enable it only on the designated training host")
        reserved = model_id is not None
        model_id = model_id or uuid4().hex
        try:
            if reserved:
                self.set_job_state(model_id, user_id, device_id, "running")
            if device_ids is not None and profile != "tray-multimodal":
                raise ValueError("multiple devices require the tray-multimodal profile")
            if profile in LABELED_PROFILES:
                if validation_session_id is not None:
                    raise ValueError("labeled-overlap fits all supplied data; omit validation session fields")
                return self._train_labeled(values, impostor_values or [], user_id, device_id, model_id, profile, device_ids)
            if impostor_values is not None:
                raise ValueError("impostor windows require the labeled-overlap profile")
            if profile not in ("all", "cadence-overlap"):
                raise ValueError("unknown feature profile")
            if validation_session_id is None:
                raise ValueError("validation sessions are required")
            validation_sessions = (
                [validation_session_id] if isinstance(validation_session_id, str) else validation_session_id
            )
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
        except Exception as error:
            if reserved:
                self.fail_job(model_id, user_id, device_id, str(error))
            raise

    def _publish(self, bundle: dict[str, object], metadata: dict[str, object]) -> dict[str, object]:
        target = self.directory / cast(str, metadata["model_id"])
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        joblib.dump(bundle, target / "model.joblib.tmp")
        os.replace(target / "model.joblib.tmp", target / "model.joblib")
        self._write_metadata(target, metadata)
        return metadata

    def _train_labeled(
        self, values: list[object], impostor_values: list[object], user_id: str, device_id: str, model_id: str,
        profile: str = "labeled-overlap",
        device_ids: list[str] | None = None,
    ) -> dict[str, object]:
        genuine, rejected_genuine = valid_windows(values)
        impostor, rejected_impostor = valid_windows(impostor_values)
        windows = genuine + impostor
        minimum = 10 if profile in TRAY_PROFILES else 20
        if len(genuine) < minimum or len(impostor) < minimum:
            raise ValueError(f"labeled training needs at least {minimum} genuine and {minimum} impostor windows")
        genuine_sessions = {cast(str, window["session_id"]) for window in genuine}
        impostor_sessions = {cast(str, window["session_id"]) for window in impostor}
        sessions = 1 if profile in TRAY_PROFILES else 2
        if len(genuine_sessions) < sessions or len(impostor_sessions) < sessions or genuine_sessions & impostor_sessions:
            raise ValueError(f"labeled training needs at least {sessions} disjoint sessions per class")
        identities = [(window["session_id"], window["sequence"]) for window in windows]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate session/sequence in labeled enrollment")
        authorized_devices = device_ids if device_ids is not None else [device_id]
        if (not authorized_devices or any(not isinstance(item, str) or not item for item in authorized_devices)
                or len(set(authorized_devices)) != len(authorized_devices) or device_id not in authorized_devices):
            raise ValueError("device IDs must be a unique, explicit allowlist including the authorized device")
        if any(window["device_id"] not in authorized_devices for window in windows):
            raise ValueError("labeled windows must belong to the authorized device")
        if profile == "tray-multimodal":
            for enrolled_device in authorized_devices:
                if not all(any(window["device_id"] == enrolled_device for window in group) for group in (genuine, impostor)):
                    raise ValueError("each enrolled device requires genuine and impostor examples")
        if any(not sufficient_activity(window) for window in windows):
            raise ValueError("labeled training windows require at least 30 presses")
        if profile != "labeled-overlap" and any(window["mode"] != "enrollment" for window in windows):
            raise ValueError("labeled training requires enrollment windows")
        names = LABELED_PROFILES[profile]
        schema_hash = (MULTIMODAL_SCHEMA_HASH if profile == "tray-multimodal" else
                       TRAY_SCHEMA_HASH if profile in TRAY_PROFILES else SCHEMA_HASH)
        matrix = labeled_features(windows, profile)
        if not (np.std(matrix, axis=0) > 1e-8).any():
            raise ValueError("all selected training features are constant")
        labels = np.r_[np.zeros(len(genuine)), np.ones(len(impostor))].astype(int)
        parameters = {"C": 3.0 if profile == "tray-multimodal" else 10.0 if profile == "tray-timing-full" else 0.1,
                      "class_weight": "balanced", "random_state": 49, "max_iter": 1000}
        model = make_pipeline(FunctionTransformer(np.log1p), StandardScaler(), LogisticRegression(**parameters))
        model.fit(matrix, labels)
        genuine_matrix = labeled_features(genuine, profile)
        baseline_median, baseline_scale = robust_baseline(genuine_matrix)
        genuine_scores = model.decision_function(genuine_matrix)
        threshold = float(np.sort(genuine_scores)[math.ceil(.98 * len(genuine_scores)) - 1])
        margin_std = .35 if profile == "tray-multimodal" else 0.0
        threshold += margin_std * float(np.std(genuine_scores))
        metadata = {
            "model_id": model_id, "state": "ready", "user_id": user_id, "device_id": device_id,
            "algorithm": "LogisticRegression", "schema_version": 4 if profile == "tray-multimodal" else 3 if profile in TRAY_PROFILES else 1,
            "schema_sha256": schema_hash,
            "feature_profile": profile, "feature_names": list(names),
            "selected_features": list(names), "dropped_features": [],
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
        if profile == "tray-multimodal":
            metadata["device_ids"] = sorted(authorized_devices)
            metadata["threshold_margin_std"] = margin_std
            metadata["threshold_rule"] = "empirical 98% genuine training score plus 0.35 training score standard deviations"
        return self._publish({"model": model, "score_kind": "impostor_margin"}, metadata)

    def score(self, model_id: str, value: object, user_id: str, device_id: str) -> dict[str, object]:
        window = validate_window(value)
        if window["device_id"] != device_id:
            raise PermissionError("window belongs to another device")
        with self._lock:
            metadata = self.ready_metadata(model_id, user_id, device_id)
            profile = cast(str, metadata.get("feature_profile", "all"))
            expected_hash = (MULTIMODAL_SCHEMA_HASH if profile == "tray-multimodal" else
                             TRAY_SCHEMA_HASH if profile in TRAY_PROFILES else SCHEMA_HASH)
            if metadata["schema_sha256"] != expected_hash:
                raise ValueError("model schema differs from the running service")
            if window["schema_version"] == 3 and profile not in (*TRAY_PROFILES, "tray-multimodal"):
                raise ValueError("tray windows require a tray model profile")
            if window["schema_version"] == 4 and profile != "tray-multimodal":
                raise ValueError("schema v4 requires the tray-multimodal profile")
            if metadata["versions"] != {name: version(name) for name in ("scikit-learn", "numpy", "joblib")}:
                raise ValueError("model dependency versions differ from the running service")
            if (profile == "tray-multimodal" and window["schema_version"] != 4) or not sufficient_activity(window):
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
            raw = labeled_features([window], profile)
            score = float(pipeline.decision_function(raw)[0])
            if score > cast(float, metadata["threshold"]):
                standardized = pipeline[:-1].transform(raw)[0]
                explanation = explain(raw[0], standardized, pipeline[-1].coef_[0],
                                      cast(dict[str, list[float]] | None, metadata.get("genuine_baseline")),
                                      LABELED_PROFILES[profile])
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
            self._remove_model(model_id, user_id, device_id)

    def deactivate(self, user_id: str, device_id: str) -> bool:
        with self._lock:
            active = self._active_path(user_id, device_id)
            if not active.exists():
                return False
            active.unlink()
            return True

    def delete_user(self, user_id: str) -> list[str]:
        with self._lock:
            owned: list[tuple[str, str]] = []
            if self.directory.exists():
                for child in self.directory.iterdir():
                    if not child.is_dir() or not (child / "metadata.json").is_file():
                        continue
                    try:
                        if UUID(child.name).hex != child.name:
                            continue
                    except ValueError:
                        continue
                    metadata = json.loads((child / "metadata.json").read_text(encoding="utf-8"))
                    if metadata.get("user_id") == user_id and isinstance(metadata.get("device_id"), str):
                        owned.append((child.name, cast(str, metadata["device_id"])))
            for model_id, device_id in owned:
                self._remove_model(model_id, user_id, device_id)
            return [model_id for model_id, _device_id in owned]

    def _remove_model(self, model_id: str, user_id: str, device_id: str) -> None:
        path = self._path(model_id)
        devices = [device_id]
        if path.exists():
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            devices = metadata.get("device_ids", [device_id])
            shutil.rmtree(path)
        self._loaded.pop(model_id, None)
        for enrolled_device in devices:
            active = self._active_path(user_id, enrolled_device)
            if active.exists():
                recorded = json.loads(active.read_text(encoding="utf-8"))
                if recorded.get("model_id") == model_id:
                    active.unlink()
