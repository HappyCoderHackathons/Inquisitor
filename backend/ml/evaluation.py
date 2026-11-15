"""Session-separated evaluation helpers for aggregate feature windows."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from .model import feature_row, robust_baseline, validate_window


def split_sessions(
    values: list[object], validation_session_id: str, holdout_session_ids: set[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    if not validation_session_id or not holdout_session_ids or validation_session_id in holdout_session_ids:
        raise ValueError("validation and holdout sessions must be nonempty and disjoint")
    windows = [validate_window(value) for value in values]
    if len({window["device_id"] for window in windows}) != 1:
        raise ValueError("evaluation requires exactly one device")
    identities = [(window["session_id"], window["sequence"]) for window in windows]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate session/sequence in evaluation")
    sessions = {cast(str, window["session_id"]) for window in windows}
    if not ({validation_session_id} | holdout_session_ids) <= sessions:
        raise ValueError("requested validation or holdout session is missing")
    training, validation, holdout = [], [], []
    for window in windows:
        session = window["session_id"]
        if session == validation_session_id:
            validation.append(window)
        elif session in holdout_session_ids:
            holdout.append(window)
        else:
            training.append(window)
    if not training:
        raise ValueError("at least one separate training session is required")
    return training, validation, holdout


@dataclass
class MedianMAD:
    median: NDArray[np.float64]
    scale: NDArray[np.float64]
    mask: NDArray[np.bool_]

    @classmethod
    def fit(cls, windows: list[dict[str, object]]) -> MedianMAD:
        if os.environ.get("ML_ALLOW_TRAINING") != "1":
            raise RuntimeError("training is disabled; enable it only on the designated training host")
        matrix = np.asarray([feature_row(window) for window in windows], dtype=float)
        if matrix.ndim != 2 or not np.isfinite(matrix).all():
            raise ValueError("baseline requires a nonempty finite training matrix")
        mask = np.std(matrix, axis=0) > 1e-8
        if not mask.any():
            raise ValueError("all training features are constant")
        median, scale = robust_baseline(matrix[:, mask])
        return cls(median, scale, mask)

    def deviations(self, windows: list[dict[str, object]]) -> NDArray[np.float64]:
        matrix = np.asarray([feature_row(window) for window in windows], dtype=float)
        return np.abs(matrix[:, self.mask] - self.median) / self.scale

    def scores(self, windows: list[dict[str, object]]) -> NDArray[np.float64]:
        return np.mean(self.deviations(windows), axis=1)


def window_metrics(states: list[str], *, impostor: bool = False) -> dict[str, int | float | None]:
    if any(state not in {"NORMAL", "UNUSUAL", "INSUFFICIENT_DATA"} for state in states):
        raise ValueError("unexpected scoring state")
    normal = states.count("NORMAL")
    unusual = states.count("UNUSUAL")
    usable = normal + unusual
    result: dict[str, int | float | None] = {
        "total": len(states), "usable": usable, "insufficient": len(states) - usable,
        "normal": normal, "unusual": unusual,
    }
    if impostor:
        result.update(far=normal / usable if usable else None, recall=unusual / usable if usable else None)
    else:
        result["frr"] = unusual / usable if usable else None
    return result


def switch_latency(
    genuine: list[tuple[str, int]], impostor: list[tuple[str, int]],
) -> dict[str, int | float | bool | None]:
    # Spec 7.6 rule: NEEDS_VERIFICATION when 3 of the last 4 usable windows are unusual.
    recent: list[str] = []
    alert_before_switch = False
    for index, (state, _) in enumerate(genuine + impostor):
        if state == "INSUFFICIENT_DATA":
            continue
        recent = (recent + [state])[-4:]
        if recent.count("UNUSUAL") < 3:
            continue
        if index < len(genuine):
            alert_before_switch = True
            continue
        elapsed = impostor[: index - len(genuine) + 1]
        return {
            "alert_before_switch": alert_before_switch, "windows": len(elapsed),
            "seconds": sum(duration for _, duration in elapsed) / 1000,
        }
    return {"alert_before_switch": alert_before_switch, "windows": None, "seconds": None}
