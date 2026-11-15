import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import mongomock
from flask import Flask
from pymongo.errors import ConnectionFailure, DuplicateKeyError

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


def native_window(device_id, session_id, sequence, schema_version=4):
    features = {
        "key_count": 40,
        "hold_mean_ms": 100.0,
        "hold_std_ms": 20.0,
        "press_interval_mean_ms": 250.0,
        "press_interval_std_ms": 50.0,
        "hold_sample_count": 40,
        "mouse_sample_count": 10,
        "mouse_click_count": 2,
        "mouse_speed_mean_counts_s": 100.0,
        "mouse_speed_std_counts_s": 20.0,
        "mouse_click_interval_mean_ms": 300.0,
        "mouse_click_interval_std_ms": 20.0,
        "mouse_turn_mean_rad": 1.0,
        "mouse_turn_std_rad": 0.2,
        "mouse_path_straightness": 0.8,
    }
    if schema_version == 3:
        features = {name: value for name, value in features.items() if not name.startswith("mouse_")}
    return {
        "schema_version": schema_version,
        "device_id": device_id,
        "session_id": session_id,
        "sequence": sequence,
        "mode": "enrollment",
        "started_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "duration_ms": 10_000,
        "features": features,
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
        self.score_states = []
        self.baseline_score = None
        self.deleted = []
        self.delete_error = None
        self.reset_calls = []
        self.deactivate_calls = []
        self.model_service_error = None
        self.training_result = None
        self.activated = []
        self.activate_error = None
        self.compared = []
        self.auth_sessions = {}
        patcher = patch("hosted.requests.request", side_effect=self.auth_request)
        patcher.start()
        self.addCleanup(patcher.stop)

        def trainer(user_id, device_id, windows, validation_sessions):
            self.trained.append((user_id, device_id, windows, validation_sessions))
            if self.training_error:
                raise self.training_error
            if self.training_result is not None:
                return self.training_result
            return f"model-{len(self.trained)}"

        def deleter(user_id, device_id):
            if self.delete_error:
                raise self.delete_error
            self.deleted.append((user_id, device_id))

        def resetter(user_id, device_id):
            if self.model_service_error:
                raise self.model_service_error
            self.reset_calls.append((user_id, device_id))

        def deactivator(user_id, device_id):
            if self.model_service_error:
                raise self.model_service_error
            self.deactivate_calls.append((user_id, device_id))

        self.trainer = trainer
        self.deleter = deleter
        app = Flask(__name__, template_folder=str(Path(server.__file__).parent / "templates"))
        with patch.dict(os.environ, ENV):
            hosted.register(
                app, self.database, self.trainer, self.score, server.onboarding_window_error, self.deleter,
                self.activate, self.compare, resetter, deactivator,
            )
        self.client = app.test_client()

    def activate(self, user_id, device_id, model_id):
        if self.activate_error:
            raise self.activate_error
        self.activated.append(model_id)

    def compare(self, user_id, device_id, model_id, windows):
        self.compared.append((model_id, windows))
        return {"candidate": {"model_id": model_id}, "active": {"model_id": self.activated[-1]}}

    def score(self, user_id, device_id, value):
        if self.score_error:
            raise self.score_error
        self.scored.append((user_id, device_id, value["sequence"]))
        state = self.score_states.pop(0) if self.score_states else "NORMAL"
        score = None if state == "INSUFFICIENT_DATA" else 0.8 if state == "UNUSUAL" else -0.4
        result = {"model_id": "model-1", "state": state, "score": score, "threshold": 0.1, "score_delta": -0.5}
        if self.baseline_score is not None:
            result["baseline_score"] = self.baseline_score
        return result

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

    def test_health_reports_database_readiness_without_authentication(self):
        with patch.object(self.database, "command", return_value={"ok": 1}) as ping:
            healthy = self.client.get("/health")
        self.assertEqual((healthy.status_code, healthy.get_json()), (200, {
            "state": "ok",
            "database": "available",
        }))
        ping.assert_called_once_with("ping")

        with (
            patch.object(self.database, "command", side_effect=ConnectionFailure("private connection detail")),
            self.assertLogs(hosted.__name__, level="WARNING") as logs,
        ):
            degraded = self.client.get("/health")
        self.assertEqual((degraded.status_code, degraded.get_json()), (503, {
            "state": "degraded",
            "database": "unavailable",
        }))
        event = json.loads(logs.records[0].getMessage())
        self.assertEqual(event, {
            "component": "database",
            "event": "health_check",
            "outcome": "unavailable",
        })
        self.assertNotIn("private connection detail", logs.output[0])

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

    def test_agent_heartbeat_stores_online_status_and_last_seen(self):
        device_id, agent = self.pair()
        self.assertEqual(self.client.post("/agent/heartbeat").status_code, 401)
        self.assertEqual(
            self.client.post("/api/agent/heartbeat", headers={"Authorization": "Bearer nope"}).status_code, 401,
        )
        self.clock += timedelta(seconds=1)
        response = self.client.post("/agent/heartbeat", headers=agent)
        expected_heartbeat = self.clock.replace(microsecond=(self.clock.microsecond // 1000) * 1000)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {
            "status": "online",
            "last_seen": expected_heartbeat.isoformat(),
        })
        stored = self.database.devices.find_one({"_id": device_id})
        self.assertEqual(stored["status"], "online")
        self.assertEqual(stored["last_heartbeat_at"], expected_heartbeat)

    def play(self, game, windows, device_id, agent):
        session_id = self.client.post("/api/onboarding/rounds", json={"game": game}).get_json()["session_id"]
        self.assertEqual(self.client.get("/api/agent/onboarding", headers=agent).get_json()["session_id"], session_id)
        for sequence, key_count in enumerate(windows):
            upload = self.client.post("/api/agent/windows", headers=agent,
                                      json=window(device_id, session_id, sequence, key_count))
            self.assertEqual(upload.status_code, 201, upload.get_json())
        self.clock += timedelta(seconds=61)
        return session_id, self.client.post("/api/onboarding/rounds/current/finish", json={}).get_json()

    def enroll(self, device_id, agent):
        for index in range(3):
            if index < 2:
                self.play("aim", [0], device_id, agent)
            for game in ("tetris", "typing", "arena"):
                last, _ = self.play(game, [40] * 4, device_id, agent)
        self.play("aim", [0], device_id, agent)
        return last

    def test_retrain_needs_approval_compares_on_a_holdout_and_rolls_back(self):
        device_id, agent = self.pair()
        self.enroll(device_id, agent)
        self.assertEqual(self.client.post("/api/onboarding/train", json={}).get_json()["model_id"], "model-1")
        self.assertEqual(self.activated, ["model-1"])
        self.assertEqual(self.client.post("/api/onboarding/retrain", json={}).status_code, 422)
        status = self.client.post("/api/onboarding/retrain", json={"approve": True}).get_json()
        self.assertEqual((status["state"], status["retraining"], status["model_id"]), ("collecting", True, "model-1"))
        self.assertFalse(status["ready_to_train"], "earlier rounds do not count toward a retrain")
        monitoring = window(device_id, "monitor-1", 0) | {"mode": "monitoring"}
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=monitoring).status_code, 201)
        enrolled = self.database.windows.count_documents({})
        self.play("typing", [40] * 4, device_id, agent)
        self.client.post("/api/onboarding/discard", json={})
        self.assertEqual(self.database.windows.count_documents({}), enrolled, "discard keeps the active model's data")

        holdout = self.enroll(device_id, agent)
        status = self.client.post("/api/onboarding/train", json={}).get_json()
        self.assertEqual((status["state"], status["candidate_model_id"]), ("review", "model-2"))
        self.assertEqual(self.activated, ["model-1"], "a retrained model is not activated automatically")
        first_sessions = {item["session_id"] for item in self.trained[0][2]}
        retrained_sessions = {item["session_id"] for item in self.trained[1][2]} | set(self.trained[1][3])
        self.assertFalse(retrained_sessions & (first_sessions | {holdout}))
        compared_model, compared_windows = self.compared[0]
        self.assertEqual((compared_model, {item["session_id"] for item in compared_windows}), ("model-2", {holdout}))
        self.assertEqual(status["comparison"]["active"]["model_id"], "model-1")

        status = self.client.post("/api/onboarding/candidate/activate", json={}).get_json()
        self.assertEqual((status["state"], status["model_id"], status["previous_model_id"]),
                         ("ready", "model-2", "model-1"))
        active = [item["_id"] for item in self.database.model_versions.find({"active": True})]
        self.assertEqual(active, ["model-2"])
        self.activate_error = RuntimeError("model service returned 503")
        self.assertEqual(self.client.post("/api/onboarding/rollback", json={}).status_code, 502)
        self.assertEqual(self.client.get("/api/onboarding").get_json()["model_id"], "model-2")
        self.activate_error = None
        status = self.client.post("/api/onboarding/rollback", json={}).get_json()
        self.assertEqual((status["model_id"], status["previous_model_id"]), ("model-1", "model-2"))
        self.assertEqual(self.activated, ["model-1", "model-2", "model-1"])
        self.assertEqual(self.client.post("/api/onboarding/candidate/activate", json={}).status_code, 409)

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
        self.assertEqual(self.database.windows.count_documents({}), 0)
        valid = window(device_id, session_id, 0)
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=valid).status_code, 201)
        self.assertEqual(self.client.post("/api/agent/windows", headers=agent, json=valid).status_code, 409)
        self.assertEqual(self.database.windows.count_documents({}), 1)
        stored = self.database.windows.find_one({"session_id": session_id})
        self.assertEqual(
            (stored["device_id"], stored["user_id"], stored["session_id"], stored["sequence"]),
            (device_id, "better-auth:user-a", session_id, 0),
        )
        session = self.database.rounds.find_one({"_id": session_id})
        self.assertEqual(session["device_id"], device_id)
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["valid_window_count"], 1)
        self.sign_in("user-b")
        self.assertEqual(self.client.get("/api/onboarding").status_code, 404)

    def test_agent_window_accepts_current_native_schemas(self):
        device_id, agent = self.pair()
        session_id = self.client.post("/api/onboarding/rounds", json={"game": "typing"}).get_json()["session_id"]
        for schema_version in (3, 4):
            with self.subTest(schema_version=schema_version):
                response = self.client.post(
                    "/api/agent/windows",
                    headers=agent,
                    json=native_window(device_id, session_id, schema_version, schema_version),
                )
                self.assertEqual(response.status_code, 201, response.get_json())
                stored = self.database.windows.find_one({"session_id": session_id, "sequence": schema_version})
                self.assertEqual(stored["window"]["schema_version"], schema_version)
                self.assertEqual((stored["device_id"], stored["session_id"]), (device_id, session_id))

    def test_invalid_agent_window_is_not_persisted_or_logged_as_input(self):
        device_id, agent = self.pair()
        session_id = self.client.post("/api/onboarding/rounds", json={"game": "typing"}).get_json()["session_id"]
        payload = window(device_id, session_id, 0) | {"private_field": "sensitive-window-content"}

        with self.assertLogs(hosted.__name__, level="INFO") as logs:
            response = self.client.post("/api/agent/windows", headers=agent, json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.database.windows.count_documents({}), 0)
        event = json.loads(logs.records[0].getMessage())
        self.assertEqual(event, {
            "device_id": device_id,
            "event": "agent_window_ingestion",
            "outcome": "rejected",
            "reason": "invalid_payload",
        })
        self.assertNotIn("private_field", logs.output[0])
        self.assertNotIn("sensitive-window-content", logs.output[0])

    def test_agent_window_request_size_is_limited_before_persistence(self):
        device_id, agent = self.pair()
        body = b"x" * (hosted.AGENT_WINDOW_MAX_BYTES + 1)

        with self.assertLogs(hosted.__name__, level="INFO") as logs:
            response = self.client.post(
                "/api/agent/windows",
                headers=agent,
                data=body,
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.database.windows.count_documents({}), 0)
        self.assertEqual(self.database.monitoring_windows.count_documents({}), 0)
        event = json.loads(logs.records[0].getMessage())
        self.assertEqual(event["event"], "agent_window_ingestion")
        self.assertEqual(event["outcome"], "rejected")
        self.assertEqual(event["reason"], "request_too_large")
        self.assertNotIn("x" * 100, logs.output[0])

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
                self.activate, self.compare,
            )

        session = self.database.rounds.find_one({"_id": "legacy-session"})
        self.assertEqual(session["device_id"], device_id)
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["valid_window_count"], 5)
        self.assertEqual(session["generation"], 0)

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

    def test_enrollment_endpoints_are_owner_scoped_and_group_usable_windows_by_session(self):
        device_id, agent = self.pair()
        started = self.client.post(
            "/api/enrollments", json={"device_id": device_id, "game": "typing"}
        )
        self.assertEqual(started.status_code, 201, started.get_json())
        enrollment = started.get_json()
        enrollment_id = enrollment["id"]
        self.assertEqual(enrollment["status"], "active")
        self.assertEqual(enrollment["progress"]["usable_window_count"], 0)

        self.assertEqual(
            self.client.post(
                "/api/enrollments", json={"device_id": device_id, "game": "typing"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.get("/api/agent/onboarding", headers=agent).get_json()["session_id"],
            enrollment_id,
        )
        uploaded = self.client.post(
            "/api/agent/windows", headers=agent, json=window(device_id, enrollment_id, 0)
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.get_json())
        stored = self.database.windows.find_one({"session_id": enrollment_id})
        self.assertEqual(stored["session_id"], enrollment_id)
        self.assertEqual(stored["window"]["session_id"], enrollment_id)
        unusable = self.client.post(
            "/api/agent/windows",
            headers=agent,
            json=window(device_id, enrollment_id, 1, key_count=20),
        )
        self.assertEqual(unusable.status_code, 201, unusable.get_json())
        self.assertFalse(unusable.get_json()["usable"])

        progress = self.client.get(f"/api/enrollments/{enrollment_id}").get_json()
        self.assertEqual(progress["progress"]["usable_window_count"], 1)
        self.clock += timedelta(seconds=30)
        completed = self.client.post(f"/api/enrollments/{enrollment_id}/complete", json={})
        self.assertEqual(completed.status_code, 200, completed.get_json())
        self.assertEqual(completed.get_json()["status"], "completed")
        onboarding_progress = self.client.get("/api/onboarding").get_json()
        self.assertIn(enrollment_id, onboarding_progress["sessions"])
        self.assertIn(enrollment_id, onboarding_progress["validation_sessions"])
        self.assertEqual(onboarding_progress["usable_windows"], 1)
        self.assertEqual(
            self.client.post(
                "/api/agent/windows", headers=agent, json=window(device_id, enrollment_id, 2)
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                "/api/enrollments", json={"device_id": device_id, "game": "typing"}
            ).status_code,
            201,
        )

        self.sign_in("user-b")
        self.assertEqual(self.client.get(f"/api/enrollments/{enrollment_id}").status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/enrollments/{enrollment_id}/complete", json={}).status_code, 404
        )
        self.assertEqual(
            self.client.post(
                "/api/enrollments", json={"device_id": device_id, "game": "typing"}
            ).status_code,
            404,
        )

    def test_enrollment_unique_index_rejects_concurrent_active_sessions(self):
        device_id, _ = self.pair()
        onboarding = self.database.onboarding.find_one({"device_id": device_id})
        session = {
            "_id": "active-enrollment",
            "onboarding_id": onboarding["_id"],
            "device_id": device_id,
            "game": "typing",
            "started_at": self.clock,
            "ended_at": None,
            "status": "active",
            "credited_ms": 0,
            "valid_window_count": 0,
        }
        self.database.rounds.insert_one(session)
        with self.assertRaises(DuplicateKeyError):
            self.database.rounds.insert_one({**session, "_id": "second-active-enrollment"})

    def test_enrollment_creation_rejects_invalid_game_or_unpaired_device(self):
        device_id, _ = self.pair()
        self.assertEqual(
            self.client.post(
                "/api/enrollments", json={"device_id": device_id, "game": "unknown"}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/api/enrollments", json={"device_id": "unpaired-device", "game": "typing"}
            ).status_code,
            404,
        )

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
        self.assertEqual(
            (stored["user_id"], stored["device_id"], stored["session_id"], stored["sequence"]),
            ("better-auth:user-a", device_id, "monitor-1", 0),
        )
        self.assertEqual(stored["result"]["state"], "NORMAL")

    def test_model_summary_reports_device_counts_algorithm_and_optional_evaluation(self):
        self.assertEqual(self.client.get("/api/model").status_code, 401)
        device_id, _ = self.pair()
        self.assertFalse(self.client.get("/api/model").get_json()["available"])
        trained_at = "2026-01-02T03:04:05+00:00"
        self.database.model_versions.insert_one({
            "_id": "model-7",
            "user_id": "better-auth:user-a",
            "device_id": device_id,
            "state": "ready",
            "algorithm": "IsolationForest",
            "active": True,
            "created_at": self.clock,
            "metadata": {
                "model_id": "model-7",
                "algorithm": "IsolationForest",
                "trained_at": trained_at,
                "training_window_count": 24,
                "validation_window_count": 12,
                "training_sessions": ["s1", "s2"],
                "validation_sessions": ["s3"],
                "threshold": 1.5,
                "threshold_rule": "fit on this device's enrollment",
                "holdout_alert_count": 2,
                "holdout_window_count": 10,
                "impostor_evaluation_alert_count": 7,
                "impostor_evaluation_window_count": 8,
            },
        })
        summary = self.client.get("/api/model").get_json()
        self.assertEqual(summary["device_name"], "keyboard")
        self.assertEqual(summary["device_id"], device_id)
        self.assertEqual(summary["model_id"], "model-7")
        self.assertEqual(summary["trained_at"], trained_at)
        self.assertEqual((summary["training_windows"], summary["validation_windows"], summary["window_count"]), (24, 12, 36))
        self.assertEqual(summary["session_count"], 3)
        self.assertEqual(summary["algorithm"], "IsolationForest")
        self.assertIn("Isolation Forest", summary["algorithm_label"])
        self.assertIn("not an identity", summary["algorithm_label"])
        self.assertEqual(summary["threshold"], 1.5)
        self.assertIn("above 1.5", summary["threshold_label"])
        self.assertEqual((summary["holdout_alerts"], summary["holdout_windows"]), (2, 10))
        self.assertEqual((summary["impostor_alerts"], summary["impostor_windows"]), (7, 8))
        self.assertIn("not proof of identity", summary["limitations"])
        self.database.model_versions.update_one({"_id": "model-7"}, {"$set": {"active": False}})
        hidden = self.client.get("/api/model").get_json()
        self.assertFalse(hidden["available"])
        self.assertIsNone(hidden["holdout_windows"])
        self.assertIsNone(hidden["impostor_windows"])
        self.sign_in("user-b")
        self.assertFalse(self.client.get("/api/model").get_json()["available"])

    def seed_enrollment(self, device_id):
        onboarding = self.database.onboarding.find_one({"device_id": device_id})
        base = self.clock - timedelta(minutes=10)
        for index, game in enumerate(("aim", "tetris", "typing", "arena")):
            for offset, count in ((0, 6), (1, 3)):
                session_id = f"{game}-{offset}"
                started = base + timedelta(seconds=index * 10 + offset)
                self.database.rounds.insert_one({
                    "_id": session_id,
                    "onboarding_id": onboarding["_id"],
                    "device_id": device_id,
                    "game": game,
                    "started_at": started,
                    "ended_at": started + timedelta(seconds=150),
                    "status": "completed",
                    "credited_ms": 150_000,
                    "valid_window_count": count,
                    "generation": 0,
                })
                for sequence in range(count):
                    self.database.windows.insert_one({
                        "onboarding_id": onboarding["_id"],
                        "user_id": "better-auth:user-a",
                        "device_id": device_id,
                        "session_id": session_id,
                        "sequence": sequence,
                        "usable": True,
                        "window": {"session_id": session_id, "features": {"key_count": 30}},
                        "created_at": started,
                    })
        self.database.monitoring_windows.insert_one({
            "user_id": "better-auth:user-a", "device_id": device_id, "session_id": "kept-monitor",
        })
        return onboarding

    def test_retrain_reset_and_deactivate_require_confirmation(self):
        self.assertEqual(self.client.post("/api/models/reset", json={"confirmation": "RESET"}).status_code, 401)
        device_id, _ = self.pair()
        onboarding = self.seed_enrollment(device_id)
        self.assertEqual(self.client.post("/api/models/reset", data="{}").status_code, 415)
        self.assertEqual(
            self.client.post("/api/models/reset", json={"confirmation": "RESET"},
                             headers={"Origin": "https://evil.example"}).status_code,
            403,
        )
        rejected = self.client.post("/api/models/reset", json={"confirmation": "reset", "practiceText": "secret"})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.trained, [])
        self.assertEqual(self.reset_calls, [])
        self.assertNotIn("secret", str(list(self.database.model_versions.find())))
        trained = self.client.post("/api/onboarding/train", json={})
        self.assertEqual(trained.status_code, 202)
        counted = self.client.get("/api/model").get_json()
        self.assertEqual(counted["model_id"], "model-1")
        self.assertIsNone(counted["algorithm"])
        self.assertEqual(counted["window_count"], 36)
        self.assertEqual(counted["session_count"], 8)
        self.assertIsNone(counted["holdout_windows"])
        self.assertIsNone(counted["impostor_windows"])
        self.database.model_versions.update_one(
            {"_id": "model-1"}, {"$set": {"metadata.algorithm": "LogisticRegression"}},
        )
        labeled = self.client.get("/api/model").get_json()
        self.assertEqual(labeled["algorithm"], "LogisticRegression")
        self.assertIn("Logistic regression", labeled["algorithm_label"])
        self.sign_in("user-b")
        self.assertEqual(self.client.post("/api/models/reset", json={"confirmation": "RESET"}).status_code, 404)
        self.assertEqual(self.reset_calls, [])
        self.sign_in()
        self.model_service_error = RuntimeError("down")
        failed = self.client.post("/api/models/deactivate", json={"confirmation": "DEACTIVATE"})
        self.assertEqual(failed.status_code, 502)
        self.assertTrue(self.database.model_versions.find_one({"_id": "model-1"})["active"])
        self.assertEqual(self.database.windows.count_documents({"onboarding_id": onboarding["_id"]}), 36)
        self.model_service_error = None
        deactivated = self.client.post("/api/models/deactivate", json={"confirmation": "DEACTIVATE"})
        self.assertEqual(deactivated.status_code, 200)
        self.assertEqual(self.deactivate_calls, [("better-auth:user-a", device_id)])
        self.assertFalse(self.client.get("/api/model").get_json()["available"])
        self.assertEqual(self.database.windows.count_documents({"onboarding_id": onboarding["_id"]}), 36)
        reset = self.client.post("/api/models/reset", json={"confirmation": "RESET"})
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(self.reset_calls, [("better-auth:user-a", device_id)])
        self.assertEqual(self.database.windows.count_documents({"onboarding_id": onboarding["_id"]}), 0)
        self.assertEqual(self.database.monitoring_windows.count_documents({"session_id": "kept-monitor"}), 1)
        self.assertFalse(self.client.get("/api/model").get_json()["available"])

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

    def ready_monitor(self):
        device_id, agent = self.pair()
        self.database.onboarding.update_one(
            {"device_id": device_id}, {"$set": {"state": "ready", "model_id": "model-1"}},
        )
        return device_id, agent

    def upload_scores(self, device_id, agent, states):
        start = getattr(self, "monitor_sequence", 0)
        self.score_states = list(states)
        last = None
        for offset in range(len(states)):
            self.clock += timedelta(seconds=1)
            body = window(device_id, "monitor-1", start + offset) | {"mode": "monitoring"}
            last = self.client.post("/api/agent/windows", headers=agent, json=body)
            self.assertEqual(last.status_code, 201, last.get_json())
        self.monitor_sequence = start + len(states)
        return last

    def test_persistence_rule_counts_valid_windows_only(self):
        decide = hosted.persistence_decision
        self.assertIsNone(decide(["UNUSUAL", "UNUSUAL"], window=4, unusual_needed=3, normal_needed=3))
        self.assertIsNone(decide(
            ["UNUSUAL", "UNUSUAL", "NORMAL", "NORMAL"], window=4, unusual_needed=3, normal_needed=3,
        ))
        self.assertEqual(decide(
            ["UNUSUAL", "UNUSUAL", "UNUSUAL", "NORMAL"], window=4, unusual_needed=3, normal_needed=3,
        ), "open")
        self.assertEqual(decide(
            ["UNUSUAL", "UNUSUAL", "INSUFFICIENT_DATA", "UNUSUAL", "NORMAL"],
            window=4, unusual_needed=3, normal_needed=3,
        ), "open")
        self.assertEqual(decide(
            ["NORMAL", "NORMAL", "NORMAL", "UNUSUAL"], window=4, unusual_needed=3, normal_needed=3,
        ), "close")
        self.assertEqual(hosted.alert_rule(), (4, 3, 3))
        with patch.dict(os.environ, {"ALERT_WINDOW": "0", "ALERT_UNUSUAL_COUNT": "nope", "ALERT_NORMAL_COUNT": ""}):
            self.assertEqual(hosted.alert_rule(), (4, 3, 3))
        with patch.dict(os.environ, {"ALERT_WINDOW": "2", "ALERT_UNUSUAL_COUNT": "2", "ALERT_NORMAL_COUNT": "2"}):
            self.assertEqual(hosted.alert_rule(), (2, 2, 2))

    def test_monitoring_status_history_and_alerts_follow_the_persistence_rule(self):
        self.assertEqual(self.client.get("/api/monitoring/status").status_code, 401)
        self.assertEqual(self.client.get("/api/monitoring/history").status_code, 401)
        self.assertEqual(self.client.get("/monitoring/status").status_code, 401)
        self.assertEqual(self.client.get("/monitoring/history").status_code, 401)
        self.assertEqual(self.client.get("/api/alerts").status_code, 401)
        self.assertEqual(
            self.client.post("/api/alerts/missing/acknowledge", json={"label": "expected"}).status_code, 401,
        )
        self.sign_in()
        empty = self.client.get("/api/monitoring/status").get_json()
        self.assertIsNone(empty["state"])
        self.assertEqual(empty["agent"], "offline")
        self.assertTrue(empty["stale"])
        self.assertEqual(self.client.get("/api/monitoring/history").get_json(), {"axis": "Anomaly score", "points": []})
        self.assertEqual(self.client.get("/monitoring/status").get_json(), empty)
        self.assertEqual(self.client.get("/monitoring/history").get_json(), {"axis": "Anomaly score", "points": []})
        for query in ("limit=0", "limit=201", "since=yesterday"):
            self.assertEqual(self.client.get(f"/api/monitoring/history?{query}").status_code, 422)
        self.assertEqual(self.client.get("/api/alerts?status=nope").status_code, 422)
        self.assertEqual(self.client.get("/api/monitoring/status?device_id=other").status_code, 404)
        self.assertEqual(self.client.get("/api/alerts?device_id=other").status_code, 404)

        device_id, agent = self.ready_monitor()
        self.upload_scores(device_id, agent, ["UNUSUAL"])
        one = self.client.get("/api/monitoring/status").get_json()
        self.assertEqual((one["state"], one["window_state"], one["score"], one["agent"], one["stale"]),
                         ("unusual", "UNUSUAL", 0.8, "active", False))
        self.assertEqual(one["scored_at"], self.clock.isoformat())
        self.assertEqual(self.client.get("/monitoring/status").get_json(), one)
        self.assertEqual(one["device_id"], device_id)
        self.assertEqual(one["device_name"], "keyboard")
        self.assertEqual(self.client.get("/api/alerts").get_json()["alerts"], [])

        self.upload_scores(device_id, agent, ["UNUSUAL", "UNUSUAL", "NORMAL"])
        opened = self.client.get("/api/alerts").get_json()["alerts"]
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["status"], "open")
        self.assertTrue(opened[0]["ongoing"])
        self.assertIsNone(opened[0]["duration_seconds"])
        self.assertIsNone(opened[0]["acknowledgement"])
        self.assertIn("not proof of identity", opened[0]["explanation"])
        self.assertEqual(self.client.get("/api/monitoring/status").get_json()["state"], "needs_verification")

        self.upload_scores(device_id, agent, ["NORMAL"])
        self.assertEqual(self.client.get("/api/alerts").get_json()["alerts"][0]["status"], "open")
        self.upload_scores(device_id, agent, ["NORMAL"])
        closed = self.client.get("/api/alerts?status=closed").get_json()["alerts"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["duration_seconds"], 2)
        self.assertFalse(closed[0]["ongoing"])
        self.assertEqual(self.client.get("/api/alerts?status=open").get_json()["alerts"], [])
        self.assertEqual(self.client.get("/api/monitoring/status").get_json()["state"], "normal")

        history = self.client.get("/api/monitoring/history").get_json()
        self.assertEqual(history["axis"], "Anomaly score")
        self.assertEqual([point["state"] for point in history["points"]],
                         ["UNUSUAL", "UNUSUAL", "UNUSUAL", "NORMAL", "NORMAL", "NORMAL"])
        self.assertEqual([point["score"] for point in history["points"]], [0.8, 0.8, 0.8, -0.4, -0.4, -0.4])
        self.assertTrue(all(point["threshold"] == 0.1 for point in history["points"]))
        self.assertTrue(all(isinstance(point["t"], str) and point["t"] for point in history["points"]))
        self.assertEqual(history["points"][-1]["t"], self.client.get("/monitoring/status").get_json()["scored_at"])
        self.assertEqual(self.client.get("/monitoring/history").get_json(), history)
        future = self.client.get("/api/monitoring/history?since=2099-01-01T00:00:00Z").get_json()
        self.assertEqual(future["points"], [])
        excluded = self.client.get("/monitoring/history?until=2000-01-01T00:00:00Z").get_json()
        self.assertEqual(excluded["points"], [])
        scoped = self.client.get(f"/api/monitoring/history?device_id={device_id}&limit=2").get_json()
        self.assertEqual([point["state"] for point in scoped["points"]], ["NORMAL", "NORMAL"])

        self.upload_scores(device_id, agent, ["UNUSUAL", "UNUSUAL", "UNUSUAL"])
        again = self.client.get("/api/alerts").get_json()["alerts"]
        self.assertEqual(len(again), 2)
        open_alert = self.client.get("/api/alerts?status=open").get_json()["alerts"][0]
        rejected = self.client.post(
            f"/api/alerts/{open_alert['id']}/acknowledge", json={"label": "maybe", "retrain": True},
        )
        self.assertEqual(rejected.status_code, 400)
        blocked = self.client.post(
            f"/api/alerts/{open_alert['id']}/acknowledge",
            json={"label": "expected"}, headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(blocked.status_code, 403)
        self.sign_in("user-b")
        self.assertEqual(self.client.get("/api/alerts").get_json()["alerts"], [])
        self.assertEqual(self.client.get(f"/api/alerts?device_id={device_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/monitoring/status?device_id={device_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/monitoring/status?device_id={device_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/monitoring/history?device_id={device_id}").status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/alerts/{open_alert['id']}/acknowledge", json={"label": "unexpected"}).status_code,
            404,
        )
        self.sign_in()
        acknowledged = self.client.post(
            f"/api/alerts/{open_alert['id']}/acknowledge", json={"label": "expected"},
        )
        self.assertEqual(acknowledged.status_code, 200)
        body = acknowledged.get_json()
        self.assertEqual(body["acknowledgement"], "expected")
        self.assertEqual(body["status"], "open")
        self.assertEqual(self.trained, [])
        self.assertEqual(self.activated, [])
        self.assertEqual(self.database.model_versions.count_documents({}), 0)
        stored = self.database.alerts.find_one({"_id": open_alert["id"]})
        self.assertEqual(stored["acknowledgement"], "expected")
        self.assertIsNotNone(stored["acknowledged_at"])

    def test_alert_rule_settings_and_agent_presence_change_the_status(self):
        device_id, agent = self.ready_monitor()
        with patch.dict(os.environ, {"ALERT_WINDOW": "2", "ALERT_UNUSUAL_COUNT": "2", "ALERT_NORMAL_COUNT": "2"}):
            self.upload_scores(device_id, agent, ["UNUSUAL", "UNUSUAL"])
        self.assertEqual(len(self.client.get("/api/alerts").get_json()["alerts"]), 1)
        self.database.onboarding.update_one({"device_id": device_id}, {"$set": {"collection": "paused"}})
        self.client.get("/api/agent/onboarding", headers=agent)
        paused = self.client.get("/api/monitoring/status").get_json()
        self.assertEqual((paused["state"], paused["agent"], paused["stale"]), ("paused", "paused", False))
        self.assertEqual(paused["score"], 0.8)
        self.database.devices.update_one(
            {"_id": device_id}, {"$set": {"last_heartbeat_at": self.clock - timedelta(minutes=11)}},
        )
        offline = self.client.get("/api/monitoring/status").get_json()
        self.assertEqual((offline["state"], offline["agent"], offline["stale"]), ("offline", "offline", True))
        self.assertEqual(offline["score"], 0.8)
        self.assertIsNotNone(offline["last_seen"])

    def test_verification_delay_counts_windows_after_the_switch(self):
        base = self.clock
        normals = [(base + timedelta(seconds=index), "NORMAL") for index in range(4)]
        switched = base + timedelta(seconds=4)
        self.assertEqual(hosted.verification_delay(normals, switched, window=4, unusual_needed=3), (False, None))
        after = normals + [(switched + timedelta(seconds=offset), "UNUSUAL") for offset in (1, 2, 3)]
        self.assertEqual(hosted.verification_delay(after, switched, window=4, unusual_needed=3), (False, 3))
        early = [(base + timedelta(seconds=index), "UNUSUAL") for index in range(4)]
        alert_before, latency = hosted.verification_delay(
            early + [(base + timedelta(seconds=5), "UNUSUAL")], base + timedelta(seconds=4),
            window=4, unusual_needed=3,
        )
        self.assertTrue(alert_before)
        self.assertEqual(latency, 1)
        skipped = [
            (base, "UNUSUAL"), (base + timedelta(seconds=1), "INSUFFICIENT_DATA"),
            (base + timedelta(seconds=2), "UNUSUAL"), (base + timedelta(seconds=3), "UNUSUAL"),
            (base + timedelta(seconds=4), "NORMAL"),
        ]
        self.assertEqual(
            hosted.verification_delay(skipped, base - timedelta(seconds=1), window=4, unusual_needed=3),
            (False, 5),
        )

    def test_devices_list_last_check_in_and_revoke_blocks_the_token(self):
        self.assertEqual(self.client.get("/api/devices").status_code, 401)
        anonymous = self.client.get("/devices")
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login?next=/devices", anonymous.headers["Location"])
        device_id, agent = self.pair()
        self.client.get("/api/agent/onboarding", headers=agent)
        listed = self.client.get("/api/devices").get_json()["devices"]
        self.assertEqual(listed[0]["id"], device_id)
        self.assertEqual(listed[0]["name"], "keyboard")
        self.assertIsNotNone(listed[0]["last_seen"])
        self.assertFalse(listed[0]["revoked"])
        self.assertFalse(listed[0]["stale"])
        self.assertNotIn("token_hash", listed[0])
        self.sign_in("user-b")
        self.assertEqual(self.client.get("/api/devices").get_json()["devices"], [])
        self.assertEqual(self.client.post(f"/api/devices/{device_id}/revoke", json={}).status_code, 404)
        self.sign_in()
        self.assertEqual(self.client.post(f"/api/devices/{device_id}/revoke", json={}).status_code, 200)
        row = self.client.get("/api/devices").get_json()["devices"][0]
        self.assertTrue(row["revoked"])
        self.assertTrue(row["stale"])
        self.assertEqual(self.client.get("/api/agent/onboarding", headers=agent).status_code, 401)
        page = self.client.get("/devices").get_data(as_text=True)
        self.assertIn("Revoke", page)
        self.assertNotIn("%", page)

    def test_demo_switch_marker_stores_time_and_plots_baseline_score(self):
        anonymous = self.client.get("/demo")
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login?next=/demo", anonymous.headers["Location"])
        self.assertEqual(self.client.post("/api/demo/switches", json={}).status_code, 401)
        device_id, agent = self.ready_monitor()
        self.baseline_score = 0.25
        self.upload_scores(device_id, agent, ["NORMAL", "NORMAL", "NORMAL", "NORMAL"])
        self.clock += timedelta(seconds=1)
        blocked = self.client.post(
            "/api/demo/switches", json={"note": "typed"}, headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(self.client.post("/api/demo/switches", json={"note": "typed"}).status_code, 400)
        self.assertEqual(self.database.demo_switches.count_documents({}), 0)
        marked = self.client.post("/api/demo/switches", json={})
        self.assertEqual(marked.status_code, 201, marked.get_json())
        self.assertEqual(marked.get_json()["device_id"], device_id)
        self.assertIsNone(marked.get_json()["latency_seconds"])
        self.upload_scores(device_id, agent, ["UNUSUAL", "UNUSUAL", "UNUSUAL"])
        listed = self.client.get("/api/demo/switches").get_json()["switches"]
        self.assertEqual(listed[0]["latency_seconds"], 3)
        self.assertFalse(listed[0]["alert_before_switch"])
        points = self.client.get("/api/monitoring/history").get_json()["points"]
        self.assertTrue(all(point["baseline_score"] == 0.25 for point in points))
        self.sign_in("user-b")
        self.assertEqual(self.client.get("/api/demo/switches").get_json()["switches"], [])
        self.assertEqual(self.client.post(f"/api/demo/switches?device_id={device_id}", json={}).status_code, 404)
        self.sign_in()
        page = self.client.get("/demo").get_data(as_text=True)
        self.assertIn("Enrolled user", page)
        self.assertIn("Guest tester", page)
        self.assertIn("Typed content never collected.", page)
        self.assertIn("Training samples", page)
        self.assertNotIn("%", page)
        self.assertEqual(self.client.delete("/api/me/data", json={}).status_code, 200)
        self.assertEqual(self.database.demo_switches.count_documents({}), 0)

    def test_each_device_has_its_own_model_and_monitoring_view(self):
        first, first_agent = self.pair()
        self.clock += timedelta(seconds=1)
        second, _second_agent = self.pair()
        self.assertEqual(self.database.onboarding.count_documents({"user_id": "better-auth:user-a"}), 2)
        self.database.onboarding.update_one(
            {"device_id": first}, {"$set": {"state": "ready", "model_id": "model-a"}},
        )
        for device_id, model_id, samples in ((first, "model-a", 20), (second, "model-b", 30)):
            self.database.model_versions.insert_one({
                "_id": model_id,
                "user_id": "better-auth:user-a",
                "device_id": device_id,
                "active": True,
                "created_at": self.clock,
                "metadata": {
                    "algorithm": "IsolationForest",
                    "training_window_count": samples,
                    "validation_window_count": 10,
                },
            })
        self.assertEqual(self.client.get(f"/api/model?device_id={first}").get_json()["model_id"], "model-a")
        self.assertEqual(self.client.get(f"/api/model?device_id={second}").get_json()["model_id"], "model-b")
        self.assertEqual(self.client.get("/api/model").get_json()["model_id"], "model-b")
        self.database.onboarding.delete_one({"device_id": second})
        missing = self.client.get(f"/api/model?device_id={second}").get_json()
        self.assertFalse(missing["available"])
        self.assertEqual(missing["device_id"], second)
        self.assertEqual(missing["device_name"], "keyboard")
        self.sign_in("user-b")
        self.assertEqual(self.client.get(f"/api/model?device_id={first}").status_code, 404)
        self.sign_in()
        self.upload_scores(first, first_agent, ["NORMAL"])
        seen = self.client.get(f"/api/monitoring/status?device_id={first}").get_json()
        self.assertEqual(seen["score"], -0.4)
        self.assertNotIn("alerts", seen)
        other = self.client.get(f"/api/monitoring/status?device_id={second}").get_json()
        self.assertIsNone(other["score"])
        self.assertEqual(other["device_id"], second)

    def test_monitoring_stream_requires_the_owner_and_publishes_scores(self):
        self.assertEqual(self.client.get("/api/monitoring/stream").status_code, 401)
        self.sign_in()
        self.assertEqual(self.client.get("/api/monitoring/stream").status_code, 404)
        device_id, agent = self.ready_monitor()
        self.sign_in("user-b")
        self.assertEqual(self.client.get(f"/api/monitoring/stream?device_id={device_id}").status_code, 404)
        self.sign_in()
        with patch("hosted.listen_monitoring", return_value=iter([{"state": "normal", "score": -0.4}])):
            response = self.client.get("/api/monitoring/stream")
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.content_type)
            body = response.get_data(as_text=True)
        self.assertIn("event: monitoring", body)
        self.assertIn("normal", body)
        self.assertIn("-0.4", body)
        with patch("hosted.publish_monitoring") as published:
            self.upload_scores(device_id, agent, ["NORMAL"])
        user_id, published_device, payload = published.call_args.args
        self.assertEqual((user_id, published_device, payload["score"]), ("better-auth:user-a", device_id, -0.4))
        self.assertIn("alerts", payload)


if __name__ == "__main__":
    unittest.main()
