"""Visible enrollment collector that saves aggregate windows only."""

import argparse
import json
import math
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, pstdev
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
from pynput import keyboard, mouse

ROOT = Path(__file__).resolve().parents[3]
SEGMENT_S = 0.02
STROKE_GAP_S = 0.25


class MouseAccumulator:
    """Relative motion and click timing only; callers pass deltas, never positions."""

    def __init__(self) -> None:
        self.speeds: list[float] = []
        self.accelerations: list[float] = []
        self.turns: list[float] = []
        self.click_intervals: list[float] = []
        self.click_count = 0
        self.last_click: float | None = None
        self.last_move: float | None = None
        self.segment_start = 0.0
        self.segment_dx = self.segment_dy = 0.0
        self.previous_speed: float | None = None
        self.previous_angle: float | None = None

    def move(self, dx: float, dy: float, now: float) -> None:
        if self.last_move is None or now - self.last_move > STROKE_GAP_S:
            self.segment_start, self.segment_dx, self.segment_dy = now, 0.0, 0.0
            self.previous_speed = self.previous_angle = None
            self.last_move = now
            return
        self.last_move = now
        self.segment_dx += dx
        self.segment_dy += dy
        elapsed = now - self.segment_start
        if elapsed < SEGMENT_S:
            return
        distance = math.hypot(self.segment_dx, self.segment_dy)
        speed = distance / elapsed
        self.speeds.append(speed)
        if self.previous_speed is not None:
            self.accelerations.append(abs(speed - self.previous_speed) / elapsed)
        if distance > 0:
            angle = math.atan2(self.segment_dy, self.segment_dx)
            if self.previous_angle is not None:
                self.turns.append(abs((angle - self.previous_angle + math.pi) % (2 * math.pi) - math.pi))
            self.previous_angle = angle
        self.previous_speed = speed
        self.segment_start, self.segment_dx, self.segment_dy = now, 0.0, 0.0

    def click(self, now: float) -> None:
        if self.last_click is not None:
            self.click_intervals.append((now - self.last_click) * 1000)
        self.last_click = now
        self.click_count += 1

    def features(self) -> dict[str, int | float]:
        def stats(values: list[float]) -> tuple[float, float]:
            return (mean(values), pstdev(values)) if values else (0.0, 0.0)

        speed, acceleration, clicks = stats(self.speeds), stats(self.accelerations), stats(self.click_intervals)
        return {
            "move_count": len(self.speeds),
            "speed_mean_px_s": speed[0], "speed_std_px_s": speed[1],
            "acceleration_mean_px_s2": acceleration[0], "acceleration_std_px_s2": acceleration[1],
            "turn_angle_mean_rad": mean(self.turns) if self.turns else 0.0,
            "click_count": self.click_count,
            "click_interval_mean_ms": clicks[0], "click_interval_std_ms": clicks[1],
        }


class WindowAccumulator:
    def __init__(self, track_mouse: bool = False) -> None:
        self.mouse = MouseAccumulator() if track_mouse else None
        self.started = time.perf_counter()
        self.started_at = datetime.now(UTC).isoformat()
        self.held: dict[object, float] = {}
        self.holds: list[float] = []
        self.intervals: list[float] = []
        self.last_press: float | None = None
        self.key_count = 0
        self.overlaps = 0

    def press(self, key: object, now: float) -> None:
        if key in self.held:
            return
        if self.key_count == 0:
            self.started = now
            self.started_at = datetime.now(UTC).isoformat()
        if self.last_press is not None:
            self.intervals.append((now - self.last_press) * 1000)
            self.overlaps += self.last_press in self.held.values()
        self.held[key] = now
        self.last_press = now
        self.key_count += 1

    def release(self, key: object, now: float) -> None:
        pressed = self.held.pop(key, None)
        if pressed is not None and pressed >= self.started:
            self.holds.append((now - pressed) * 1000)

    def finish(self, now: float, device_id: str, session_id: str, sequence: int) -> dict:
        if not self.holds or self.key_count < 30:
            raise ValueError("window needs 30 presses and at least one completed hold")
        duration = max(1, math.ceil((now - self.started) * 1000))
        pauses = [gap for gap in self.intervals if gap > 1000]
        pause_ms = sum(pauses)
        window = {
            "schema_version": 1, "device_id": device_id, "session_id": session_id,
            "sequence": sequence, "mode": "enrollment", "started_at": self.started_at,
            "duration_ms": duration,
            "features": {
                "key_count": self.key_count,
                "presses_per_second": self.key_count * 1000 / (duration - pause_ms),
                "hold_mean_ms": mean(self.holds), "hold_std_ms": pstdev(self.holds),
                "press_interval_mean_ms": mean(self.intervals),
                "press_interval_std_ms": pstdev(self.intervals),
                "press_interval_p90_ms": sorted(self.intervals)[math.ceil(0.9 * len(self.intervals)) - 1],
                "pause_count": len(pauses), "pause_fraction": pause_ms / duration,
                "burst_count": len(pauses) + 1,
                "press_overlap_fraction": self.overlaps / len(self.intervals),
            },
        }
        if self.mouse is not None:
            window["schema_version"] = 2
            window["mouse_features"] = self.mouse.features()
        return window


