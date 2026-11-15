from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.inspect_feature_storage import (
    InspectionError,
    inspect_mongodb_database,
    inspect_sqlite_database,
)


class FakeCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def find(self) -> list[dict[str, object]]:
        return self.documents


class FakeDatabase:
    def __init__(self, collections: dict[str, list[dict[str, object]]]) -> None:
        self.collections = {name: FakeCollection(documents) for name, documents in collections.items()}

    def list_collection_names(self) -> list[str]:
        return list(self.collections)

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections[name]


class InspectFeatureStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database_path = Path(self.temporary.name) / "features.sqlite3"
        fixture_path = Path(__file__).resolve().parents[1] / "sample-data" / "windows.jsonl"
        self.window = json.loads(fixture_path.read_text(encoding="utf-8").splitlines()[0])

    def create_sqlite_database(self, *, extra_column: bool = False) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            extra = ", typed_text TEXT" if extra_column else ""
            connection.execute(
                f"""CREATE TABLE feature_windows (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    window_json TEXT NOT NULL{extra}
                )"""
            )
            connection.execute(
                "INSERT INTO feature_windows (created_at, device_id, session_id, sequence, mode, window_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "2000-01-01T00:00:00Z",
                    self.window["device_id"],
                    self.window["session_id"],
                    self.window["sequence"],
                    self.window["mode"],
                    json.dumps(self.window),
                ),
            )
            connection.commit()

    def test_accepts_numeric_windows_in_sqlite_and_mongodb(self) -> None:
        self.create_sqlite_database()
        self.assertEqual(inspect_sqlite_database(self.database_path), 1)

        database = FakeDatabase({"windows": [{"_id": "window-1", "window": self.window}]})
        self.assertEqual(inspect_mongodb_database(database), {"windows": 1})

    def test_rejects_non_numeric_features_and_unexpected_input_columns(self) -> None:
        self.create_sqlite_database()
        self.window["features"]["key_count"] = "44"
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("UPDATE feature_windows SET window_json = ?", (json.dumps(self.window),))
            connection.commit()
        with self.assertRaisesRegex(InspectionError, "invalid feature window"):
            inspect_sqlite_database(self.database_path)

        self.database_path.unlink()
        self.create_sqlite_database(extra_column=True)
        with self.assertRaisesRegex(InspectionError, "unexpected columns"):
            inspect_sqlite_database(self.database_path)

    def test_rejects_non_numeric_sqlite_feature_columns(self) -> None:
        self.create_sqlite_database()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("ALTER TABLE feature_windows ADD COLUMN key_count TEXT")
            connection.commit()
        with self.assertRaisesRegex(InspectionError, "feature column key_count is not numeric"):
            inspect_sqlite_database(self.database_path)

    def test_rejects_unexpected_mongodb_window_fields(self) -> None:
        database = FakeDatabase({
            "windows": [{"_id": "window-1", "window": self.window, "typed_text": "not displayed"}],
        })
        with self.assertRaisesRegex(InspectionError, "unexpected fields"):
            inspect_mongodb_database(database)


if __name__ == "__main__":
    unittest.main()
