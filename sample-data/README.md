# Synthetic feature windows

These are generated integration fixtures, not collected input or evidence of model
accuracy. They contain numeric aggregates plus synthetic device/session metadata.
No characters, key identities, or ordered events are written.

With Python 3.13+ and uv installed, run from the repository root:

```powershell
uv run scripts/generate_sample_windows.py
```

The script declares its own JSON Schema validation dependency. Defaults are seed
49, ten windows, and `sample-data/windows.jsonl`. Existing output is never overwritten.
To generate another file:

```powershell
uv run scripts/generate_sample_windows.py --seed 123 --count 30 --output sample-data/other.jsonl
```

The same arguments and Python/dependency versions produce identical UTF-8 JSONL
bytes. Timestamps start at the fixed synthetic origin `2000-01-01T00:00:00Z` and
advance by each window's duration. One device/session is used, with sequence numbers
starting at zero. Windows have 40 to 80 presses and last at least 30 seconds.

The shared [v1 schema](../shared/feature-window-v1.schema.json) follows the eleven
features in the project spec's example payload and adds `schema_version: 1`.
All fields are required; extra fields are rejected, including nested feature fields.
IDs are nonempty strings, timestamps use RFC 3339, sequence numbers are nonnegative
integers, and durations are positive integer milliseconds. Modes are `enrollment`
and `monitoring`.

Feature counts are nonnegative integers, timing statistics are nonnegative
milliseconds, and fractions range from zero to one. No empirical upper limits are
claimed. Timing standard deviations use the population formula; p90 uses the
nearest-rank method. Pauses are inter-press gaps greater than 1,000 ms, and their
full durations contribute to `pause_fraction`. Active time for
`presses_per_second` is window duration minus those pauses. Bursts are separated
by pauses. Overlap is the fraction of successive presses where the previous hold
exceeds the intervening gap. Leading/trailing silence is not an inter-press pause.

The generator validates every window with timestamp format checking before
writing. JSON Schema checks field types and bounds, not relationships between
aggregates. Training eligibility and real agent/backend integration are separate work.

Run focused checks:

```powershell
uv run --with 'jsonschema[format]>=4.18,<5' python -m unittest discover -s tests
```

## Replaying recorded windows

`scripts/replay_windows.py` is the demo fallback for when live capture fails. It posts
previously recorded aggregate windows to the hosted `/api/agent/windows` route, so they
go through the same validation, storage, and scoring as live monitoring windows.

```powershell
$env:INQUISITOR_DEVICE_TOKEN = "<paired device token>"
uv run --project backend python scripts/replay_windows.py C:\demo\guest.jsonl `
  --url https://<hosted app> --device-id <paired device ID> --speed 4
```

Each window is rewritten as a `monitoring` window for the paired device, gets the next
sequence number in a new `replay-<random>` session, and is restamped to end now. Features
are sent unchanged. Stored results keep that session prefix, so replayed rows can be told
apart from live ones. `--speed 1` waits each window's recorded duration; higher values
shorten the wait. Ingestion accepts 60 windows per device per minute, and the script stops
at the first rejected window.

Every output line starts with `REPLAY (recorded windows, not live input)`. Keep that
label visible during a demo and say that replay proves integration, not live detection.
Record replay files only from consenting people, aggregate-only, and keep them outside
the repository. Synthetic files from the generator above are fine for rehearsing.
