import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.ml.model import validate_window
from scripts.generate_sample_windows import generate_windows
from scripts.replay_windows import main, replay_window

# Schema-v4 windows, the only kind the agent endpoint accepts. Synthetic fixture, not evidence of accuracy.
FIXTURE = Path(__file__).resolve().parents[1] / "shared" / "fixtures" / "tray-capture.v4.json"


def v4_windows(count: int) -> list[dict[str, object]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[:count]


class ReplayWindowsTests(unittest.TestCase):
    def test_restamps_window_as_fresh_monitoring_for_the_paired_device(self) -> None:
        now = datetime(2026, 11, 15, 12, tzinfo=UTC)
        source = v4_windows(1)[0]
        window = replay_window(source, "device-1", "replay-abc", 7, now)
        validate_window(window)
        self.assertEqual((window["device_id"], window["session_id"], window["sequence"], window["mode"]),
                         ("device-1", "replay-abc", 7, "monitoring"))
        ended = datetime.fromisoformat(window["started_at"]).timestamp() + window["duration_ms"] / 1000
        self.assertEqual(ended, now.timestamp())
        self.assertEqual(window["features"], source["features"])

    def replay(self, windows: list[dict[str, object]]) -> tuple[MagicMock, MagicMock, MagicMock]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "windows.jsonl"
            path.write_text("".join(json.dumps(window) + "\n" for window in windows), encoding="utf-8")

            def response(*_: object, **__: object) -> MagicMock:
                opened = MagicMock()
                opened.__enter__.return_value = io.BytesIO(b'{"state": "NORMAL", "score": 0.1}')
                return opened

            argv = ["replay", str(path), "--url", "https://example.test/", "--device-id", "device-1", "--speed", "4"]
            with patch("sys.argv", argv), patch.dict(os.environ, {"INQUISITOR_DEVICE_TOKEN": "secret"}), \
                 patch("urllib.request.urlopen", side_effect=response) as urlopen, \
                 patch("time.sleep") as sleep, patch("builtins.print") as printed:
                main()
        return urlopen, sleep, printed

    def test_posts_each_window_with_device_token_and_labels_output(self) -> None:
        windows = v4_windows(2)
        urlopen, sleep, printed = self.replay(windows)
        requests = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual([request.full_url for request in requests], ["https://example.test/api/agent/windows"] * 2)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer secret")
        sent = [json.loads(request.data) for request in requests]
        self.assertEqual([window["sequence"] for window in sent], [0, 1])
        self.assertTrue(sent[0]["session_id"].startswith("replay-"))
        sleep.assert_called_once_with(windows[0]["duration_ms"] / 1000 / 4)
        self.assertTrue(all("REPLAY" in call.args[0] for call in printed.call_args_list))

    def test_refuses_windows_older_than_schema_v4_before_posting(self) -> None:
        # One v1 window anywhere in the file stops the replay before any request is sent.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "windows.jsonl"
            mixed = [*v4_windows(1), *generate_windows(49, 1)]
            path.write_text("".join(json.dumps(window) + "\n" for window in mixed), encoding="utf-8")
            argv = ["replay", str(path), "--url", "https://example.test/", "--device-id", "device-1"]
            with patch("sys.argv", argv), patch.dict(os.environ, {"INQUISITOR_DEVICE_TOKEN": "secret"}), \
                 patch("urllib.request.urlopen") as urlopen, patch("sys.stderr", io.StringIO()) as stderr, \
                 self.assertRaises(SystemExit):
                main()
        urlopen.assert_not_called()
        self.assertIn("schema v4", stderr.getvalue())
