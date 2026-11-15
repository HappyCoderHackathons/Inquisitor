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