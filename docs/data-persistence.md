# Data persistence

The hosted stack uses MongoDB Atlas collections. Arcade game rounds and browser-collected
feature windows live in `arcade_samples` and `arcade_feature_windows`. The local prototype
account store in `backend/account/store.py` still uses SQLite until that migration lands.
Account, device, window, score, alert, and model rows are scoped to `user_id` and
`device_id`. The monitoring retention period is one process-wide row. A purge deletes
monitoring rows older than that period for every account on that store.

## Model versions

Each trained artifact is tracked as a model version with job state, algorithm name, and
full training metadata. Active selection is per user and device. Deleting a device removes
its model versions and artifact directories.

Each device enrolls and trains its own baseline by default. A second device on the same
account cannot read, score with, or activate the first device's model, and activating or
deleting one device's model leaves the other device's active model in place. The only
cross-device model is the opt-in `tray-multimodal` profile, which is trained with an
explicit `device_ids` allowlist (see [multimodal-model.md](multimodal-model.md)).

## Inference results and alerts

Monitoring windows produce an inference result with model ID, score, threshold, and
classification (`NORMAL`, `UNUSUAL`, or `INSUFFICIENT_DATA`). `UNUSUAL` also creates an
alert row for dashboard review.

## Foreign keys

In SQLite, `devices` holds each `(user_id, device_id)` pair. `windows`, `inference_results`,
`alerts`, and `models` reference it with `ON DELETE CASCADE`, so deleting a device row
removes its dependent rows. Opening an older database rebuilds those tables with the keys
and keeps existing rows. Atlas has no foreign keys; every document stores `user_id` and
`device_id`, and the application deletes dependents explicitly.

## Deletion cascade

| Trigger | Removed data |
| --- | --- |
| `DELETE /me/data` | All windows, inference results, alerts, and models for the account |
| `AccountStore.delete_device` | Windows, inference results, alerts, and models for that device |
| `POST /retention/purge` | Monitoring windows, inference results, and alerts older than the retention period |

On Atlas, `delete_device_records` in `Frontend/atlas_persistence.py` applies the same device cascade to
`model_versions`, `inference_results`, `alerts`, `windows`, and `monitoring_windows`. The hosted
`DELETE /api/me/data` route deletes the account on the ML service first, then runs that cascade for
each paired device, removes onboarding rounds, and revokes every device credential. If the ML service
is unavailable it returns 502 and deletes nothing. `POST /api/devices/<id>/revoke` (or `DELETE /api/devices/<id>`) revokes one device
without deleting data. Both are on the setup page.

Enrollment windows stay until reset or account deletion. Model artifact files on the ML
host follow the same account and device boundaries.