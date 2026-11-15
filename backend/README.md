# Remote ML service

Python 3.13+, Flask, and one selected model per user/device. The backend
uses schema-v4 native keyboard/mouse aggregates for the deployed model;
legacy schemas remain available for archived profiles.
Training and inference belong on the designated remote model host. Hostnames,
accounts, and credentials must stay in local/server configuration.

The trained numeric weights are checked in at
[`artifacts/tray-two-device-v1.json`](artifacts/tray-two-device-v1.json).
For inference without retraining, follow the [published API deployment guide](deploy/README.md).

The deployed shared two-computer model is
[`artifacts/tray-two-device-v1.json`](artifacts/tray-two-device-v1.json). Its
`tray-multimodal` profile consumes schema-v4 native keyboard/mouse aggregates and
supports one owned model with an explicit device allowlist. Earlier numeric models
are retired under [`archive_models/`](../archive_models/). See the
[model card and CSV conversion instructions](../docs/multimodal-model.md).

From the repository root, install the locked environment:

```sh
uv sync --project backend --locked
```

Configure `ML_TOKEN_FILE` with the path to a dedicated service token file (at least
32 URL-safe characters), and `ML_DATA_DIR` with a private artifact directory.
Missing credentials prevent startup. Never use an SSH password as the API token.
Set `ML_ALLOW_TRAINING=1` only on the designated training host.

Start on remote loopback using a production server:

```sh
uv run --project backend gunicorn 'backend.ml.app:create_app()' --bind 127.0.0.1:8765 --workers 1 --threads 4
```

Reach it through authenticated HTTPS or a verified SSH tunnel. Do not bind the
service to a public interface. The browser and collector call the application
backend, which checks the logged-in user or paired device before forwarding work.

Every request requires `Authorization: Bearer <service-token>`. Model endpoints
also require `X-User-ID` and `X-Device-ID`, derived by the trusted application
backend from its authorized principal. These headers are not a replacement for
the application's user/device authentication.

| Endpoint | Request | Result |
| --- | --- | --- |
| `GET /health` | No body | Authenticated dependency readiness; returns database and model-store availability, HTTP 503 if either is unavailable |
| `GET /model` | Configured published owner/device headers | Published model version, hash, threshold, and summary |
| `POST /score` | `window` object and configured owner/device headers | Published model inference without a private model ID |
| `POST /models/train` | `windows` array and `validation_session_id` or `validation_session_ids`; optional `profile` | Ready model metadata, HTTP 201 |
| `PUT /models/{id}/active` | No body | Makes an owned model the active model for this user/device |
| `GET /models/active` | No body | Active model metadata, or 404 if none is active |
| `POST /models/active/score` | `window` object | Score using the active model |
| `GET /models/{id}` | No body | Owned model metadata |
| `POST /models/{id}/score` | `window` object | Score, threshold, model ID, and state |
| `POST /windows` | `window` object | Stores one authorized feature window, HTTP 201 |
| `POST /models/active/deactivate` | No body | Leaves the model files in place and clears the active model |
| `POST /enrollment/reset` | No body | Deletes this device's enrollment windows and active model artifact |
| `DELETE /me/data` | No body | Deletes this account's windows, scores, alerts, and model artifact files |
| `GET /retention` | No body | Monitoring retention period in seconds |
| `PUT /retention` | `monitoring_retention_seconds` | Replaces the retention period. The initial value comes from `INQUISITOR_MONITORING_RETENTION_SECONDS`, defaulting to 604800 |
| `POST /retention/purge` | No body | Deletes monitoring windows, inference results, and alerts older than the configured period |
| `DELETE /models/{id}` | No body | Removes owned artifacts and cached model, HTTP 204 |

For the `labeled-overlap` profile, training takes `windows` for genuine examples
and `impostor_windows` for operator-labeled examples from a consenting second user.
Omit validation-session fields: this profile deliberately fits all supplied labeled
data. Each class needs at least 20 windows across two disjoint sessions. Both
classes must use the authorized device and unique session/sequence pairs.

This profile fits one logistic regression (`C=0.1`, balanced class weights) on
`hold_std_ms / max(hold_mean_ms, 1e-8)` and `press_overlap_fraction`, with `log1p`
and a training-fitted standard scaler. The complete pipeline is persisted. Scores
are impostor-class decision margins, not calibrated identity probabilities.
Both scoring APIs return `score_delta = score - threshold`: positive is above the
alert threshold. This centers the display at zero; it is not a probability or a
cross-model magnitude comparison.
The threshold is the empirical 98th percentile of genuine training margins;
`threshold_data_role` explicitly records training reuse rather than independent
calibration. The 2% empirical training false-alarm limit is not a population guarantee.

