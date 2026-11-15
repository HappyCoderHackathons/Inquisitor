import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import mongomock
from flask import Flask

import hosted
import server

ENV = {name: "test-value" for name in hosted.ENV} | {
    "SSOJET_AUTHORITY": "https://sso.example.test", "SSOJET_REDIRECT_URI": "https://app.example.test/auth/callback",
}


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.clock = datetime.now(timezone.utc)
        patcher = patch("hosted.now", lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.database = mongomock.MongoClient(tz_aware=True).db
        app = Flask(__name__, template_folder=str(Path(server.__file__).parent / "templates"))
        with patch.dict(os.environ, ENV):
            hosted.register(app, self.database)
        self.client = app.test_client()

    def sign_in(self, user_id="user-a"):
        self.database.users.insert_one({"_id": user_id, "email": f"{user_id}@example.test"})
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def pair(self):
        self.sign_in()
        code = self.client.post("/api/pairing-codes", json={}).get_json()["code"]
        paired = self.client.post("/api/agent/pair", json={"code": code.lower()})
        self.assertEqual(paired.status_code, 201)
        self.assertEqual(self.client.post("/api/agent/pair", json={"code": code}).status_code, 404)
        body = paired.get_json()
        return body["device_id"], {"Authorization": "Bearer " + body["token"]}

    def test_pairing_requires_sign_in_json_and_live_codes(self):
        self.assertEqual(self.client.post("/api/pairing-codes", json={}).status_code, 401)
        self.sign_in()
        self.assertEqual(self.client.post("/api/pairing-codes", data="{}").status_code, 415)
        code = self.client.post("/api/pairing-codes", json={}).get_json()["code"]
        self.clock += timedelta(minutes=11)
        self.assertEqual(self.client.post("/api/agent/pair", json={"code": code}).status_code, 404)
        self.assertEqual(self.client.post("/api/agent/pair", json={"code": "WRONG123"}).status_code, 404)

    def test_paired_device_belongs_to_code_owner_and_stores_only_token_hash(self):
        device_id, agent = self.pair()
        token = agent["Authorization"].removeprefix("Bearer ")
        device = self.database.devices.find_one({"_id": device_id})
        self.assertEqual(device["user_id"], "user-a")
        self.assertEqual(device["token_hash"], hosted.digest(token))
        self.assertNotIn(token, str(list(self.database.devices.find())))

    def test_partial_configuration_fails_loudly(self):
        with patch.dict(os.environ, {"SSOJET_AUTHORITY": "https://sso.example.test"}, clear=True):
            with self.assertRaises(RuntimeError):
                hosted.configured()
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(hosted.configured())

    def test_me_requires_session(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.sign_in()
        self.assertEqual(self.client.get("/api/me").get_json()["email"], "user-a@example.test")
        self.client.post("/logout")
        self.assertEqual(self.client.get("/api/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
