import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.generate_sample_windows import ROOT, SCHEMA, generate_windows


class SampleWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def test_reproducible_valid_windows(self) -> None:
        windows = generate_windows(49, 100)
        self.assertEqual(windows, generate_windows(49, 100))
        self.assertNotEqual(windows, generate_windows(50, 100))
        for index, window in enumerate(windows):
            self.validator.validate(window)
            json.dumps(window, allow_nan=False)
            self.assertEqual(window["sequence"], index)
            self.assertLessEqual(window["duration_ms"], 60_000)
            features = window["features"]
            self.assertEqual(features["burst_count"], features["pause_count"] + 1)
            self.assertAlmostEqual(
                features["presses_per_second"],
                features["key_count"] * 1000 / (
                    window["duration_ms"] * (1 - features["pause_fraction"])
                ),
            )
            if index:
                previous = windows[index - 1]
                self.assertEqual(
                    datetime.fromisoformat(window["started_at"]),
                    datetime.fromisoformat(previous["started_at"])
                    + timedelta(milliseconds=previous["duration_ms"]),
                )

    def test_contract_rejects_invalid_fields(self) -> None:
        original = generate_windows(49, 1)[0]
        for field in original:
            with self.subTest(missing=field):
                invalid = copy.deepcopy(original)
                del invalid[field]
                with self.assertRaises(ValidationError):
                    self.validator.validate(invalid)
        for feature in original["features"]:
            with self.subTest(missing_feature=feature):
                invalid = copy.deepcopy(original)
                del invalid["features"][feature]
                with self.assertRaises(ValidationError):
                    self.validator.validate(invalid)
        for field, value in (("started_at", "yesterday"), ("sequence", -1), ("text", "secret")):
            with self.subTest(field=field):
                invalid_payload = {**original, field: value}
                with self.assertRaises(ValidationError):
                    self.validator.validate(invalid_payload)
        for field, value in (("key_code", 65), ("pause_fraction", 1.1), ("hold_mean_ms", -1)):
            with self.subTest(feature=field):
                invalid = copy.deepcopy(original)
                invalid["features"][field] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(invalid)

    def test_checked_in_samples_match_defaults(self) -> None:
        samples = (ROOT / "sample-data" / "windows.jsonl").read_text(encoding="utf-8")
        self.assertEqual([json.loads(line) for line in samples.splitlines()], generate_windows(49, 10))

    def test_cli_reproducibility_and_refusal_to_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.jsonl"
            second = Path(directory) / "second.jsonl"
            command = [sys.executable, str(ROOT / "scripts" / "generate_sample_windows.py")]
            for output in (first, second):
                result = subprocess.run(command + ["--output", str(output)], capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            original = first.read_bytes()
            result = subprocess.run(command + ["--output", str(first)], capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(first.read_bytes(), original)
            result = subprocess.run(command + ["--count", "0", "--output", str(first)], capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"--count must be positive", result.stderr)
            self.assertEqual(first.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
