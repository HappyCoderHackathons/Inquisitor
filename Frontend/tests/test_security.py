import tempfile
import unittest
from pathlib import Path

import mongomock

import server

SAMPLE = {
    "game": "aim",
    "duration_ms": 1000,
    "input_count": 2,
    "keyboard_count": 1,
    "controller_count": 0,
    "mean_interval_ms": 10.0,
    "interval_std_ms": 1.0,
    "mean_hold_ms": 20.0,
    "score": 1,
    "source": "test",
}


class SecurityHeaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_database = server._arcade_database
        self.previous = server.ARTIFACT_ROOT
        self.previous_account = server.account
        server.set_arcade_database(mongomock.MongoClient(tz_aware=True).db)
        server.ARTIFACT_ROOT = Path(self.temporary.name) / "account-artifacts"
        server.account = None
        server.initialize()
        self.client = server.app.test_client()

    def tearDown(self):
        server.set_arcade_database(self.previous_database)
        server.ARTIFACT_ROOT = self.previous
        server.account = self.previous_account
        self.temporary.cleanup()

    def assert_browser_policy(self, response, *, hsts: bool):
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertIn("style-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        if hsts:
            self.assertEqual(response.headers["Strict-Transport-Security"], "max-age=31536000; includeSubDomains")
        else:
            self.assertNotIn("Strict-Transport-Security", response.headers)

    def test_http_pages_omit_hsts(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assert_browser_policy(response, hsts=False)
        self.assertIn('href="/static/style.css"', response.get_data(as_text=True))

    def test_https_pages_send_hsts(self):
        response = self.client.get("/privacy", base_url="https://localhost")
        self.assertEqual(response.status_code, 200)
        self.assert_browser_policy(response, hsts=True)

    def test_cross_site_mutations_do_not_write(self):
        attacker = {"Origin": "https://attacker.example"}
        self.assertEqual(self.client.post("/api/samples", json=SAMPLE, headers=attacker).status_code, 403)
        self.assertEqual(
            self.client.post("/api/samples", json=SAMPLE, headers={"Sec-Fetch-Site": "cross-site"}).status_code,
            403,
        )
        self.assertEqual(self.client.get("/api/summary").get_json()["samples"], 0)
        saved = self.client.post("/api/samples", json=SAMPLE)
        self.assertEqual(saved.status_code, 201)
        self.assert_browser_policy(saved, hsts=False)
        matched = self.client.post("/api/samples", json=SAMPLE, headers={"Origin": "http://localhost"})
        self.assertEqual(matched.status_code, 201)
        blocked = self.client.delete("/api/samples", headers=attacker)
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.client.get("/api/summary").get_json()["samples"], 2)
