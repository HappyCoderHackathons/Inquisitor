# Privacy

Inquisitor collects aggregate timing features, not typed content or pointer targets.

## What is collected

- Keyboard timing statistics per rolling window (press counts, hold variability, overlap)
- Mouse motion and click timing aggregates. The Windows agent always sends them with the keyboard
  aggregates (schema v4); the browser arcade's mouse collection remains a separate opt-in
- Account email and device metadata needed for pairing and authorization

## Feature window fields

Every window that leaves the collector matches one of the schemas in
[`shared/`](../shared). Schemas reject additional properties, so these are the
only fields a window can carry.

| Field | Contents |
| --- | --- |
| `schema_version` | 1, 2, 3, or 4. The Windows agent sends only 4 |
| `device_id`, `session_id` | Opaque identifiers issued at pairing and per collection session |
| `sequence` | Window counter within a session |
| `mode` | `enrollment` or `monitoring` |
| `started_at`, `duration_ms` | Window start time and length |
| `features` (v1, v2) | `key_count`, `presses_per_second`, `hold_mean_ms`, `hold_std_ms`, `press_interval_mean_ms`, `press_interval_std_ms`, `press_interval_p90_ms`, `press_overlap_fraction`, `burst_count`, `pause_count`, `pause_fraction` |
| `mouse_features` (v2) | `move_count`, `speed_mean_px_s`, `speed_std_px_s`, `acceleration_mean_px_s2`, `acceleration_std_px_s2`, `turn_angle_mean_rad`, `click_count`, `click_interval_mean_ms`, `click_interval_std_ms` |
| `features` (v3) | `key_count`, `hold_sample_count`, `hold_mean_ms`, `hold_std_ms`, `press_interval_mean_ms`, `press_interval_std_ms` |
| `features` (v4) | The v3 fields plus `mouse_sample_count`, `mouse_speed_mean_counts_s`, `mouse_speed_std_counts_s`, `mouse_turn_mean_rad`, `mouse_turn_std_rad`, `mouse_path_straightness`, `mouse_click_count`, `mouse_click_interval_mean_ms`, `mouse_click_interval_std_ms` |

Mouse values are speeds, angles, counts, and intervals. None of them is a position.

## What is not collected

- Key identities, typed characters, or ordered key sequences
- Absolute pointer coordinates, click targets, or window titles
- Raw event logs outside transient in-memory pairing

## Storage and retention

Enrollment windows support model training. Monitoring windows, inference results, and
alerts follow the configured retention period on the ML service. `DELETE /me/data` on the
ML service and the local arcade removes an account's windows, results, alerts, and models.
On the hosted service, "Delete my data" on the setup page calls `DELETE /api/me/data`, which
removes the same data from the ML service and Atlas and revokes every paired agent. Hosted
Atlas monitoring rows have no automatic retention purge yet.

## Remote processing

Training and inference run on the designated remote ML host. Enrollment windows leave the
user's device only after explicit onboarding. Credentials for that host never ship in the
client binary.

## Automated privacy scanning

[`scripts/scan_privacy_payloads.py`](../scripts/scan_privacy_payloads.py) scans JSON, JSONL, and
JSON audit log lines for prohibited content. It fails closed on:

- Forbidden field names (key codes, typed text fields, absolute pointer coordinates, window
  titles, URL fields, ordered raw event lists)
- Feature windows whose fields fall outside the shared `feature-window-v1` through v4 schema
  allowlists
- Audit records that include fields beyond the metadata emitted by `hosted.audit_event`
- String values that contain `http://` or `https://`

The Backend GitHub Actions workflow runs this scanner on
[`shared/fixtures/tray-capture.v4.json`](../shared/fixtures/tray-capture.v4.json) and
[`sample-data/windows.jsonl`](../sample-data/windows.jsonl). Unit tests in
[`tests/test_privacy_scan.py`](../tests/test_privacy_scan.py) cover additional cases.

## Verified agent link capture (local, not production HTTPS)

To inspect what the hosted agent routes accept and persist without a deployed site, use the
in-process capture helper against [`scripts/agent_link_test_server.py`](../scripts/agent_link_test_server.py):

```text
uv run --no-project --python 3.13 --with-requirements Frontend/requirements.txt ^
  --with "mongomock>=4.3,<5" python scripts/capture_agent_link_artifacts.py
uv run --project backend python scripts/scan_privacy_payloads.py scripts/fixtures/agent-link-captured.json
```

What this verifies:

- Pairing with the test-only code, posting a schema v4 monitoring window, and reading persisted
  documents through the `GET /__test/artifacts` helper route added for privacy checks
- Running the privacy scanner on the captured upload request, persisted `window` payloads, and
  INFO audit lines collected during the flow

Limits (not verified by this path):

- No TLS termination or production MongoDB Atlas persistence
- No live Rust tray binary or WSS session; this replays the HTTP upload path only
- Ignored Rust `live_*` tests still require a manual test server and real agent for full transport
  coverage