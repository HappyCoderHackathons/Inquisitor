import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.ml.app import create_app
from backend.ml.export_weights import main as export_weights
from backend.ml.model import ModelStore, labeled_features, validate_window
from backend.ml.published import PublishedModel
from backend.ml.tray_csv import HEADER, csv_windows
from scripts.generate_sample_windows import generate_windows


def capture(scale: int = 1, shadow: bool = False) -> str:
    """Synthetic input events for contract tests, never evidence of model accuracy."""
    rows = []
    for index in range(61):
        now = index * .5
        rows.extend([(now, "keyboard,primary,press,key,,,,,"),
                     (now + .1, "keyboard,primary,release,key,100,,,,")])
        if shadow and index % 3 == 0:
            rows.extend([(now + .006, "keyboard,shadow,press,key,,,,,"),
                         (now + .106, "keyboard,shadow,release,key,100,,,,")])
        rows.append((now, f"mouse,mouse0,move,,,,{scale * (1 + index % 4)},{scale * (index % 3)},"))
        if index % 2 == 0:
            rows.append((now + .2, "mouse,mouse0,press,button_left,,,,,"))
    return ",".join(HEADER) + "\n" + "\n".join(
        f"2026-01-01T00:00:00Z,{time},{value}" for time, value in sorted(rows)
    )


