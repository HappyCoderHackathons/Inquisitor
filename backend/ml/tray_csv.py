"""Convert a tray capture CSV to aggregate-only timing windows, without training."""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import mean, pstdev
from typing import TextIO
from uuid import uuid4

from .model import sufficient_activity, validate_window

HEADER = ["iso_time", "monotonic_s", "io_type", "device_id", "event", "control", "hold_ms", "flight_ms", "dx", "dy", "value"]
# Every io_type the tray writes (backend/rust/src/capture.rs). Only keyboard, and
# mouse when opted in, feed the model; the rest are accepted and skipped.
IO_TYPES = frozenset({"keyboard", "mouse", "gamepad", "keypad", "consumer", "touchpad", "touchscreen", "pen",
                      "digitizer", "hid"})
WINDOW_SECONDS = 30
MOUSE_BIN_SECONDS = .02


def csv_windows(
    source: TextIO, device_id: str, session_id: str, mode: str = "monitoring", *,
    keyboard_source: str | None = None, track_mouse: bool = False,
) -> list[dict[str, object]]:
    reader = csv.DictReader(source)
    if reader.fieldnames != HEADER:
        raise ValueError("CSV header does not match the tray capture contract")
    windows: list[dict[str, object]] = []
    presses: list[float] = []
    holds: list[float] = []
    origin: float | None = None
    started_at: datetime | None = None
    previous = 0.0
    sequence = 0
    keyboard_device: str | None = None
    mouse_device: str | None = None
    mouse_bins: dict[int, list[float]] = {}
    clicks: list[float] = []

    def finish() -> None:
        gaps = [(right - left) * 1000 for left, right in pairwise(presses)]
        window: dict[str, object] = {
            "schema_version": 3, "device_id": device_id, "session_id": session_id,
            "sequence": sequence, "mode": mode,
            "started_at": (started_at + timedelta(seconds=sequence * WINDOW_SECONDS)).isoformat(),
            "duration_ms": WINDOW_SECONDS * 1000,
            "features": {
                "key_count": len(presses),
                "hold_sample_count": len(holds),
                "hold_mean_ms": mean(holds) if holds else 0.0,
                "hold_std_ms": pstdev(holds) if holds else 0.0,
                "press_interval_mean_ms": mean(gaps) if gaps else 0.0,
                "press_interval_std_ms": pstdev(gaps) if gaps else 0.0,
            },
        }
        if track_mouse:
            speeds, turns = [], []
            previous: tuple[int, float] | None = None
            path = total_x = total_y = 0.0
            for index, (dx, dy) in sorted(mouse_bins.items()):
                distance = math.hypot(dx, dy)
                if distance == 0:
                    continue
                speeds.append(distance / MOUSE_BIN_SECONDS)
                angle = math.atan2(dy, dx)
                if previous is not None and (index - previous[0]) * MOUSE_BIN_SECONDS <= .25:
                    turns.append(abs((angle - previous[1] + math.pi) % (2 * math.pi) - math.pi))
                previous = index, angle
                path += distance
                total_x += dx
                total_y += dy
            click_intervals = [(right - left) * 1000 for left, right in pairwise(clicks)]
            window["schema_version"] = 4
            window["features"].update({
                "mouse_sample_count": len(speeds), "mouse_click_count": len(clicks),
                "mouse_speed_mean_counts_s": mean(speeds) if speeds else 0.0,
                "mouse_speed_std_counts_s": pstdev(speeds) if speeds else 0.0,
                "mouse_click_interval_mean_ms": mean(click_intervals) if click_intervals else 0.0,
                "mouse_click_interval_std_ms": pstdev(click_intervals) if click_intervals else 0.0,
                "mouse_turn_mean_rad": mean(turns) if turns else 0.0,
                "mouse_turn_std_rad": pstdev(turns) if turns else 0.0,
                "mouse_path_straightness": min(1.0, math.hypot(total_x, total_y) / path) if path else 0.0,
            })
        windows.append(validate_window(window))
        presses.clear()
        holds.clear()
        mouse_bins.clear()
        clicks.clear()

    for line, row in enumerate(reader, 2):
        try:
            if None in row or any(value is None for value in row.values()):
                raise ValueError
            now = float(row["monotonic_s"])
            if not math.isfinite(now) or now < previous or now < 0:
                raise ValueError
            previous = now
            if row["io_type"] not in IO_TYPES:
                raise ValueError
            if origin is None:
                origin = now
                started_at = datetime.fromisoformat(row["iso_time"])
                if started_at.tzinfo is None:
                    raise ValueError
                started_at = started_at.astimezone(UTC)
            while now - origin >= (sequence + 1) * WINDOW_SECONDS:
                finish()
                sequence += 1
            if track_mouse and row["io_type"] == "mouse":
                if mouse_device is None:
                    mouse_device = row["device_id"]
                if not mouse_device or row["device_id"] != mouse_device:
                    raise ValueError
                if row["event"] == "press":
                    clicks.append(now)
                elif row["event"] == "move":
                    dx, dy = int(row["dx"] or 0), int(row["dy"] or 0)
                    if any(not -(2 ** 31) <= value < 2 ** 31 for value in (dx, dy)):
                        raise ValueError
                    index = int((now - origin - sequence * WINDOW_SECONDS) / MOUSE_BIN_SECONDS)
                    vector = mouse_bins.setdefault(index, [0.0, 0.0])
                    vector[0] += dx
                    vector[1] += dy
            if row["io_type"] != "keyboard":
                continue
            if keyboard_source is not None and row["device_id"] != keyboard_source:
                continue
            if keyboard_device is None:
                keyboard_device = row["device_id"]
            if not keyboard_device or row["device_id"] != keyboard_device:
                raise ValueError
            if row["event"] == "press":
                presses.append(now)
            elif row["event"] == "release":
                if row["hold_ms"]:
                    hold = float(row["hold_ms"])
                    if not math.isfinite(hold) or hold < 0:
                        raise ValueError
                    if now - hold / 1000 >= origin + sequence * WINDOW_SECONDS:
                        holds.append(hold)
            else:
                raise ValueError
        except (ValueError, OverflowError) as error:
            raise ValueError(f"invalid tray CSV at line {line}; require one ordered session and one keyboard") from error
    if origin is None:
        raise ValueError("tray CSV is empty")
    if keyboard_source is not None and keyboard_device is None:
        raise ValueError("selected keyboard source is absent")
    # A partial final window is excluded, never stretched or padded into a full one.
    return windows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--device-id", required=True, help="authorized PC identity, not the CSV's kbd0 label")
    parser.add_argument("--session-id", default=None, help="one unique ID per original collection session")
    parser.add_argument("--mode", choices=("enrollment", "monitoring"), default="monitoring")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keyboard-source", help="explicit primary raw-input source when a capture has multiple keyboards")
    parser.add_argument("--mouse", action="store_true", help="opt in to aggregate mouse timing and motion shape")
    args = parser.parse_args()
    try:
        with args.csv.open(encoding="utf-8-sig", newline="") as source:
            windows = csv_windows(source, args.device_id, args.session_id or uuid4().hex, args.mode,
                                  keyboard_source=args.keyboard_source, track_mouse=args.mouse)
        if not windows:
            raise ValueError("capture has no complete 30-second windows")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            for window in windows:
                output.write(json.dumps(window, allow_nan=False) + "\n")
    except (ValueError, OSError) as error:
        parser.exit(1, f"Conversion failed: {error}\n")
    usable = sum(sufficient_activity(window) for window in windows)
    print(json.dumps({"windows": len(windows), "usable_windows": usable,
                      "partial_tail": "excluded", "mouse": "aggregated" if args.mouse else "excluded"}))


if __name__ == "__main__":
    main()
