import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import mongomock
from flask import Flask

import hosted
import server

ENV = {name: "test-value" for name in hosted.ENV} | {
    "BETTER_AUTH_URL": "https://app.example.test",
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
        # MongoDB stores datetimes to the millisecond, so keep the test clock comparable after a round trip.
        self.clock = datetime.now(timezone.utc).replace(microsecond=0)
        for target, value in (("hosted.now", lambda: self.clock), ("hosted.threading.Thread", SyncThread)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.database = mongomock.MongoClient(tz_aware=True).db
        self.trained = []
        self.scored = []
        self.training_error = None
        self.auth_sessions = {}
        patcher = patch("hosted.requests.request", side_effect=self.auth_request)
        patcher.start()
        self.addCleanup(patcher.stop)

        def trainer(user_id, device_id, windows, validation_sessions):
            self.trained.append((user_id, device_id, windows, validation_sessions))
            if self.training_error:
                raise self.training_error
            return "model-1"

        app = Flask(__name__, template_folder=str(Path(server.__file__).parent / "templates"))
        with patch.dict(os.environ, ENV):
            hosted.register(app, self.database, trainer, self.score, server.onboarding_window_error)
        self.client = app.test_client()

    def score(self, user_id, device_id, value):
        self.scored.append((user_id, device_id, value["sequence"]))
        return {"model_id": "model-1", "state": "NORMAL", "score": -0.4, "threshold": 0.1, "score_delta": -0.5}

    def sign_in(self, user_id="user-a"):
        sid = f"session-{user_id}"
        self.auth_sessions[sid] = (user_id, self.clock + timedelta(hours=12))
        self.client.set_cookie("better-auth.session_token", sid)

    def auth_request(self, method, url, *, headers, data, timeout, allow_redirects):
        sid = headers.get("Cookie", "").removeprefix("better-auth.session_token=")
        record = self.auth_sessions.get(sid)
        result = None
        if url.endswith("/sign-out"):
            self.auth_sessions.pop(sid, None)
        elif record and record[1] > self.clock:
            result = {"user": {"id": record[0], "email": f"{record[0]}@example.test"}}
        response = Mock(status_code=200, content=b"{}", headers={"Content-Type": "application/json"})
        response.json.return_value = result
        response.raw.headers.getlist.return_value = []
        return response

    def pair(self):
        self.sign_in()
        code = self.client.post("/api/pairing-codes", json={}).get_json()["code"]
        paired = self.client.post("/api/agent/pair", json={"code": code.lower(), "name": "keyboard"})
        self.assertEqual(paired.status_code, 201)
        self.assertEqual(
            self.client.post("/api/agent/pair", json={"code": code, "name": "keyboard"}).status_code, 404
        )
        body = paired.get_json()
        return body["device_id"], {"Authorization": "Bearer " + body["token"]}

    def test_pairing_requires_sign_in_json_and_live_codes(self):
        self.assertEqual(self.client.post("/api/pairing-codes", json={}).status_code, 401)
        self.sign_in()
        self.assertEqual(self.client.post("/api/pairing-codes", data="{}").status_code, 415)
        code = self.client.post("/api/pairing-codes", json={}).get_json()["code"]
        self.clock += timedelta(minutes=11)
        self.assertEqual(
            self.client.post("/api/agent/pair", json={"code": code, "name": "keyboard"}).status_code, 404
        )
        self.assertEqual(
            self.client.post("/api/agent/pair", json={"code": "WRONG123", "name": "keyboard"}).status_code, 404
        )

    def test_pairing_rejects_empty_or_unbounded_device_names(self):
        self.sign_in()
        code = self.client.post("/api/pairing-codes", json={}).get_json()["code"]
        empty_name = self.client.post("/api/agent/pair", json={"code": code, "name": ""})
        self.assertEqual(empty_name.status_code, 422)
        long_name = self.client.post("/api/agent/pair", json={"code": code, "name": "x" * 65})
        self.assertEqual(long_name.status_code, 422)
        paired = self.client.post("/api/agent/pair", json={"code": code, "name": "mouse"})
        self.assertEqual(paired.status_code, 201)

    def test_paired_device_belongs_to_code_owner_and_stores_only_token_hash(self):
        device_id, agent = self.pair()
        token = agent["Authorization"].removeprefix("Bearer ")
        device = self.database.devices.find_one({"_id": device_id})
        self.assertEqual(device["user_id"], "better-auth:user-a")
        self.assertEqual(device["name"], "keyboard")
        self.assertEqual(device["pairing_state"], "paired")
        self.assertIsNone(device["last_heartbeat_at"])
        self.assertEqual(device["token_hash"], hosted.digest(token))
        self.assertNotIn(token, str(list(self.database.devices.find())))
        self.client.get("/api/agent/onboarding", headers=agent)
        self.assertEqual(
            self.database.devices.find_one({"_id": device_id})["last_heartbeat_at"],
            self.clock,
        )

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
        self.assertTrue(status["ready_to_train"])
        self.assertEqual(self.trained, [], "training waits for the user")
        status = self.client.post("/api/onboarding/train", json={}).get_json()
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["model_id"], "model-1")
        user_id, trained_device, windows, validation = self.trained[0]
        self.assertEqual((user_id, trained_device), ("better-auth:user-a", device_id))
        self.assertEqual(sorted(validation), sorted(latest.values()))
        self.assertEqual(len(windows), 36)
        self.assertNotIn(cancelled, {item["session_id"] for item in windows})
        self.assertTrue(all(item["features"]["key_count"] >= 30 for item in windows))
        self.assertEqual(self.client.post("/api/onboarding/rounds", json={"game": "aim"}).status_code, 409)
        enrollment = window(device_id, "after-ready", 0)
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=enrollment).status_code, 409)
        monitoring = window(device_id, "monitor-1", 0) | {"mode": "monitoring"}
        scored = self.client.post("/api/agent/windows", headers=agent, json=monitoring)
        self.assertEqual((scored.status_code, scored.get_json()["state"]), (201, "NORMAL"))
        self.assertEqual(self.scored, [("better-auth:user-a", device_id, 0)])
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=monitoring).status_code, 409)
        stored = self.database.monitoring_windows.find_one({"session_id": "monitor-1"})
        self.assertEqual(stored["result"]["state"], "NORMAL")

    def test_monitoring_waits_for_a_trained_model(self):
        device_id, agent = self.pair()
        monitoring = window(device_id, "monitor-1", 0) | {"mode": "monitoring"}
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=monitoring).status_code, 409)
        self.assertEqual(self.scored, [])

    def test_training_failure_is_reported(self):
        self.training_error = RuntimeError("model service returned 422: all training features are constant")
        device_id, agent = self.pair()
        self.assertEqual(self.client.post("/api/onboarding/train", json={}).status_code, 409)
        for _ in range(3):
            for game in ("aim", "tetris", "typing", "arena"):
                self.play(game, [40] * 4, device_id, agent)
        status = self.client.post("/api/onboarding/train", json={}).get_json()
        self.assertEqual(status["state"], "failed")
        self.assertIn("all training features are constant", status["error"])
        self.assertEqual(len(self.trained), 1)

    def test_pairing_and_uploads_are_rate_limited(self):
        statuses = [
            self.client.post("/api/agent/pair", json={"code": "WRONG123", "name": "keyboard"}).status_code
            for _ in range(11)
        ]
        self.assertEqual(statuses, [404] * 10 + [429])
        self.clock += hosted.PAIR_LIMIT[1]
        device_id, agent = self.pair()
        session_id = self.client.post("/api/onboarding/rounds", json={"game": "typing"}).get_json()["session_id"]
        uploads = [self.client.post("/api/agent/windows", headers=agent, json=window(device_id, session_id, n))
                   for n in range(61)]
        self.assertEqual(uploads[59].status_code, 201)
        self.assertEqual(uploads[60].status_code, 429)

    def test_partial_configuration_fails_loudly(self):
        with patch.dict(os.environ, {"BETTER_AUTH_URL": "https://app.example.test"}, clear=True):
            with self.assertRaises(RuntimeError):
                hosted.configured()
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(hosted.configured())

    def test_me_requires_session(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.sign_in()
        self.assertEqual(self.client.get("/api/me").get_json()["email"], "user-a@example.test")
        user = self.database.users.find_one({"_id": "better-auth:user-a"})
        self.assertEqual(user["privacy_preferences"], {
            "keyboard_collection": False, "mouse_collection": False, "model_training": False,
        })
        self.assertIn("consent_records", self.database.list_collection_names())
        cookie = self.client.get_cookie("better-auth.session_token").value
        self.assertEqual(self.client.post("/api/auth/sign-out", json={}).status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.client.set_cookie("better-auth.session_token", cookie)
        self.assertEqual(self.client.get("/api/me").status_code, 401, "logout must revoke the server-side session")
        self.sign_in("user-b")
        self.clock += timedelta(hours=12)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_auth_proxy_rejects_cross_site_posts_and_unknown_endpoints(self):
        self.assertEqual(self.client.post("/api/auth/sign-in/email", json={},
                         headers={"Origin": "https://attacker.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/sign-in/email", data="{}").status_code, 415)
        self.assertEqual(self.client.post("/api/auth/delete-user", json={}).status_code, 404)
        self.assertEqual(self.client.get("/auth/callback").status_code, 404)

    def test_auth_service_failure_does_not_authorize_requests(self):
        self.sign_in()
        with patch("hosted.requests.request", side_effect=hosted.requests.ConnectionError):
            self.assertEqual(self.client.get("/api/me").status_code, 503)
            self.assertEqual(self.client.post("/api/auth/sign-in/email", json={}).status_code, 503)
        upstream = Mock(status_code=200)
        upstream.json.return_value = {"user": {"id": "", "email": "forged@example.test"}}
        with patch("hosted.requests.request", return_value=upstream):
            self.assertEqual(self.client.get("/api/me").status_code, 503)

    def test_auth_proxy_preserves_all_session_cookies(self):
        upstream = Mock(status_code=200, content=b"{}", headers={"Content-Type": "application/json"})
        upstream.raw.headers.getlist.return_value = ["first=one; HttpOnly; Path=/", "second=two; Path=/"]
        with patch("hosted.requests.request", return_value=upstream):
            response = self.client.post("/api/auth/sign-in/email", json={"email": "user@example.test"})
        self.assertEqual(response.headers.getlist("Set-Cookie"), upstream.raw.headers.getlist.return_value)
        self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
