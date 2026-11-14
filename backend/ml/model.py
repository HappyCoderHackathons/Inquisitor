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

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "shared" / "feature-window-v1.schema.json"
SCHEMA_BYTES = SCHEMA_PATH.read_bytes()
SCHEMA_HASH = hashlib.sha256(SCHEMA_BYTES).hexdigest()
SCHEMA = json.loads(SCHEMA_BYTES)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
FEATURE_NAMES = tuple(SCHEMA["properties"]["features"]["required"])


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
    if any(features[name] > duration for name in FEATURE_NAMES if name.endswith("_ms")):
        raise ValueError("timing features cannot exceed window duration")
    return window


def feature_row(window: dict[str, object]) -> list[float]:
    features = cast(dict[str, int | float], window["features"])
    return [float(features[name]) for name in FEATURE_NAMES]


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

    def train(
        self,
        values: list[object],
        user_id: str,
        device_id: str,
        validation_session_id: str,
        min_train_windows: int = 20,
        min_validation_windows: int = 10,
    ) -> dict[str, object]:
        if os.environ.get("ML_ALLOW_TRAINING") != "1":
            raise RuntimeError("training is disabled; enable it only on the designated training host")
        windows = [validate_window(value) for value in values]
        if any(window["device_id"] != device_id or window["mode"] != "enrollment" for window in windows):
            raise ValueError("training requires enrollment windows for the authorized device")
        if any(cast(dict[str, int | float], window["features"])["key_count"] < 30 for window in windows):
            raise ValueError("training windows require at least 30 presses")
        identities = [(window["session_id"], window["sequence"]) for window in windows]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate session/sequence in enrollment")
        training = [window for window in windows if window["session_id"] != validation_session_id]
        validation = [window for window in windows if window["session_id"] == validation_session_id]
        if len(training) < min_train_windows or len(validation) < min_validation_windows:
            raise ValueError(
                f"need {min_train_windows} training windows and {min_validation_windows} windows "
                "from a separate validation session"
            )
        matrix = np.asarray([feature_row(window) for window in training])
        mask = np.std(matrix, axis=0) > 1e-8
        if not mask.any():
            raise ValueError("all training features are constant")
        model = IsolationForest(n_estimators=100, random_state=49, n_jobs=1)
        model.fit(matrix[:, mask])
        validation_matrix = np.asarray([feature_row(window) for window in validation])
        threshold = float(np.quantile(-model.score_samples(validation_matrix[:, mask]), 0.95, method="higher"))
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
            "dropped_features": [name for name, keep in zip(FEATURE_NAMES, mask) if not keep],
            "training_window_count": len(training),
            "validation_window_count": len(validation),
            "training_sessions": sorted({cast(str, window["session_id"]) for window in training}),
            "validation_session": validation_session_id,
            "threshold": threshold,
            "threshold_percentile": 95,
            "parameters": {"n_estimators": 100, "random_state": 49, "n_jobs": 1},
            "trained_at": datetime.now(UTC).isoformat(),
            "versions": {name: version(name) for name in ("scikit-learn", "numpy", "joblib")},
        }
        with tempfile.TemporaryDirectory(prefix=".training-", dir=self.directory) as temporary:
            staging = Path(temporary)
            joblib.dump({"model": model, "mask": mask}, staging / "model.joblib")
            (staging / "metadata.json").write_text(json.dumps(metadata, allow_nan=False), encoding="utf-8")
            staging.rename(self.directory / model_id)
        return metadata

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
                return {"model_id": model_id, "state": "INSUFFICIENT_DATA", "score": None}
            if model_id not in self._loaded:
                if len(self._loaded) >= 32:
                    self._loaded.pop(next(iter(self._loaded)))
                self._loaded[model_id] = joblib.load(self._path(model_id) / "model.joblib")
            bundle = self._loaded[model_id]
        model = cast(IsolationForest, bundle["model"])
        matrix = np.asarray([feature_row(window)])[:, bundle["mask"]]
        score = float(-model.score_samples(matrix)[0])
        threshold = cast(float, metadata["threshold"])
        return {
            "model_id": model_id,
            "state": "UNUSUAL" if score > threshold else "NORMAL",
            "score": score,
            "threshold": threshold,
        }

    def delete(self, model_id: str, user_id: str, device_id: str) -> None:
        with self._lock:
            self.metadata(model_id, user_id, device_id)
            shutil.rmtree(self._path(model_id))
            self._loaded.pop(model_id, None)
