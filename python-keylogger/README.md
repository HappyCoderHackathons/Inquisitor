# keylogger

## Aggregate-only model enrollment

From the repository root, run:

```powershell
uv run --project python-keylogger keylogger-enroll
```

This command uses the keylogger's input library but saves only schema-valid numeric
feature windows in the ignored `.private/enrollment/` directory. It never writes
key identities, typed text, categories, or ordered events. Auto-repeat is ignored;
key identity is held in memory only until release.

Collection waits for Enter, then captures 20 training windows. It stops and waits
for a separate Enter before collecting 10 validation windows. Type naturally in
a text editor. Each demo window needs at least 10 seconds, 30 presses, and completed
hold samples. Keys still held at a boundary do not prevent saving; their cross-window
holds are excluded. Esc or Ctrl+C stops collection; each session is capped at
10 minutes. Interrupted collections do not produce a completed enrollment manifest.
These short windows are a demo choice, not independent biometric evidence.

If training collection is interrupted, use `keylogger-enroll --resume <file.jsonl>`
to preserve its completed windows. Resume accepts a single training session only;
the validation session is collected separately afterward.

The generated device ID is a local enrollment identity, not an application-paired
device. Upload and training require the separately authenticated remote service.
The legacy CSV commands below are not the aggregate-only enrollment path.

For a frozen-model test, use `keylogger-enroll --evaluate`. It collects 20 new
genuine-user windows, pauses, then collects 20 from a second consenting person.
Session labels are saved in a separate ignored manifest rather than added to the
feature schema. Evaluation does not train or change the model threshold.
The manifest's `session_testers` map labels each session with the anonymous
`enrolled` or `second-user` tester role.

Focused checks: `uv run --project python-keylogger python -m unittest discover -s python-keylogger/tests`.

## Legacy CSV collector

A small, **opt-in** keyboard-capture tool for the InputPrint project. It writes a
*live* CSV log (flushed after every event) of keyboard activity.

> ⚠️ Run only on a machine you own or have explicit permission to monitor.
> Captured logs may contain sensitive data — they are git-ignored by default.

## Modes

| Mode | Flag | What it records |
|------|------|-----------------|
| **Timing-only** (default) | *(none)* | Press/release timestamps, key-hold duration, and flight time between keys. **Key identity is masked** into coarse categories (`alpha`, `digit`, `space`, …). No characters, key names, or ordered sequences — matching InputPrint's privacy guarantee. |
| **Raw** | `--raw` | Everything above **plus the actual key** that was pressed. Stores typed content; use only for local learning/debugging. |

## Setup

```powershell
uv sync
```

## Run

```powershell
# timing-only, logs to keystrokes.log
uv run keylogger

# choose an output file
uv run keylogger --out sessions/alice.log

# raw mode (records actual keys)
uv run keylogger --raw
```

Press **Esc** (or **Ctrl+C**) to stop.

## Watch the log live

In a second terminal:

```powershell
Get-Content keystrokes.log -Wait -Tail 20
```

## Log format

CSV with header: `iso_time,monotonic_s,io_type,device_id,event,key,hold_ms,flight_ms`

- `iso_time` — wall-clock timestamp (millisecond precision)
- `monotonic_s` — high-resolution monotonic clock, for precise deltas
- `io_type` — input source: `keyboard` (mouse/controller reserved for later)
- `device_id` — host machine id (NIC MAC via `uuid.getnode()`), constant per session. **Not** a per-physical-device id — pynput can't see which keyboard/mouse fired an event.
- `event` — `press` or `release`
- `key` — masked category (timing-only) or the actual key (`--raw`)
- `hold_ms` — on `release`: how long the key was held
- `flight_ms` — on `press`: time since the previous key press
