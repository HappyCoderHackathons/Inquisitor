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
        self.score_error = None
        self.deleted = []
        self.delete_error = None
        self.auth_sessions = {}
        patcher = patch("hosted.requests.request", side_effect=self.auth_request)
        patcher.start()
        self.addCleanup(patcher.stop)

        def trainer(user_id, device_id, windows, validation_sessions):
            self.trained.append((user_id, device_id, windows, validation_sessions))
            if self.training_error:
                raise self.training_error
            return "model-1"

        def deleter(user_id, device_id):
            if self.delete_error:
                raise self.delete_error
            self.deleted.append((user_id, device_id))

        self.trainer = trainer
        self.deleter = deleter
        app = Flask(__name__, template_folder=str(Path(server.__file__).parent / "templates"))
        with patch.dict(os.environ, ENV):
            hosted.register(app, self.database, self.trainer, self.score, server.onboarding_window_error, self.deleter)
        self.client = app.test_client()

    def score(self, user_id, device_id, value):
        if self.score_error:
            raise self.score_error
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
        expected_heartbeat = self.clock.replace(microsecond=(self.clock.microsecond // 1000) * 1000)
        self.assertEqual(
            self.database.devices.find_one({"_id": device_id})["last_heartbeat_at"],
            expected_heartbeat,
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
        session = self.database.rounds.find_one({"_id": session_id})
        self.assertEqual(session["device_id"], device_id)
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["valid_window_count"], 1)
        self.sign_in("user-b")
        self.assertEqual(self.client.get("/api/onboarding").status_code, 404)

    def test_legacy_round_documents_are_migrated_to_enrollment_sessions(self):
        device_id, _ = self.pair()
        onboarding = self.database.onboarding.find_one({"device_id": device_id})
        self.database.rounds.insert_one({
            "_id": "legacy-session",
            "onboarding_id": onboarding["_id"],
            "game": "typing",
            "started_at": self.clock - timedelta(seconds=30),
            "ended_at": self.clock,
            "credited_ms": 30_000,
            "usable_windows": 5,
        })

        with patch.dict(os.environ, ENV):
            hosted.register(
                Flask(__name__), self.database, self.trainer, self.score, server.onboarding_window_error, self.deleter,
            )

        session = self.database.rounds.find_one({"_id": "legacy-session"})
        self.assertEqual(session["device_id"], device_id)
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["valid_window_count"], 5)

    def test_rounds_credit_server_time_and_pick_latest_round_per_game_for_calibration(self):
        device_id, agent = self.pair()
        first, _ = self.play("typing", [40] * 4, device_id, agent)
        latest, status = self.play("typing", [40] * 4, device_id, agent)
        first_session = self.database.rounds.find_one({"_id": first})
        self.assertEqual(first_session["device_id"], device_id)
        self.assertEqual(first_session["status"], "completed")
        self.assertEqual(first_session["valid_window_count"], 4)
        self.assertEqual(status["seconds"]["typing"], 120)
        self.assertEqual(status["validation_sessions"], [latest])
        self.assertEqual((status["training_windows"], status["validation_windows"]), (4, 4))
        cancelled_session_id = self.client.post("/api/onboarding/rounds", json={"game": "aim"}).get_json()["session_id"]
        self.clock += timedelta(seconds=30)
        cancelled = self.client.post("/api/onboarding/rounds/current/cancel", json={}).get_json()
        self.assertEqual(cancelled["seconds"]["aim"], 0)
        self.assertFalse(cancelled["ready_to_train"])
        cancelled_session = self.database.rounds.find_one({"_id": cancelled_session_id})
        self.assertEqual(cancelled_session["status"], "cancelled")
        self.assertEqual(cancelled_session["valid_window_count"], 0)

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

    def test_scoring_failure_removes_the_window_so_the_agent_can_retry(self):
        device_id, agent = self.pair()
        self.database.onboarding.update_one({"device_id": device_id}, {"$set": {"state": "ready", "model_id": "model-1"}})
        monitoring = window(device_id, "monitor-1", 0) | {"mode": "monitoring"}
        self.score_error = RuntimeError("model service returned 503")
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=monitoring).status_code, 502)
        self.assertEqual(self.database.monitoring_windows.count_documents({}), 0)
        self.score_error = None
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=monitoring).status_code, 201)
        self.assertEqual(self.database.inference_results.count_documents({"device_id": device_id}), 1)

    def test_revoking_a_device_is_owner_only_and_blocks_its_token(self):
        device_id, agent = self.pair()
        self.sign_in("user-b")
        self.assertEqual(self.client.post(f"/api/devices/{device_id}/revoke", json={}).status_code, 404)
        self.assertEqual(self.client.get("/api/agent/onboarding", headers=agent).status_code, 200)
        self.sign_in("user-a")
        self.assertEqual(self.client.post(f"/api/devices/{device_id}/revoke", data="{}").status_code, 415)
        self.assertEqual(self.client.post(f"/api/devices/{device_id}/revoke", json={}).status_code, 200)
        self.assertEqual(self.database.devices.find_one({"_id": device_id})["pairing_state"], "revoked")
        self.assertEqual(self.client.get("/api/agent/onboarding", headers=agent).status_code, 401)
        self.assertEqual(self.client.post(f"/api/devices/{device_id}/revoke", json={}).status_code, 404)

    def test_delete_my_data_clears_atlas_rows_revokes_devices_and_calls_model_service(self):
        device_id, agent = self.pair()
        session_id = self.client.post("/api/onboarding/rounds", json={"game": "typing"}).get_json()["session_id"]
        self.client.post("/api/agent/windows", headers=agent, json=window(device_id, session_id, 0))
        user_id = "better-auth:user-a"
        for collection in ("model_versions", "inference_results", "alerts", "monitoring_windows"):
            self.database[collection].insert_many([
                {"user_id": user_id, "device_id": device_id}, {"user_id": "other-user", "device_id": "other-device"},
            ])
        self.delete_error = RuntimeError("model service returned 503")
        self.assertEqual(self.client.delete("/api/me/data", json={}).status_code, 502)
        self.assertEqual(self.database.windows.count_documents({"user_id": user_id}), 1)
        self.delete_error = None
        response = self.client.delete("/api/me/data", json={})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.deleted, [(user_id, device_id)])
        for collection in ("windows", "model_versions", "inference_results", "alerts", "monitoring_windows",
                           "onboarding"):
            self.assertEqual(self.database[collection].count_documents({"user_id": user_id}), 0, collection)
        self.assertEqual(self.database.rounds.count_documents({}), 0)
        self.assertEqual(self.database.alerts.count_documents({"user_id": "other-user"}), 1)
        self.assertEqual(self.client.get("/api/agent/onboarding", headers=agent).status_code, 401)
        self.assertEqual(self.database.devices.find_one({"_id": device_id})["pairing_state"], "revoked")
        self.assertEqual(self.client.get("/api/onboarding").status_code, 404)

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

    def test_login_and_register_pages_guard_the_dashboard(self):
        anonymous = self.client.get("/dashboard")
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login?next=/dashboard", anonymous.headers["Location"])
        login = self.client.get("/login?next=/dashboard")
        self.assertIn("/onboarding?next=/dashboard", login.headers["Location"])
        page = self.client.get("/onboarding?next=/dashboard").get_data(as_text=True)
        self.assertIn("<h1>Sign in</h1>", page)
        self.assertIn("novalidate", page)
        self.assertIn('id="emailError"', page)
        self.assertIn('id="passwordError"', page)
        self.assertIn('data-next="/dashboard"', page)
        self.assertIn('id="authNameLabel" class="mode-label" hidden', page)
        register = self.client.get("/register?next=/dashboard", follow_redirects=True).get_data(as_text=True)
        self.assertIn("<h1>Create an account</h1>", register)
        self.assertIn('id="nameError"', register)
        script = Path(__file__).parents[1].joinpath("frontend/onboarding.ts").read_text(encoding="utf-8")
        self.assertIn("Use at least 8 characters.", script)
        self.assertIn("Enter an email address.", script)
        self.assertIn("Enter your name.", script)
        rejected = self.client.get("/login?next=https://attacker.example")
        self.assertNotIn("attacker", rejected.headers["Location"])
        self.assertIn("/onboarding", rejected.headers["Location"])
        self.sign_in()
        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Your dashboard", dashboard.get_data(as_text=True))

    def test_enrollment_wizard_explains_collection_and_keeps_practice_text_local(self):
        unsigned = self.client.get("/onboarding").get_data(as_text=True)
        self.assertNotIn("practiceText", unsigned)
        self.sign_in()
        page = self.client.get("/onboarding").get_data(as_text=True)
        for label in ("Warm-up", "Typing Sample", "Natural Activity", "Train Model"):
            self.assertIn(label, page)
        self.assertIn("What is collected", page)
        self.assertIn("What is not collected", page)
        self.assertIn('id="practiceText"', page)
        self.assertNotIn('name="practiceText"', page)
        self.assertNotIn("<form", page[page.index('id="practiceText"') : page.index('id="enrollmentProgress"')])
        self.assertIn('id="enrollmentProgress"', page)
        self.assertIn('id="pauseEnrollment"', page)
        self.assertIn('id="resumeEnrollment"', page)
        self.assertIn('id="discardEnrollment"', page)
        self.assertIn('id="finishEnrollment"', page)
        script = Path(__file__).parents[1].joinpath("frontend/onboarding.ts").read_text(encoding="utf-8")
        self.assertNotIn("practiceText", script)
        self.assertIn("usable windows", script)
        for path in ("pause", "resume", "discard", "finish"):
            self.assertIn(f"/api/onboarding/{path}", script)

    def test_enrollment_controls_pause_resume_discard_and_finish(self):
        self.assertEqual(self.client.post("/api/onboarding/pause", json={}).status_code, 401)
        device_id, agent = self.pair()
        self.assertEqual(self.client.post("/api/onboarding/pause", data="{}").status_code, 415)
        blocked = self.client.post(
            "/api/onboarding/pause", json={"practiceText": "secret phrase"}, headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(blocked.status_code, 403)
        initial = self.client.get("/api/onboarding").get_json()
        self.assertEqual((initial["agent"], initial["collection"], initial["usable_windows"]), ("offline", "active", 0))
        self.assertEqual(initial["required_windows"], 30)
        self.client.get("/api/agent/onboarding", headers=agent)
        self.assertEqual(self.client.get("/api/onboarding").get_json()["agent"], "active")
        self.assertEqual(self.client.post("/api/onboarding/resume", json={}).status_code, 409)
        session_id = self.client.post("/api/onboarding/rounds", json={"game": "typing"}).get_json()["session_id"]
        uploaded = self.client.post("/api/agent/windows", headers=agent, json=window(device_id, session_id, 0))
        self.assertEqual(uploaded.status_code, 201)
        paused = self.client.post("/api/onboarding/pause", json={"practiceText": "secret phrase"})
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.get_json()["agent"], "paused")
        self.assertEqual(paused.get_json()["usable_windows"], 1)
        self.assertNotIn("secret phrase", str(list(self.database.onboarding.find())))
        self.assertNotIn("secret phrase", str(list(self.database.windows.find())))
        self.assertIsNone(self.client.get("/api/agent/onboarding", headers=agent).get_json()["session_id"])
        self.assertEqual(
            self.client.post("/api/agent/windows", headers=agent, json=window(device_id, session_id, 1)).status_code,
            409,
        )
        self.assertEqual(self.client.post("/api/onboarding/rounds", json={"game": "aim"}).status_code, 409)
        resumed = self.client.post("/api/onboarding/resume", json={})
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.get_json()["agent"], "active")
        self.assertEqual(self.client.get("/api/agent/onboarding", headers=agent).get_json()["session_id"], session_id)
        self.clock += timedelta(seconds=91)
        self.assertEqual(self.client.get("/api/onboarding").get_json()["agent"], "offline")
        self.client.get("/api/agent/onboarding", headers=agent)
        finished = self.client.post("/api/onboarding/finish", json={})
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(finished.get_json()["collection"], "finished")
        self.assertEqual(finished.get_json()["duration_seconds"], 60)
        self.assertEqual(finished.get_json()["usable_windows"], 1)
        self.assertEqual(
            self.client.post("/api/agent/windows", headers=agent, json=window(device_id, session_id, 2)).status_code,
            409,
        )
        self.assertEqual(self.client.post("/api/onboarding/pause", json={}).status_code, 409)
        self.assertEqual(self.client.post("/api/onboarding/finish", json={}).get_json()["collection"], "finished")
        discarded = self.client.post("/api/onboarding/discard", json={})
        self.assertEqual(discarded.status_code, 200)
        cleared = discarded.get_json()
        self.assertEqual((cleared["collection"], cleared["usable_windows"], cleared["duration_seconds"]), ("active", 0, 0))
        self.assertEqual(self.database.windows.count_documents({"device_id": device_id}), 0)


if __name__ == "__main__":
    unittest.main()
