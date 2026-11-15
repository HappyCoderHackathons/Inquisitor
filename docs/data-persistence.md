# Data persistence

The hosted stack uses MongoDB Atlas collections. The ML service and local arcade prototype
use SQLite through `backend/account/store.py`. Both implementations scope every row to
`user_id` and `device_id`.

## Model versions

Each trained artifact is tracked as a model version with job state, algorithm name, and
full training metadata. Active selection is per user and device. Deleting a device removes
its model versions and artifact directories.

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
`model_versions`, `inference_results`, `alerts`, `windows`, and `monitoring_windows`. No hosted route
calls it yet.

Enrollment windows stay until reset or account deletion. Model artifact files on the ML
host follow the same account and device boundaries.