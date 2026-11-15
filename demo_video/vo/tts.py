# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["kokoro-onnx==0.4.9", "soundfile"]
# ///
"""Placeholder VO with Kokoro (Apache-2.0): writes a WAV only for cue ids that have none, so human takes are kept."""

import json
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

HERE = Path(__file__).resolve().parent
MODEL = HERE.parent / "stack" / "run" / "tts"
VOICES = {"Austin": "am_michael", "Ian": "am_adam", "Conner": "am_eric", "Colin": "am_liam"}

kokoro = Kokoro(str(MODEL / "kokoro-v1.0.onnx"), str(MODEL / "voices-v1.0.bin"))
durations = {}
for line in json.loads((HERE / "lines.json").read_text(encoding="utf-8")):
    if (HERE / f"{line['id']}.wav").exists():
        continue
    samples, rate = kokoro.create(line["text"], voice=VOICES[line["speaker"]], speed=1.05, lang="en-us")
    loud = np.flatnonzero(np.abs(samples) > 0.01)
    samples = samples[max(loud[0] - rate // 50, 0): loud[-1] + rate // 20]
    sf.write(HERE / f"{line['id']}.wav", samples, rate)
    durations[line["id"]] = round(len(samples) / rate, 3)
    print(line["id"], durations[line["id"]])
if durations:
    known = json.loads((HERE / "durations.json").read_text()) if (HERE / "durations.json").exists() else {}
    (HERE / "durations.json").write_text(json.dumps(known | durations, indent=1))
print("total", round(sum(durations.values()), 1))
