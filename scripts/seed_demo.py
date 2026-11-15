# /// script
# requires-python = ">=3.13"
# dependencies = ["jsonschema[format]>=4.18,<5", "pymongo>=4.19,<5"]
# ///
"""Create a demo account/device and store explicitly non-training synthetic windows."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "shared" / "feature-window-v1.schema.json"
DEFAULT_WINDOWS = ROOT / "sample-data" / "windows.jsonl"
SYNTHETIC_SOURCE = "synthetic_sample_data"
JsonObject = dict[str, object]


class HostedAPI:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        self.base_url = base_url.rstrip("/")
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def request(self, method: str, path: str, body: JsonObject | None = None) -> JsonObject:
        headers = {"Accept": "application/json", "Origin": self.origin}
        data = None
        if body is not None:
            data = json.dumps(body, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=headers
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            error.close()
            raise RuntimeError(f"{method} {path} returned HTTP {error.code}") from None
        except urllib.error.URLError as error:
            raise RuntimeError(f"Could not reach the hosted app: {error.reason}") from None
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError(f"{method} {path} returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise TypeError(f"{method} {path} returned an invalid response")
        return cast(JsonObject, payload)

    def get(self, path: str) -> JsonObject:
        return self.request("GET", path)

    def post(self, path: str, body: JsonObject) -> JsonObject:
        return self.request("POST", path, body)


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("--url must be an origin without credentials, path, query, or fragment")
    is_loopback = parsed.hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            is_loopback = False
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
        raise ValueError("use HTTPS except for a loopback development URL")
    return value.rstrip("/")


def read_sample_windows(path: Path) -> list[JsonObject]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    windows: list[JsonObject] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSON in {path} at line {line_number}") from error
        if not isinstance(value, dict) or next(validator.iter_errors(value), None) is not None:
            raise ValueError(f"Invalid feature window in {path} at line {line_number}")
        windows.append(cast(JsonObject, value))
    if not windows:
        raise ValueError(f"No sample windows found in {path}")
    return windows


def required_string(value: JsonObject, key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise RuntimeError(f"Hosted app response is missing {key}")
    return result


def build_demo_documents(
    windows: list[JsonObject],
    *,
    user_id: str,
    device_id: str,
    onboarding_id: str,
    session_id: str,
    game: str,
) -> list[JsonObject]:
    from datetime import UTC, datetime, timedelta

    created_at = datetime.now(UTC)
    documents: list[JsonObject] = []
    for sequence, original in enumerate(windows):
        duration_ms = cast(int, original["duration_ms"])
        started_at = (created_at - timedelta(milliseconds=duration_ms)).isoformat().replace(
            "+00:00", "Z"
        )
        window = {
            **original,
            "device_id": device_id,
            "session_id": session_id,
            "sequence": sequence,
            "mode": "enrollment",
            "started_at": started_at,
        }
        documents.append({
            "user_id": user_id,
            "device_id": device_id,
            "onboarding_id": onboarding_id,
            "session_id": session_id,
            "sequence": sequence,
            "game": game,
            "usable": False,
            "synthetic": True,
            "source": SYNTHETIC_SOURCE,
            "window": window,
            "created_at": created_at,
        })
    return documents


def seed(
    api: HostedAPI,
    database: Database,
    *,
    email: str,
    password: str,
    name: str,
    device_name: str,
    windows: list[JsonObject],
) -> tuple[str, str, str]:
    api.post("/api/auth/sign-up/email", {"email": email, "password": password, "name": name})
    user_id = required_string(api.get("/api/me"), "user_id")

    pairing = api.post("/api/pairing-codes", {})
    code = required_string(pairing, "code")
    paired = api.post("/api/agent/pair", {"code": code, "name": device_name})
    device_id = required_string(paired, "device_id")
    token = required_string(paired, "token")

    onboarding = database.onboarding.find_one({"user_id": user_id, "device_id": device_id})
    if onboarding is None:
        raise RuntimeError("Pairing did not create an onboarding record")
    onboarding_id = cast(str, onboarding["_id"])

    session = api.post("/api/onboarding/rounds", {"game": "typing"})
    session_id = required_string(session, "session_id")
    active_round = database.rounds.find_one({
        "_id": session_id,
        "onboarding_id": onboarding_id,
        "device_id": device_id,
        "status": "active",
    })
    if active_round is None:
        raise RuntimeError("Hosted app did not create the requested enrollment session")

    documents = build_demo_documents(
        windows,
        user_id=user_id,
        device_id=device_id,
        onboarding_id=onboarding_id,
        session_id=session_id,
        game=cast(str, active_round["game"]),
    )
    try:
        database.windows.insert_many(documents)
    except PyMongoError as error:
        try:
            api.post("/api/onboarding/rounds/current/cancel", {})
        except (RuntimeError, TypeError) as cancel_error:
            raise RuntimeError(
                f"Could not store demo windows ({error}); session cancellation also failed "
                f"({cancel_error})"
            ) from error
        raise RuntimeError(f"Could not store demo windows: {error}") from error
    api.post("/api/onboarding/rounds/current/cancel", {})
    return user_id, device_id, token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="hosted app origin; HTTPS required except loopback")
    parser.add_argument("--email", required=True, help="email for a new demo account")
    parser.add_argument("--name", default="Inquisitor Demo", help="demo account display name")
    parser.add_argument("--device-name", default="Demo Device")
    parser.add_argument("--windows", type=Path, default=DEFAULT_WINDOWS)
    parser.add_argument("--confirm", action="store_true", required=True, help="confirm creation of demo data")
    args = parser.parse_args()

    try:
        base_url = validate_base_url(args.url)
        windows = read_sample_windows(args.windows)
        password = os.environ.get("INQUISITOR_DEMO_PASSWORD") or getpass.getpass(
            "New demo account password: "
        )
        if not password:
            raise ValueError("a non-empty demo account password is required")
        mongo_uri = os.environ.get("MONGODB_URI")
        database_name = os.environ.get("MONGODB_DB")
        if not mongo_uri or not database_name:
            raise ValueError("MONGODB_URI and MONGODB_DB must be set on the hosted-app host")
        with MongoClient(mongo_uri, tz_aware=True, serverSelectionTimeoutMS=5000) as client:
            client.admin.command("ping")
            user_id, device_id, token = seed(
                HostedAPI(base_url),
                client[database_name],
                email=args.email,
                password=password,
                name=args.name,
                device_name=args.device_name,
                windows=windows,
            )
    except (OSError, PyMongoError, RuntimeError, TypeError, ValueError) as error:
        sys.exit(f"Demo seeding failed: {error}")

    print(f"Created demo account: {args.email} ({user_id})")
    print(f"Paired device ID: {device_id}")
    print(f"Device token (save securely; shown only once): {token}")
    print(
        f"Stored {len(windows)} windows as {SYNTHETIC_SOURCE}; "
        "they are excluded from enrollment progress and model training."
    )


if __name__ == "__main__":
    main()
