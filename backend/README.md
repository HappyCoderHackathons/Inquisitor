# Remote ML service

Python 3.13+, Flask, and one Isolation Forest model per user/device. The backend
uses the provisional contract in `shared/feature-window-v1.schema.json`.
Training and inference belong on the designated remote model host. Hostnames,
accounts, and credentials must stay in local/server configuration.

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
| `GET /health` | No body | Authenticated readiness |
| `POST /models/train` | `windows` array and `validation_session_id` | Ready model metadata, HTTP 201 |
| `GET /models/{id}` | No body | Owned model metadata |
| `POST /models/{id}/score` | `window` object | Score, threshold, model ID, and state |
| `DELETE /models/{id}` | No body | Removes owned artifacts and cached model, HTTP 204 |

Training is synchronous in this first implementation. One training request runs
at a time; another receives 503. There is no queued/running job API or automatic
active-model selection yet. The application backend must select the correct model
ID. No monitoring request triggers retraining.

The default minimum is 20 training windows and 10 validation windows from a
different collection session. Every enrollment window needs at least 30 presses;
duplicate session/sequence pairs and invalid/non-finite rows are rejected. These
are initial demo gates, not validated biometric sample requirements. The Python
training function accepts explicit minimum counts if the enrollment policy changes.

Features are selected in schema order. Near-constant columns are dropped using
training data only. Isolation Forest uses 100 trees, a fixed seed, and one compute
job; no scaler is needed for its axis-aligned splits. A 95th-percentile genuine
validation threshold is saved with ownership, session IDs, feature names, schema
hash, and dependency versions. This small validation set gives a coarse threshold;
separate real holdout data is still needed to measure false alarms.

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

Still required: real session data, a finalized collector aggregation contract,
application ingestion and auth integration, enrollment retention/deletion policy,
and the hosted application's HTTPS or SSH-tunnel connection to the model service.
The workstation's development tunnel is not that deployed application connection.
Never upload the collector's raw per-event log directly to this service.

For session-separated median/MAD versus Isolation Forest comparison, see
[model evaluation](../docs/model-evaluation.md). The evaluation command runs only
on the designated training host, uses temporary artifacts, and does not activate
a production model. Real-data metrics remain unavailable until collected sessions
are evaluated.
