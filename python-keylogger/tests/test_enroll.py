import json
import unittest

from keylogger.enroll import WindowAccumulator


class EnrollmentTests(unittest.TestCase):
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
