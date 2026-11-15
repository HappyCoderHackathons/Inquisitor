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

## Deletion cascade

| Trigger | Removed data |
| --- | --- |
| `DELETE /me/data` | All windows, inference results, alerts, and models for the account |
| Device-scoped delete helper | Windows, monitoring rows, inference results, alerts, and models for that device |
| `POST /retention/purge` | Monitoring windows, inference results, and alerts older than the retention period |

Enrollment windows stay until reset or account deletion. Model artifact files on the ML
host follow the same account and device boundaries.