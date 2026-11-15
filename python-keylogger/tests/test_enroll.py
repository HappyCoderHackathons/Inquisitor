import json
import math
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from keylogger.enroll import ROOT, MouseAccumulator, WindowAccumulator

VALIDATORS = {
    version: Draft202012Validator(
        json.loads((ROOT / f"shared/feature-window-v{version}.schema.json").read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )
    for version in (1, 2)
}
VALIDATOR = VALIDATORS[1]


class EnrollmentTests(unittest.TestCase):
    def test_shared_schema_fixtures(self) -> None:
        for version, validator in VALIDATORS.items():
            path = ROOT / f"shared/fixtures/feature-window-v{version}.json"
            fixtures = json.loads(path.read_text(encoding="utf-8"))
            for name, window in fixtures["valid"].items():
                with self.subTest(version=version, name=name):
                    self.assertTrue(validator.is_valid(window))
            for name, window in fixtures["invalid"].items():
                with self.subTest(version=version, name=name):
                    self.assertFalse(validator.is_valid(window))

    def test_mouse_features_from_relative_motion(self) -> None:
        pointer = MouseAccumulator()
        for step in range(11):
            pointer.move(10, 0, step * 0.025)
        for step in range(1, 11):
            pointer.move(0, 10, 0.25 + step * 0.025)
        pointer.move(500, 500, 5)
        pointer.move(10, 0, 5.025)
        for second in (6, 6.5, 7.5):
            pointer.click(second)
        features = pointer.features()
        self.assertEqual(features["move_count"], 21)
        self.assertAlmostEqual(features["speed_mean_px_s"], 400)
        self.assertAlmostEqual(features["acceleration_mean_px_s2"], 0)
        self.assertAlmostEqual(features["turn_angle_mean_rad"], math.pi / 2 / 19)
        self.assertEqual(features["click_count"], 3)
        self.assertAlmostEqual(features["click_interval_mean_ms"], 750)

    def test_mouse_window_uses_schema_v2(self) -> None:
        window = WindowAccumulator(track_mouse=True)
        for index in range(30):
            window.press(index, index * 0.1)
            window.release(index, index * 0.1 + 0.05)
        payload = window.finish(10, "device", "session", 0)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["mouse_features"]["move_count"], 0)
        VALIDATORS[2].validate(payload)

    def test_complete_window_ignores_repeat_and_keeps_only_aggregates(self) -> None:
        window = WindowAccumulator()
        window.release("unmatched", 0)
        for index in range(30):
            key = f"private-key-{index}"
            now = index * 0.1
            window.press(key, now)
            window.press(key, now + 0.02)
            if index:
                window.release(f"private-key-{index - 1}", now + 0.08)
        window.release("private-key-29", 3.08)
        payload = window.finish(10, "device", "session", 0)
        self.assertEqual(payload["features"]["key_count"], 30)
        self.assertEqual(payload["features"]["press_overlap_fraction"], 1)
        self.assertAlmostEqual(payload["features"]["hold_mean_ms"], 180)
        self.assertEqual(window.held, {})
        self.assertNotIn("private-key", json.dumps(payload))
        VALIDATOR.validate(payload)

    def test_incomplete_window_is_not_emitted(self) -> None:
        window = WindowAccumulator()
        for index in range(30):
            window.press(index, index * 0.1)
        with self.assertRaises(ValueError):
            window.finish(10, "device", "session", 0)

    def test_pending_key_does_not_stall_completed_samples(self) -> None:
        window = WindowAccumulator()
        window.press("pending", 0)
        for index in range(30):
            window.press(index, 0.1 + index * 0.1)
            window.release(index, 0.18 + index * 0.1)
        self.assertEqual(window.finish(10, "device", "session", 0)["features"]["key_count"], 31)
        next_window = WindowAccumulator()
        next_window.started = 10
        next_window.held = window.held
        next_window.release("pending", 11)
        self.assertEqual(next_window.holds, [])
        self.assertEqual(next_window.held, {})


if __name__ == "__main__":
    unittest.main()