The initial labeled pilot used 60 genuine and 30 second-user windows across six
sessions. Session-grouped model-selection cross-validation reached 96.7% accuracy,
93.3% second-user recall, and 1.67% genuine false alarms. These are selection CV
results, not an untouched independent final test. One enrolled person and one guest
do not establish performance across other people or devices. The saved model was
subsequently fitted on all 90 labeled windows on the designated remote host.

Training is synchronous in this first implementation. One training request runs
at a time; another receives 503. There is no queued/running job API. Training never
activates a model: the application backend activates one explicitly per user/device
with `PUT /models/{id}/active`, and deleting the active model leaves none active.
No monitoring request triggers retraining.

UNUSUAL results from labeled-overlap models include an `explanation`. Each feature's
`contribution` is its scaled value times the model weight, so the features are
ranked by how much they pushed the score toward the second-user class. Models trained
with this version also save a `genuine_baseline` (median and 1.4826 x MAD of genuine
training windows) and add each feature's `baseline_z`. The archived
`labeled-overlap-v2` artifact includes the baseline; v1 predates it and reports
contributions only. Isolation Forest models do not return explanations.
Explanations describe the model's decision, not the cause of a user's behavior.

The default minimum is 20 training windows and 10 validation windows from a
different collection session. Every enrollment window needs at least 30 presses;
duplicate session/sequence pairs and invalid/non-finite rows are rejected. These
are initial demo gates, not validated biometric sample requirements. The Python
training function accepts explicit minimum counts if the enrollment policy changes.

Features are selected in schema order. Near-constant columns are dropped using
training data only. Isolation Forest uses 100 trees, a fixed seed, and one compute
job; no scaler is needed for its axis-aligned splits. A 95% genuine calibration
order-statistic threshold is saved with ownership, session IDs, feature names, schema
hash, and dependency versions. This small validation set gives a coarse threshold;
separate real holdout data is still needed to measure false alarms.

The default `all` profile preserves the original feature set. The development-selected
`cadence-overlap` profile uses only `presses_per_second` and
`press_overlap_fraction`, applies `log1p`, and fits 200 trees with 16 samples per
tree. Preprocessing and the selected feature mask are persisted with each artifact;
older identity-transform artifacts remain readable. This profile was selected
using development examples and must be checked on new held-out sessions.

Multiple genuine calibration sessions retain their original IDs through
`validation_session_ids`. Their IDs are recorded in `validation_sessions`;
the legacy singular metadata field is null when more than one is used. The
threshold is the sorted score at rank `ceil(0.95 * n)` and alerts use strict `>`.
This permits at most 5% empirical calibration alerts (one out of 20), not a
guaranteed population false-alarm rate. A fresh test is required after tuning.

Scores are negative `score_samples` values, with larger values more anomalous.
They are not identity probabilities. Fewer than 30 presses returns
`INSUFFICIENT_DATA`. Missing models or unavailable services must never be shown as
normal activity by the caller.

Artifacts are written as complete versioned bundles, loaded only from the service's
own private directory, and rejected if schema/dependency versions differ. No model
upload endpoint is provided. Keep that directory private: joblib files are trusted
server-created objects, not safe inputs from users.

Run focused checks locally without fitting a model:

```sh
uv run --project backend python -m unittest discover -s tests -v
```

On the designated remote host, set `ML_ALLOW_TRAINING=1` for the same command to
include synthetic train/save/reload/score and ownership checks. Test artifacts are
temporary and deleted. Synthetic checks establish execution, not detection accuracy.

The initial deployment has been verified over a private SSH tunnel: authenticated
training/scoring, missing credentials, cross-account access, invalid payloads,
low activity, model deletion, and health after service restart. A user service
with lingering enabled supervises it. Host details and token files are local-only.
A host reboot has not been tested.

Account deletion, enrollment reset, model deactivation, and the monitoring
retention period are implemented on this service. `INQUISITOR_MONITORING_RETENTION_SECONDS`
sets the initial period; `PUT /retention` changes it without editing code.
`POST /retention/purge` removes monitoring rows older than that period.

Still required: real session data, a finalized collector aggregation contract,
application ingestion and auth integration,
and the hosted application's HTTPS or SSH-tunnel connection to the model service.
The workstation's development tunnel is not that deployed application connection.
Never upload the collector's raw per-event log directly to this service.

For session-separated median/MAD versus Isolation Forest comparison, see
[model evaluation](../docs/model-evaluation.md). The evaluation command runs only
on the designated training host, uses temporary artifacts, and does not activate
a production model. Real-data metrics remain unavailable until collected sessions
are evaluated.
