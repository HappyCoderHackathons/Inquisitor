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


def window(device_id, session_id, sequence, key_count=40):
    started = datetime.now(timezone.utc) - timedelta(minutes=1)
    return {
        "schema_version": 1, "device_id": device_id, "session_id": session_id, "sequence": sequence,
        "mode": "enrollment", "started_at": started.isoformat(), "duration_ms": 10_000,
        "features": {
            "key_count": key_count, "presses_per_second": key_count / 10, "hold_mean_ms": 100.0, "hold_std_ms": 20.0,
            "press_interval_mean_ms": 250.0, "press_interval_std_ms": 50.0, "press_interval_p90_ms": 300.0,
            "pause_count": 0, "pause_fraction": 0.0, "burst_count": 1 if key_count else 0,
            "press_overlap_fraction": 0.2,
        },
    }


class SyncThread:
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        self.target()


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.clock = datetime.now(timezone.utc)
        for target, value in (("hosted.now", lambda: self.clock), ("hosted.threading.Thread", SyncThread)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.database = mongomock.MongoClient(tz_aware=True).db
        self.trained = []
        self.training_error = None

        def trainer(user_id, device_id, windows, validation_sessions):
            self.trained.append((user_id, device_id, windows, validation_sessions))
            if self.training_error:
                raise self.training_error
            return "model-1"

        app = Flask(__name__, template_folder=str(Path(server.__file__).parent / "templates"))
        with patch.dict(os.environ, ENV):
            hosted.register(app, self.database, trainer, server.onboarding_window_error)
        self.client = app.test_client()

    def sign_in(self, user_id="user-a"):
        self.database.users.insert_one({"_id": user_id, "email": f"{user_id}@example.test"})
        sid = f"session-{user_id}"
        self.database.sessions.insert_one({"_id": hosted.digest(sid), "user_id": user_id,
                                           "expires_at": self.clock + hosted.SESSION_LIFETIME})
        with self.client.session_transaction() as session:
            session["sid"] = sid

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

    def play(self, game, windows, device_id, agent):
        session_id = self.client.post("/api/onboarding/rounds", json={"game": game}).get_json()["session_id"]
        self.assertEqual(self.client.get("/api/agent/onboarding", headers=agent).get_json()["session_id"], session_id)
        for sequence, key_count in enumerate(windows):
            upload = self.client.post("/api/agent/windows", headers=agent,
                                      json=window(device_id, session_id, sequence, key_count))
            self.assertEqual(upload.status_code, 201, upload.get_json())
        self.clock += timedelta(seconds=61)
        return session_id, self.client.post("/api/onboarding/rounds/current/finish", json={}).get_json()

    def test_agent_windows_are_scoped_to_device_and_active_round(self):
        device_id, agent = self.pair()
        self.assertEqual(self.client.get("/api/agent/onboarding", headers={"Authorization": "Bearer nope"}).status_code,
                         401)
        orphan = window(device_id, "no-round", 0)
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=orphan).status_code, 409)
        session_id = self.client.post("/api/onboarding/rounds", json={"game": "typing"}).get_json()["session_id"]
        other_device = window("other-device", session_id, 0)
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=other_device).status_code, 422)
        forged = window(device_id, session_id, 0) | {"key": "a"}
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=forged).status_code, 422)
        valid = window(device_id, session_id, 0)
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=valid).status_code, 201)
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=valid).status_code, 409)
        self.sign_in("user-b")
        self.assertEqual(self.client.get("/api/onboarding").status_code, 404)

    def test_rounds_credit_server_time_and_pick_latest_round_per_game_for_calibration(self):
        device_id, agent = self.pair()
        first, _ = self.play("typing", [40] * 4, device_id, agent)
        latest, status = self.play("typing", [40] * 4, device_id, agent)
        self.assertEqual(status["seconds"]["typing"], 120)
        self.assertEqual(status["validation_sessions"], [latest])
        self.assertEqual((status["training_windows"], status["validation_windows"]), (4, 4))
        self.client.post("/api/onboarding/rounds", json={"game": "aim"})
        self.clock += timedelta(seconds=30)
        cancelled = self.client.post("/api/onboarding/rounds/current/cancel", json={}).get_json()
        self.assertEqual(cancelled["seconds"]["aim"], 0)
        self.assertFalse(cancelled["ready_to_train"])

    def test_ten_minutes_of_games_trains_and_activates_model(self):
        device_id, agent = self.pair()
        latest = {}
        for index in range(3):
            if index < 2:
                self.play("aim", [0], device_id, agent)
            for game in ("tetris", "typing", "arena"):
                latest[game], status = self.play(game, [40] * 4, device_id, agent)
                self.assertEqual(status["state"], "collecting")
        cancelled = self.client.post("/api/onboarding/rounds", json={"game": "typing"}).get_json()["session_id"]
        self.client.post("/api/agent/windows", headers=agent, json=window(device_id, cancelled, 0))
        self.client.post("/api/onboarding/rounds/current/cancel", json={})
        self.assertEqual(self.trained, [])
        self.clock += timedelta(seconds=1)
        _, status = self.play("aim", [0], device_id, agent)
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["model_id"], "model-1")
        user_id, trained_device, windows, validation = self.trained[0]
        self.assertEqual((user_id, trained_device), ("user-a", device_id))
        self.assertEqual(sorted(validation), sorted(latest.values()))
        self.assertEqual(len(windows), 36)
        self.assertNotIn(cancelled, {item["session_id"] for item in windows})
        self.assertTrue(all(item["features"]["key_count"] >= 30 for item in windows))
        self.assertEqual(self.client.post("/api/onboarding/rounds", json={"game": "aim"}).status_code, 409)

    def test_training_failure_is_reported(self):
        self.training_error = RuntimeError("model service returned 422: all training features are constant")
        device_id, agent = self.pair()
        for _ in range(3):
            for game in ("aim", "tetris", "typing", "arena"):
                _, status = self.play(game, [40] * 4, device_id, agent)
        self.assertEqual(status["state"], "failed")
        self.assertIn("all training features are constant", status["error"])
        self.assertEqual(len(self.trained), 1)

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
        cookie = self.client.get_cookie("session").value
        self.assertEqual(self.client.post("/logout").status_code, 302)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.client.set_cookie("session", cookie)
        self.assertEqual(self.client.get("/api/me").status_code, 401, "logout must revoke the server-side session")
        self.sign_in("user-b")
        self.clock += hosted.SESSION_LIFETIME
        self.assertEqual(self.client.get("/api/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
