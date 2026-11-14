# /// script
# requires-python = ">=3.13"
# dependencies = ["jsonschema[format]>=4.18,<5"]
# ///
"""Generate reproducible aggregate-only integration fixtures, never real input data."""

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from statistics import mean, pstdev
from typing import TypedDict

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "shared" / "feature-window-v1.schema.json"


class Window(TypedDict):
    schema_version: int
    device_id: str
    session_id: str
    sequence: int
    mode: str
    started_at: str
    duration_ms: int
    features: dict[str, int | float]


def generate_windows(seed: int, count: int) -> list[Window]:
    if count < 1:
        raise ValueError("count must be positive")
    rng = random.Random(seed)
    started_at = datetime(2000, 1, 1, tzinfo=UTC)
    windows: list[Window] = []
    for sequence in range(count):
        key_count = rng.randint(40, 80)
        holds = [rng.randint(40, 180) for _ in range(key_count)]
        intervals = [rng.randint(80, 350) for _ in range(key_count - 1)]
        pause_count = rng.randint(0, 3)
        for index in rng.sample(range(len(intervals)), pause_count):
            intervals[index] = rng.randint(1100, 2000)
        pause_ms = sum(gap for gap in intervals if gap > 1000)
        duration_ms = max(30_000, sum(intervals) + max(1000, holds[-1]))
        windows.append({
            "schema_version": 1,
            "device_id": "synthetic-device",
            "session_id": "synthetic-session",
            "sequence": sequence,
            "mode": "enrollment",
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "duration_ms": duration_ms,
            "features": {
                "key_count": key_count,
                "presses_per_second": key_count * 1000 / (duration_ms - pause_ms),
                "hold_mean_ms": mean(holds),
                "hold_std_ms": pstdev(holds),
                "press_interval_mean_ms": mean(intervals),
                "press_interval_std_ms": pstdev(intervals),
                "press_interval_p90_ms": sorted(intervals)[ceil(0.9 * len(intervals)) - 1],
                "pause_count": pause_count,
                "pause_fraction": pause_ms / duration_ms,
                "burst_count": pause_count + 1,
                "press_overlap_fraction": sum(
                    hold > gap for hold, gap in zip(holds, intervals)
                ) / len(intervals),
            },
        })
        started_at += timedelta(milliseconds=duration_ms)
    return windows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=49)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--output", type=Path, default=ROOT / "sample-data" / "windows.jsonl")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    windows = generate_windows(args.seed, args.count)
    for window in windows:
        validator.validate(window)
    payload = "".join(json.dumps(window, allow_nan=False) + "\n" for window in windows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        output.write(payload)
    print(f"Wrote {len(windows)} synthetic windows to {args.output}")


if __name__ == "__main__":
    main()
