# Demo video

Under-3-minute demo of Inquisitor, built from real recordings of the app and the 0.7.2 Windows agent.

- [script.md](script.md): playbook (time, picture, source, VO, on-screen text), claims check, how the footage was made
- [voice-over.md](voice-over.md): the same lines split by speaker, with a time budget per line
- `out/inquisitor-demo.mp4`: current render with the team's VO

## Swap in our voices

1. Record each line as `vo/<cue id>.wav`, for example `vo/c06_problem.wav`, replacing the placeholder file.
   A whole read-through works too: save it as `vo/raw/<speaker>.wav`, run `uv run --python 3.13 vo/transcribe.py vo/raw/<speaker>.wav`,
   then `uv run vo/split.py` (the cue boundaries are in `PLAN`). `vo/tts.py` only fills cues that have no WAV.
2. Drop photos in `assets/team/` as `austin.jpg`, `ian.jpg`, `conner.jpg`, `colin.jpg` (square crop is automatic).
   Without a photo the team card shows initials.
3. `uv run build/build.py` from `demo_video/`. It re-times every scene from the new line lengths, writes
   `cues.json`, and stops with an error if the cut would pass 3:00.
4. Review: `out/inquisitor-demo.mp4`, plus the checks in the video-production skill (contact sheet, midpoint frames,
   loudness around -14 LUFS).

## Layout

| Path | What |
| --- | --- |
| `stack/` | Throwaway local stack (`start.sh`, `reset.sh`) and Windows port shims |
| `capture/record.mjs` | Playwright takes plus the stand-in agent that posts synthetic windows |
| `capture/agent_capture.py` | gdigrab capture of the agent setup wizard and pairing window |
| `takes/` | Raw takes (git-ignored) |
| `vo/` | `lines.json` (source of truth for VO), `tts.py` placeholder generator, WAVs (git-ignored) |
| `build/build.py` | Cards, captions, picture, cues, ducked music mix, final mux |
| `assets/` | Logo, music, team photos |

## Re-recording

```sh
bash stack/start.sh                                      # leave running
bash stack/reset.sh
gh release download --pattern "*windows-x64.exe*" -D stack/run/agent-exe
python capture/agent_capture.py wizard
python capture/agent_capture.py pair &                   # waits for the pairing code
cd capture && npm ci && REAL_AGENT=1 node record.mjs
```

The agent windows take keyboard focus while they are recorded. Do not type during the agent steps: a stray Enter
clicks the default button. `agent_capture.py` removes any `HKCU` install entries that point into its sandbox.

## Music

"Plains of Luminescence" by vitalezzz, CC0, from [OpenGameArt](https://opengameart.org/node/180398),
re-encoded from the original WAV to Ogg Vorbis (q7) to keep the repository small.