def collect(
    output, validator, device_id: str, session_id: str, target: int, count: int = 0, mode: str = "enrollment",
    track_mouse: bool = False,
) -> None:
    lock = threading.Lock()
    accumulator = WindowAccumulator(track_mouse)
    stopped = threading.Event()
    last_position: list[float] = []

    def press(key):
        if key == keyboard.Key.esc:
            stopped.set()
            return False
        with lock:
            accumulator.press(key, time.perf_counter())
        return None

    def release(key):
        with lock:
            accumulator.release(key, time.perf_counter())

    def moved(x, y):
        with lock:
            # The previous position stays in memory only to compute the next delta.
            if last_position and accumulator.mouse is not None:
                accumulator.mouse.move(x - last_position[0], y - last_position[1], time.perf_counter())
            last_position[:] = [x, y]

    def clicked(x, y, button, pressed):
        if pressed:
            with lock:
                if accumulator.mouse is not None:
                    accumulator.mouse.click(time.perf_counter())

    deadline = time.perf_counter() + 600
    next_progress = time.perf_counter() + 5
    pointer = mouse.Listener(on_move=moved, on_click=clicked) if track_mouse else None
    if pointer is not None:
        pointer.start()
    with keyboard.Listener(on_press=press, on_release=release) as listener:
        try:
            while count < target and not stopped.wait(0.1):
                if not listener.is_alive() or (pointer is not None and not pointer.is_alive()):
                    raise RuntimeError("input listener stopped")
                if time.perf_counter() >= deadline:
                    raise RuntimeError("session reached its 10-minute limit; no model trained")
                with lock:
                    now = time.perf_counter()
                    if now >= next_progress:
                        print(f"Collecting: {accumulator.key_count}/30 presses, {len(accumulator.holds)} completed holds; {count}/{target} windows", flush=True)
                        next_progress = now + 5
                    if now - accumulator.started < 10 or not accumulator.holds or accumulator.key_count < 30:
                        continue
                    window = accumulator.finish(now, device_id, session_id, count)
                    window["mode"] = mode
                    validator.validate(window)
                    output.write(json.dumps(window, allow_nan=False) + "\n")
                    output.flush()
                    count += 1
                    pending = accumulator.held
                    accumulator = WindowAccumulator(track_mouse)
                    accumulator.held = pending
                print(f"Saved aggregate window {count}/{target}", flush=True)
        finally:
            listener.stop()
            if pointer is not None:
                pointer.stop()
            accumulator.held.clear()
            last_position.clear()
    if stopped.is_set():
        raise RuntimeError("collection stopped with Esc; no model trained")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--resume", type=Path, help="resume an incomplete training collection JSONL file")
    operation.add_argument("--evaluate", action="store_true", help="collect genuine and second-user test sessions without training")
    parser.add_argument("--mouse", action="store_true", help="separately opt in to mouse movement and click timing")
    args = parser.parse_args()
    directory = ROOT / ".private" / "enrollment"
    directory.mkdir(parents=True, exist_ok=True)
    identity_path = directory / "identity.json"
    if not identity_path.exists():
        identity_path.write_text(json.dumps({"device_id": uuid4().hex}), encoding="utf-8")
    device_id = json.loads(identity_path.read_text(encoding="utf-8"))["device_id"]
    version = 2 if args.mouse else 1
    schema = json.loads((ROOT / f"shared/feature-window-v{version}.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    training_session, validation_session = uuid4().hex, uuid4().hex
    output_path = args.resume or directory / f"{'evaluation-' if args.evaluate else ''}{training_session}.jsonl"
    previous = []
    if args.resume:
        previous = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
        if not all(validator.is_valid(window) for window in previous):
            parser.error(f"resume requires schema v{version} windows; repeat the original --mouse choice")
        if previous:
            training_session = previous[0]["session_id"]
        if any(window["session_id"] != training_session or window["device_id"] != device_id for window in previous):
            parser.error("resume requires training windows from this device and a single session")
        if [window["sequence"] for window in previous] != list(range(len(previous))) or len(previous) > 20:
            parser.error("resume requires consecutive sequences and at most 20 training windows")
    print("Aggregate-only enrollment. No text, key identities, or ordered events are saved.")
    print("Type naturally in Notepad while collection is active. Esc stops collection.")
    print("Each window needs at least 10 seconds, 30 presses, and completed hold samples.")
    print("Collection stops between sessions and automatically after each target is reached.")
    if args.mouse:
        print("Mouse opt-in: movement speed, acceleration, turning, and click timing are aggregated.")
        print("Pointer positions, click targets, and window titles are never saved.")
    try:
        with output_path.open("a" if args.resume else "x", encoding="utf-8", newline="\n") as output:
            if args.evaluate:
                input("Evaluation only: ENROLLED USER press Enter for 20 genuine holdout windows, then switch to Notepad: ")
                collect(output, validator, device_id, training_session, 20, mode="monitoring", track_mouse=args.mouse)
                input("Capture stopped. SECOND CONSENTING PERSON: press Enter for 20 impostor-test windows, then switch to Notepad: ")
                collect(output, validator, device_id, validation_session, 20, mode="monitoring", track_mouse=args.mouse)
            else:
                input("Press Enter to begin 20 training windows, then switch to Notepad: ")
                collect(output, validator, device_id, training_session, 20, len(previous), track_mouse=args.mouse)
                input("Training collection stopped. Take a short break; press Enter for 10 validation windows: ")
                collect(output, validator, device_id, validation_session, 10, track_mouse=args.mouse)
    except (EOFError, KeyboardInterrupt, RuntimeError) as error:
        raise SystemExit(f"Collection stopped without training: {error}") from None
    if args.evaluate:
        manifest = {"windows_file": str(output_path), "device_id": device_id,
                    "genuine_session_id": training_session, "impostor_session_id": validation_session,
                    "session_testers": {training_session: "enrolled", validation_session: "second-user"}}
        (directory / "evaluation-latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        print("Evaluation collection complete. The model has not been retrained.")
    else:
        (directory / "latest.json").write_text(json.dumps({
            "windows_file": str(output_path), "device_id": device_id,
            "validation_session_id": validation_session,
            "session_testers": {training_session: "enrolled", validation_session: "enrolled"},
        }), encoding="utf-8")
        print("Collection complete. Ready for remote training.")
