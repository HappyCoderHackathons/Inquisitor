# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "soundfile"]
# ///
"""Cut each raw recording (vo/raw/<speaker>.wav + .words.json) into one WAV per cue id.

Each cue is located by its first and last few transcript words, in recording order.
Cut points sit between words so nothing is clipped.
"""

import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
# speaker -> [(cue id, first words, last words)]
PLAN = {
    "austin": [("c01_title", "hi this is", "is inquisitor"), ("c02_austin", "i'm austin", "system together"),
               ("c06_problem", "the password", "who enrolled"), ("c13_train", "training runs", "latest rounds"),
               ("c14_monitor", "now it's monitoring", "stays normal"), ("c18_end", "that's inquisitor", "for watching")],
    "conner": [("c04_conner", "i'm connor", "the dashboard"), ("c07_privacy", "it works on", "every field"),
               ("c09_signup", "on the website", "pairing code"), ("c11_games", "enrollment runs", "the round"),
               ("c15_switch", "this is the", "needs verification")],
    "colin": [("c05_colin", "and i'm colin", "its api"), ("c12_rounds", "the backend tracks", "your model"),
              ("c16_alert", "an alert opens", "the pc"), ("c17_devices", "you can revoke", "your account")],
    "austin2": [("c16b_gemini", "when an alert", "the screen")],
    "ian": [("c03_ian", "hi i'm ian", "in rust"), ("c08_agent", "the agent is", "admin rights"),
            ("c10_pair", "you enter that", "your pc")],
}


def norm(word: str) -> str:
    return re.sub(r"[^a-z']", "", word.lower())


def find(words: list[str], phrase: str, start: int) -> int:
    target = phrase.split()
    for i in range(start, len(words) - len(target) + 1):
        if words[i:i + len(target)] == target:
            return i
    raise LookupError(f"{phrase!r} not found after word {start}")


for speaker, cues in PLAN.items():
    audio, rate = sf.read(RAW / f"{speaker}.wav", dtype="float32")
    timed = json.loads((RAW / f"{speaker}.words.json").read_text())
    words = [norm(w["w"]) for w in timed]
    cursor = 0
    for cue, first, last in cues:
        a = find(words, first, cursor)
        b = find(words, last, a) + len(last.split()) - 1
        cursor = b + 1
        before = timed[a - 1]["e"] if a else 0.0
        after = timed[b + 1]["s"] if b + 1 < len(timed) else len(audio) / rate
        start = max(before + 0.6 * (timed[a]["s"] - before), timed[a]["s"] - 0.25)
        end = min(timed[b]["e"] + 0.6 * (after - timed[b]["e"]), timed[b]["e"] + 0.45)
        clip = audio[int(start * rate):int(end * rate)].copy()
        fade = int(0.03 * rate)
        clip[:fade] *= np.linspace(0, 1, fade)
        clip[-fade:] *= np.linspace(1, 0, fade)
        sf.write(HERE / f"{cue}.wav", clip, rate)
        print(f"{cue:12} {end - start:5.2f}s  {' '.join(w['w'] for w in timed[a:b + 1])}")
