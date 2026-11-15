# /// script
# requires-python = ">=3.13"
# dependencies = ["jsonschema[format]>=4.18,<5", "pymongo>=4.19,<5"]
# ///
"""Read-only checks for numeric-only feature-window storage."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "shared"
SQLITE_WINDOW_COLUMNS = {
    "id",
    "user_id",
    "device_id",
    "session_id",
    "sequence",
    "mode",
    "created_at",
    "window_json",
}
MONGODB_WINDOW_FIELDS = {
    "windows": {
        "_id",
        "user_id",
        "device_id",
        "onboarding_id",
        "session_id",
        "sequence",
        "game",
        "usable",
        "synthetic",
        "source",
        "window",
        "created_at",
    },
    "monitoring_windows": {
        "_id",
        "user_id",
        "device_id",
        "session_id",
        "sequence",
        "window",
        "created_at",
        "result",
    },
}
WINDOW_METADATA_FIELDS = {
    "schema_version",
    "device_id",
    "session_id",
    "sequence",
    "mode",
    "started_at",
    "duration_ms",
    "features",
    "mouse_features",
}


class InspectionError(ValueError):
    """Stored feature data or its schema violates the numeric-only contract."""


def load_validators() -> dict[int, Draft202012Validator]:
    validators: dict[int, Draft202012Validator] = {}
    for path in sorted(SCHEMA_DIR.glob("feature-window-v*.schema.json")):
        with path.open(encoding="utf-8") as file:
            schema = json.load(file)
        properties = schema.get("properties")
        version = properties.get("schema_version", {}).get("const") if isinstance(properties, dict) else None
        if type(version) is not int or schema.get("additionalProperties") is not False:
            raise InspectionError(f"{path.name}: schema must be versioned and reject undeclared fields")
        required_fields = {
            "schema_version",
            "device_id",
            "session_id",
            "sequence",
            "mode",
            "started_at",
            "duration_ms",
            "features",
        }
        if (
            not set(properties).issubset(WINDOW_METADATA_FIELDS)
            or not required_fields.issubset(properties)
            or not required_fields.issubset(schema.get("required", []))
        ):
            raise InspectionError(f"{path.name}: window fields differ from the approved metadata and feature fields")

        for field in ("features", "mouse_features"):
            feature_schema = properties.get(field)
            if feature_schema is None:
                continue
            feature_properties = feature_schema.get("properties")
            if feature_schema.get("type") != "object" or feature_schema.get("additionalProperties") is not False:
                raise InspectionError(f"{path.name}: {field} must reject undeclared feature fields")
            if not isinstance(feature_properties, dict) or not feature_properties:
                raise InspectionError(f"{path.name}: {field} must declare its numeric fields")
            if not set(feature_properties).issubset(feature_schema.get("required", [])):
                raise InspectionError(f"{path.name}: {field} must require all declared feature fields")
            for name, definition in feature_properties.items():
                if definition.get("type") not in {"integer", "number"}:
                    raise InspectionError(f"{path.name}: {field}.{name} must be numeric")

        validators[version] = Draft202012Validator(schema, format_checker=FormatChecker())
    if not validators:
        raise InspectionError("No feature-window schemas were found")
    return validators


VALIDATORS = load_validators()


def validate_window(value: object, location: str) -> None:
    if not isinstance(value, dict):
        raise InspectionError(f"{location}: feature window is not an object")
    version = value.get("schema_version")
    validator = VALIDATORS.get(version) if type(version) is int else None
    if validator is None:
        raise InspectionError(f"{location}: unsupported feature-window schema version")
    for error in validator.iter_errors(value):
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise InspectionError(f"{location}: invalid feature window at {path} ({error.validator})")

    schema = validator.schema
    for field in ("features", "mouse_features"):
        feature_schema = schema["properties"].get(field)
        features = value.get(field)
        if feature_schema is None or not isinstance(features, dict):
            continue
        for name in feature_schema["properties"]:
            feature = features.get(name)
            if isinstance(feature, float) and not math.isfinite(feature):
                raise InspectionError(f"{location}: non-finite numeric feature at {field}.{name}")


def inspect_sqlite_database(path: Path) -> int:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        inspected = 0
        for table in tables:
            quoted_table = '"' + table.replace('"', '""') + '"'
            columns = connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
            column_types = {row[1]: str(row[2]).upper() for row in columns}
            if "window_json" not in column_types:
                continue
            feature_fields = {
                name
                for validator in VALIDATORS.values()
                for field in ("features", "mouse_features")
                for name in validator.schema["properties"].get(field, {}).get("properties", {})
            }
            extra_columns = set(column_types) - SQLITE_WINDOW_COLUMNS - feature_fields
            if extra_columns:
                raise InspectionError(
                    f"{path.name}:{table}: unexpected columns: {', '.join(sorted(extra_columns))}"
                )
            if column_types["window_json"] != "TEXT":
                raise InspectionError(f"{path.name}:{table}: window_json must contain serialized JSON")

            stored_feature_columns = [column for column in column_types if column in feature_fields]
            for column in stored_feature_columns:
                if column_types[column] not in {"INTEGER", "REAL", "NUMERIC"}:
                    raise InspectionError(f"{path.name}:{table}: feature column {column} is not numeric")

            selected_columns = ["rowid", "window_json"] + [
                '"' + column.replace('"', '""') + '"' for column in stored_feature_columns
            ]
            query = f"SELECT {', '.join(selected_columns)} FROM {quoted_table}"
            cursor = connection.execute(query)
            try:
                for row in cursor:
                    row_id, raw_window = row[:2]
                    for index, column in enumerate(stored_feature_columns, start=2):
                        feature_value = row[index]
                        if type(feature_value) not in {int, float} or (
                            isinstance(feature_value, float) and not math.isfinite(feature_value)
                        ):
                            raise InspectionError(
                                f"{path.name}:{table} row {row_id}: feature column {column} is not numeric"
                            )
                    try:
                        window = json.loads(raw_window)
                    except (TypeError, json.JSONDecodeError):
                        raise InspectionError(
                            f"{path.name}:{table} row {row_id}: window_json is invalid JSON"
                        ) from None
                    validate_window(window, f"{path.name}:{table} row {row_id}")
                    inspected += 1
            finally:
                cursor.close()
        if inspected == 0:
            raise InspectionError(f"{path.name}: no feature windows were inspected")
        return inspected


def inspect_mongodb_database(database: Database) -> dict[str, int]:
    counts: dict[str, int] = {}
    available = set(database.list_collection_names())
    for collection_name, allowed_fields in MONGODB_WINDOW_FIELDS.items():
        if collection_name not in available:
            continue
        inspected = 0
        for document in database[collection_name].find():
            extra_fields = set(document) - allowed_fields
            if extra_fields:
                raise InspectionError(
                    f"{collection_name} document {document.get('_id')}: "
                    f"unexpected fields: {', '.join(sorted(extra_fields))}"
                )
            if "window" not in document:
                raise InspectionError(f"{collection_name} document {document.get('_id')}: missing feature window")
            validate_window(document["window"], f"{collection_name} document {document.get('_id')}")
            inspected += 1
        counts[collection_name] = inspected
    if not counts or sum(counts.values()) == 0:
        raise InspectionError("No feature windows were inspected in the MongoDB database")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="inspect a local SQLite database; may be supplied more than once",
    )
    parser.add_argument(
        "--mongodb",
        action="store_true",
        help="inspect Atlas using MONGODB_URI and MONGODB_DB from the environment",
    )
    args = parser.parse_args()
    if not args.sqlite and not args.mongodb:
        parser.error("specify --sqlite PATH and/or --mongodb")

    try:
        for path in args.sqlite:
            count = inspect_sqlite_database(path)
            print(f"{path}: checked {count} feature windows")
        if args.mongodb:
            uri = os.environ.get("MONGODB_URI")
            database_name = os.environ.get("MONGODB_DB")
            if not uri or not database_name:
                raise InspectionError("--mongodb requires MONGODB_URI and MONGODB_DB")
            with MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000) as client:
                client.admin.command("ping")
                counts = inspect_mongodb_database(client[database_name])
            summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
            print(f"MongoDB: checked feature windows ({summary})")
    except (InspectionError, OSError, PyMongoError, sqlite3.Error) as error:
        print(f"Feature storage inspection failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
