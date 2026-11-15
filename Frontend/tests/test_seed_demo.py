import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import mongomock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed_demo import (  # noqa: E402
    SYNTHETIC_SOURCE,
    build_demo_documents,
    read_sample_windows,
)


class DemoSeedTests(unittest.TestCase):
    def test_synthetic_windows_are_labeled_and_excluded_from_training(self):
        sample_path = ROOT / "sample-data" / "windows.jsonl"
        windows = read_sample_windows(sample_path)
        documents = build_demo_documents(
            windows,
            user_id="better-auth:demo-user",
            device_id="demo-device",
            onboarding_id="demo-onboarding",
            session_id="demo-session",
            game="typing",
        )
        database = mongomock.MongoClient().db
        database.windows.insert_many(documents)

        stored = database.windows.find_one({"sequence": 0})
        self.assertEqual(stored["source"], SYNTHETIC_SOURCE)
        self.assertTrue(stored["synthetic"])
        self.assertFalse(stored["usable"])
        self.assertEqual(stored["window"]["device_id"], "demo-device")
        self.assertEqual(stored["window"]["session_id"], "demo-session")
        self.assertEqual(stored["window"]["sequence"], 0)
        self.assertEqual(stored["window"]["features"], windows[0]["features"])
        self.assertEqual(
            list(database.windows.find({
                "onboarding_id": "demo-onboarding",
                "usable": True,
                "session_id": {"$in": ["demo-session"]},
            })),
            [],
        )

    def test_sample_loader_rejects_fields_outside_the_shared_schema(self):
        window = read_sample_windows(ROOT / "sample-data" / "windows.jsonl")[0]
        window["private_field"] = "must not be seeded"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.jsonl"
            path.write_text(json.dumps(window) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid feature window"):
                read_sample_windows(path)


if __name__ == "__main__":
    unittest.main()
