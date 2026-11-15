# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["faster-whisper", "soundfile"]
# ///
"""Transcribe raw VO recordings with word timestamps so takes can be matched to cue ids."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

model = WhisperModel("medium.en", device="cpu", compute_type="int8")
for path in map(Path, sys.argv[1:]):
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    segments, _ = model.transcribe(np.frombuffer(pcm, np.float32), word_timestamps=True, vad_filter=False)
    words = [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for seg in segments for w in seg.words]
    out = path.with_suffix(".words.json")
    out.write_text(json.dumps(words))
    print(path.name, len(words), "words")
