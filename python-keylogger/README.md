# keylogger

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