class TrayMultimodalTests(unittest.TestCase):
    def test_primary_keyboard_is_explicit_and_mouse_has_separate_opt_in(self) -> None:
        with self.assertRaises(ValueError):
            csv_windows(io.StringIO(capture(shadow=True)), "pc", "session")
        window = csv_windows(io.StringIO(capture(shadow=True)), "pc", "session",
                             keyboard_source="primary", track_mouse=True)[0]
        self.assertEqual(window["schema_version"], 4)
        self.assertEqual(window["features"]["key_count"], 60)
        self.assertEqual(window["features"]["hold_sample_count"], 60)
        self.assertEqual(window["features"]["mouse_click_count"], 30)
        validate_window(window)
        keyboard_only = csv_windows(io.StringIO(capture()), "pc", "session")[0]
        self.assertEqual(keyboard_only["schema_version"], 3)
        with self.assertRaises(ValueError):
            csv_windows(io.StringIO(capture()), "pc", "session", keyboard_source="absent")

    def test_other_tray_device_types_are_skipped_but_unknown_types_rejected(self) -> None:
        lines = capture().splitlines()
        extra = [f"2026-01-01T00:00:00Z,{index * .5 + .05},{kind},hid{index},axis,axis_x,,,,,7"
                 for index, kind in enumerate(("touchpad", "touchscreen", "pen", "consumer", "keypad",
                                               "digitizer", "hid", "gamepad"))]
        rows = sorted(lines[1:] + extra, key=lambda row: float(row.split(",")[1]))
        mixed = "\n".join([lines[0], *rows])
        self.assertEqual(csv_windows(io.StringIO(mixed), "pc", "session"),
                         csv_windows(io.StringIO(capture()), "pc", "session"))
        with self.assertRaises(ValueError):
            csv_windows(io.StringIO(mixed.replace(",pen,", ",webcam,")), "pc", "session")

    def test_idle_stretches_produce_no_empty_windows(self) -> None:
        lines = capture().splitlines()
        later = [f"{iso},{float(time) + 300},{rest}" for iso, time, rest in (row.split(",", 2) for row in lines[1:])]
        idle = "\n".join([*lines, *later])
        for track_mouse in (False, True):
            windows = csv_windows(io.StringIO(idle), "pc", "session", track_mouse=track_mouse)
            # Windows 2 to 9 cover the five idle minutes and are skipped, not zero-filled.
            self.assertEqual([window["sequence"] for window in windows], [0, 1, 10])
            self.assertEqual(windows[2]["started_at"], "2026-01-01T00:05:00+00:00")
            self.assertTrue(all(window["features"]["key_count"] for window in windows))

    def test_model_inputs_do_not_change_with_uniform_mouse_resolution_scaling(self) -> None:
        windows = [csv_windows(io.StringIO(capture(scale)), "pc", "session", track_mouse=True)[0] for scale in (1, 10)]
        matrix = labeled_features(windows, "tray-multimodal")
        self.assertEqual(matrix.shape, (2, 13))
        np.testing.assert_allclose(matrix[0], matrix[1], rtol=1e-12, atol=1e-12)
        invalid = copy.deepcopy(windows[0])
        invalid["features"]["raw_events"] = []
        with self.assertRaises(ValueError):
            validate_window(invalid)

    @unittest.skipUnless(os.environ.get("ML_ALLOW_TRAINING") == "1", "fitting runs only on the designated host")
    def test_one_model_serves_two_allowed_devices_and_keeps_other_accounts_out(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            template = csv_windows(io.StringIO(capture()), "pc-1", "session", "enrollment", track_mouse=True)[0]
            groups = [[], []]
            for label, target in enumerate(groups):
                for device in ("pc-1", "pc-2"):
                    for index in range(10):
                        window = copy.deepcopy(template)
                        window.update(device_id=device, session_id=f"synthetic-{label}-{device}", sequence=index)
                        window["features"].update(hold_std_ms=10 + index + label * 150,
                                                  mouse_click_count=10 + index + label * 100)
                        target.append(window)
            store = ModelStore(folder / "models")
            metadata = store.train(groups[0], "synthetic-user", "pc-1", None, profile="tray-multimodal",
                                   impostor_values=groups[1], device_ids=["pc-1", "pc-2"])
            self.assertEqual(metadata["device_ids"], ["pc-1", "pc-2"])
            self.assertEqual(len(metadata["feature_names"]), 13)
            artifact = folder / "weights.json"
            with patch("sys.argv", ["export", "--model-dir", str(folder / "models" / metadata["model_id"]),
                                    "--output", str(artifact), "--version", "synthetic-multimodal"]):
                export_weights()
            published = PublishedModel(artifact)
            self.assertEqual(metadata["schema_sha256"], published.artifact["schema_sha256"])
            (folder / "token").write_text("test-only-" + "a" * 32)
            with patch.dict(os.environ, {"ML_TOKEN_FILE": str(folder / "token"), "ML_DATA_DIR": str(folder / "models"),
                                         "ML_PUBLISHED_MODEL_FILE": str(artifact), "ML_PUBLISHED_USER_ID": "synthetic-user",
                                         "ML_PUBLISHED_DEVICE_ID": "pc-1", "ML_PUBLISHED_MODEL_ID": metadata["model_id"]}):
                client = create_app().test_client()
                for device in ("pc-1", "pc-2"):
                    headers = {"Authorization": "Bearer test-only-" + "a" * 32,
                               "X-User-ID": "synthetic-user", "X-Device-ID": device}
                    self.assertEqual(client.get("/model", headers=headers).json["model_version"], "synthetic-multimodal")
                    for window in [row for row in groups[0] + groups[1] if row["device_id"] == device]:
                        result = client.post(f"/models/{metadata['model_id']}/score", headers=headers, json={"window": window})
                        self.assertEqual(result.status_code, 200, result.json)
                        self.assertAlmostEqual(result.json["score"], published.score(window, device)["score"], places=12)
                        deployed = client.post("/score", headers=headers, json={"window": window})
                        self.assertEqual(deployed.status_code, 200, deployed.json)
                        self.assertAlmostEqual(deployed.json["score"], result.json["score"], places=12)
                    self.assertEqual(client.put(f"/models/{metadata['model_id']}/active", headers=headers).status_code, 200)
                    self.assertEqual(client.get("/models/active", headers=headers).json["model_id"], metadata["model_id"])
                    wrong = {**headers, "X-User-ID": "another-account"}
                    self.assertEqual(client.get(f"/models/{metadata['model_id']}", headers=wrong).status_code, 403)
                    self.assertEqual(client.get("/model", headers=wrong).status_code, 403)
                outside = {**headers, "X-Device-ID": "pc-3"}
                self.assertEqual(client.get(f"/models/{metadata['model_id']}", headers=outside).status_code, 403)
                self.assertEqual(client.get("/model", headers=outside).status_code, 403)
                mismatched = {**headers, "X-Device-ID": "pc-1"}
                pc_two = next(row for row in groups[0] if row["device_id"] == "pc-2")
                self.assertEqual(client.post(f"/models/{metadata['model_id']}/score", headers=mismatched,
                                             json={"window": pc_two}).status_code, 403)
                self.assertEqual(client.post("/enrollment/reset", headers=headers).status_code, 200)
                for device in ("pc-1", "pc-2"):
                    owner = {**headers, "X-Device-ID": device}
                    self.assertEqual(client.get("/models/active", headers=owner).status_code, 404)
                    self.assertEqual(client.get("/model", headers=owner).status_code, 404)
                restarted = create_app().test_client()
                self.assertEqual(restarted.get("/health", headers=headers).status_code, 200)
                self.assertEqual(restarted.get("/model", headers=headers).status_code, 503)
            legacy = generate_windows(49, 1)[0]
            self.assertEqual(published.score(legacy, "synthetic-device")["state"], "INSUFFICIENT_DATA")
            self.assertNotIn("device_ids", json.loads(artifact.read_text()))
            with self.assertRaises(ValueError):
                store.train(groups[0], "synthetic-user", "pc-1", None, profile="tray-multimodal",
                            impostor_values=groups[1], device_ids=["pc-1"])


if __name__ == "__main__":
    unittest.main()
