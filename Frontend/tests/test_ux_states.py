import unittest
from pathlib import Path

import server

ROOT = Path(__file__).parents[2]


def channel(value: str) -> float:
    color = int(value, 16) / 255
    return color / 12.92 if color <= 0.04045 else ((color + 0.055) / 1.055) ** 2.4


def contrast(left: str, right: str) -> float:
    def luminance(hex_color: str) -> float:
        red, green, blue = hex_color[0:2], hex_color[2:4], hex_color[4:6]
        return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)

    lighter = max(luminance(left), luminance(right))
    darker = min(luminance(left), luminance(right))
    return (lighter + 0.05) / (darker + 0.05)


class UxStateTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_dashboard_copy_stays_inside_the_data(self):
        page = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn("anomaly signals", page)
        self.assertIn("Needs verification", page)
        self.assertIn("does not name a person", page)
        self.assertIn("not proof of identity", page)
        self.assertIn('id="collectionStatus"', page)
        self.assertIn('id="dataActionStatus"', page)
        self.assertNotIn("Non-user activity", page)
        self.assertNotIn("activity detections", page)
        self.assertNotIn("identify or confirm another person", page)
        self.assertNotIn("%", page)

    def test_arcade_exposes_status_and_a_keyboard_dialog(self):
        page = self.client.get("/arcade").get_data(as_text=True)
        script = (ROOT / "Frontend" / "frontend" / "app.ts").read_text(encoding="utf-8")

        self.assertIn('id="arcadeStatus"', page)
        self.assertIn('role="status"', page)
        self.assertIn("Loading recent games", page)
        self.assertIn('role="dialog"', page)
        self.assertIn('aria-modal="true"', page)
        self.assertIn('tabindex="-1"', page)
        for phrase in (
            "Loading recent games…",
            "Could not load recent games. The server did not respond.",
            "Saving this round…",
            "Could not save this round",
            "Clearing saved rounds…",
            "Could not clear saved rounds",
            "Could not report this round",
            "Could not check setup",
            "Aim with the mouse or Q and E",
            "Fire with click, Space, or F",
        ):
            self.assertIn(phrase, script)

    def test_setup_and_dashboard_fetches_name_loading_and_disconnects(self):
        signed_in = server.app.jinja_env.get_template("onboarding.html").render(signed_in=True)
        onboarding = (ROOT / "Frontend" / "frontend" / "onboarding.ts").read_text(encoding="utf-8")
        dashboard = (ROOT / "Frontend" / "frontend" / "dashboard.ts").read_text(encoding="utf-8")

        self.assertIn("Loading setup status", signed_in)
        self.assertIn('id="authStatus"', signed_in)
        self.assertIn("Could not load setup status. The server did not respond.", onboarding)
        self.assertIn("Could not create a code. The server did not respond.", onboarding)
        self.assertIn("Could not start training. The server did not respond.", onboarding)
        self.assertIn("Please wait.", onboarding)
        self.assertIn("Loading saved windows…", dashboard)
        self.assertIn("Could not load saved windows. The server did not respond.", dashboard)
        self.assertIn("Could not reach the server. Nothing was changed.", dashboard)
        self.assertIn("Could not save feature window. The server did not respond.", dashboard)

    def test_supported_evaluation_numbers_keep_their_limits(self):
        evaluation = (ROOT / "docs" / "model-evaluation.md").read_text(encoding="utf-8")
        handoff = (ROOT / "Frontend" / "INTEGRATION_HANDOFF.md").read_text(encoding="utf-8")

        self.assertIn("96.67%", evaluation)
        self.assertIn("not an untouched independent final test", evaluation)
        self.assertNotIn("non-user activity", handoff)
        self.assertIn("not proof of identity", handoff)

    def test_visible_text_and_focus_meet_contrast(self):
        css = (ROOT / "Frontend" / "static" / "style.css").read_text(encoding="utf-8")

        self.assertIn("select:focus-visible", css)
        self.assertIn("textarea:focus-visible", css)
        self.assertIn(".game-stage:focus-visible", css)
        self.assertIn("#948D9E", css)
        self.assertIn("#7E6C9A", css)
        self.assertGreaterEqual(contrast("F7F4FC", "09070D"), 4.5)
        self.assertGreaterEqual(contrast("B9B3C4", "09070D"), 4.5)
        self.assertGreaterEqual(contrast("F7F4FC", "7C3AED"), 4.5)
        self.assertGreaterEqual(contrast("948D9E", "211B2A"), 4.5)
        self.assertGreaterEqual(contrast("7E6C9A", "100D14"), 3)
        self.assertGreaterEqual(contrast("A3E635", "09070D"), 3)


if __name__ == "__main__":
    unittest.main()
