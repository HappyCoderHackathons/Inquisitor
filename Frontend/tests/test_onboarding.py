import os
import unittest
from datetime import datetime, timezone
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
