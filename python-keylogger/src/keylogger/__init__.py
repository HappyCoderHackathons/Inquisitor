"""keylogger - a small, opt-in keyboard capture tool for the InputPrint project.

It writes a *live* CSV log (flushed after every event) of keyboard activity.

Two modes:

* timing-only (default) - records event timing (press/release timestamps,
  key-hold duration, and flight time between keys) but **masks the key
  identity**. This matches InputPrint's privacy guarantee: no characters,
  key names, key codes, or ordered sequences are ever written.
* raw (``--raw``)       - additionally records the actual key that was
  pressed. Useful for learning/debugging, but it stores typed content, so
  only use it on a machine you own with explicit consent.

Press ``Esc`` (or ``Ctrl+C``) to stop.

Run only on systems you own or have explicit permission to monitor.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from pynput import keyboard

CSV_HEADER = (
    "iso_time,monotonic_s,io_type,device_id,event,key,hold_ms,flight_ms\n"
)


def _host_device_id() -> str:
    """Best-effort machine identifier (the host NIC MAC), formatted like a MAC.

    NOTE: pynput cannot tell us which *physical* input device generated an
    event, so this is a machine-level id, constant for the whole session, not a
    per-keyboard/per-mouse id. ``uuid.getnode()`` returns a random, locally
    administered address (bit 0x01..00 set) when no real MAC can be found.
    """
    node = uuid.getnode()
    mac = ":".join(f"{(node >> shift) & 0xFF:02x}" for shift in range(40, -1, -8))
    return mac


def _categorize(key) -> str:
    """Coarse, content-free category of a key (used in timing-only mode)."""
    if isinstance(key, keyboard.KeyCode) and key.char is not None:
        ch = key.char
        if ch.isalpha():
            return "alpha"
        if ch.isdigit():
            return "digit"
        return "punct"
    if key == keyboard.Key.space:
        return "space"
    if key in (keyboard.Key.backspace, keyboard.Key.delete):
        return "edit"
    if key in (keyboard.Key.enter, keyboard.Key.tab):
        return "whitespace"
    return "control"


def _describe(key, raw: bool) -> str:
    """Render the key for the log, honoring the privacy mode."""
    if not raw:
        # Content-free: never emit the actual character/name.
        return _categorize(key)
    if isinstance(key, keyboard.KeyCode) and key.char is not None:
        return repr(key.char)
    return str(key)  # e.g. "Key.space", "Key.enter"


class LiveKeyLogger:
    """Listens for key events and appends them to a live-flushed CSV file."""

    def __init__(self, out_path: Path, raw: bool, quiet: bool = False) -> None:
        self.out_path = out_path
        self.raw = raw
        self.quiet = quiet
        self.device_id = _host_device_id()
        self._fh = None
        self._press_monotonic: dict[object, float] = {}
        self._last_press: float | None = None
        self._count = 0

    def _write(self, event: str, key, hold_ms, flight_ms, io_type: str = "keyboard") -> None:
        now = datetime.now().isoformat(timespec="milliseconds")
        mono = f"{time.perf_counter():.6f}"
        hold = "" if hold_ms is None else f"{hold_ms:.1f}"
        flight = "" if flight_ms is None else f"{flight_ms:.1f}"
        key_field = _describe(key, self.raw)
        # Keep the value CSV-safe (describe() may produce a comma in punct mode).
        if "," in key_field or '"' in key_field:
            key_field = '"' + key_field.replace('"', '""') + '"'
        self._fh.write(
            f"{now},{mono},{io_type},{self.device_id},"
            f"{event},{key_field},{hold},{flight}\n"
        )
        self._fh.flush()  # <-- makes the log "live" (tail-able immediately)

        if not self.quiet:
            # Immediate terminal feedback so you can see capture working live.
            detail = f"hold={hold}ms" if event == "release" else f"flight={flight}ms"
            print(f"  [{io_type:<8}][{event:<7}] {key_field:<10} {detail}", flush=True)
        self._count += 1

    def on_press(self, key) -> None:
        now = time.perf_counter()
        flight_ms = None
        if self._last_press is not None:
            flight_ms = (now - self._last_press) * 1000.0
        # Only set the press time if the key isn't already held (ignore autorepeat).
        self._press_monotonic.setdefault(key, now)
        self._last_press = now
        self._write("press", key, None, flight_ms)

    def on_release(self, key) -> bool | None:
        now = time.perf_counter()
        pressed_at = self._press_monotonic.pop(key, None)
        hold_ms = None if pressed_at is None else (now - pressed_at) * 1000.0
        self._write("release", key, hold_ms, None)
        if key == keyboard.Key.esc:
            print("\nEsc pressed - stopping.")
            return False  # stops the listener
        return None

    def run(self) -> None:
        mode = "RAW (records actual keys)" if self.raw else "timing-only (keys masked)"
        print(f"keylogger: {mode}")
        print(f"Logging to: {self.out_path.resolve()}")
        print("Press Esc (or Ctrl+C) to stop.\n")

        new_file = not self.out_path.exists() or self.out_path.stat().st_size == 0
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.out_path.open("a", encoding="utf-8", newline="")
        try:
            if new_file:
                self._fh.write(CSV_HEADER)
                self._fh.flush()
            with keyboard.Listener(
                on_press=self.on_press, on_release=self.on_release
            ) as listener:
                listener.join()
        finally:
            if self._fh is not None:
                self._fh.flush()
                self._fh.close()
            print(f"Stopped. Logged {self._count} events to {self.out_path.resolve()}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="keylogger",
        description="Capture keyboard activity to a live CSV log (InputPrint).",
    )
    parser.add_argument(
        "-o",
        "--out",
        default="keystrokes.log",
        type=Path,
        help="Output log file (default: keystrokes.log).",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Record the ACTUAL keys, not just timing. Stores typed content.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Don't echo each event to the console (only write to the log file).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logger = LiveKeyLogger(out_path=args.out, raw=args.raw, quiet=args.quiet)
    try:
        logger.run()
    except KeyboardInterrupt:
        print("\nInterrupted - stopping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
