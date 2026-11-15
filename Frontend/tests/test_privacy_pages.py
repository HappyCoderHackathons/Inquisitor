import html
import json
import unittest
from pathlib import Path

import server

ROOT = Path(__file__).parents[2]
V2_SCHEMA = json.loads((ROOT / "shared" / "feature-window-v2.schema.json").read_text(encoding="utf-8"))
FIXTURE = json.loads((ROOT / "shared" / "fixtures" / "feature-window-v2.json").read_text(encoding="utf-8"))


class PrivacyPageTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_landing_states_the_product_limits(self):
        page = self.client.get("/").get_data(as_text=True)

        self.assertIn('href="/onboarding?mode=signup">Create Account</a>', page)
        self.assertIn('href="/onboarding">Log In</a>', page)
        self.assertIn("A Windows agent is required.", page)
        self.assertIn("Timing summaries flow forward.", page)
        self.assertIn("It detects anomalies, not identity.", page)
        self.assertIn("does not replace Windows login", page)
        self.assertNotIn("completely secure", page.casefold())
        self.assertNotIn("guaranteed", page.casefold())
        for path in ("/onboarding", "/onboarding?mode=signup"):
            account = self.client.get(path)
            self.assertEqual(account.status_code, 200)
            self.assertIn("Sign-in is not configured", account.get_data(as_text=True))

    def test_privacy_page_lists_the_accepted_contract(self):
        page = self.client.get("/privacy").get_data(as_text=True)
        sample = FIXTURE["valid"]["enrollment"]

        self.assertEqual(sample, server.SAMPLE_FEATURE_WINDOW)
        self.assertIn(json.dumps(sample, indent=2), html.unescape(page))
        self.assertIn("synthetic contract data", page)
        for name in (*V2_SCHEMA["properties"], *V2_SCHEMA["properties"]["features"]["properties"],
                     *V2_SCHEMA["properties"]["mouse_features"]["properties"]):
            self.assertIn(f"<code>{name}</code>", page)
        for unit in ("milliseconds", "presses per second", "fraction, 0 to 1", "pixels per second",
                     "pixels per second squared", "radians, 0 to π"):
            self.assertIn(unit, page)
        for prohibited in ("typed text", "passwords", "clipboard contents", "key names",
                           "Ordered keydown and keyup", "Screenshots", "browser URLs",
                           "absolute pointer positions", "without their informed consent"):
            self.assertIn(prohibited, page)
        self.assertIn("not an identity", page)
        self.assertIn("are not accepted and are not collected", page)
        self.assertNotIn("hold_p50_ms", page)


if __name__ == "__main__":
    unittest.main()
