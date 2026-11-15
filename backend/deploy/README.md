# Published model API deployment

The deployed numeric artifact is `backend/artifacts/labeled-overlap-v2.json`; v1 is kept for rollback.
It contains learned coefficients, scaler mean/scale, the fixed threshold, and the
genuine baseline median/scale used for alert explanations.
It contains no enrollment rows, account/device/session IDs, credentials, or hostnames.
Publishing the trained numeric weights is intentional. This is a per-user pilot,
not a universal model. See [evaluation](../../docs/model-evaluation.md) for limitations.

On the model host, place this repository at `~/inquisitor-model/app`, install
Python 3.13+ and uv, and create the locked environment from that directory:

```sh
UV_PROJECT_ENVIRONMENT="$HOME/inquisitor-model/.venv" uv sync --project backend --locked
```

Create `~/inquisitor-model/service.env`, readable only by the deployment user.
Set these values to real server-local paths and authorized identity values:

```text
ML_TOKEN_FILE=/absolute/private/path/to/service.token
ML_DATA_DIR=/absolute/private/path/to/model-artifacts
ML_PUBLISHED_MODEL_FILE=/absolute/path/to/app/backend/artifacts/labeled-overlap-v2.json
ML_PUBLISHED_USER_ID=the-enrolled-account-id
ML_PUBLISHED_DEVICE_ID=the-paired-device-id
ML_ALLOW_TRAINING=0
```

The token file must contain a dedicated random URL-safe token of at least 32
characters. Keep this file, the environment file, and deployment identity out of
Git. Distribute the token only to the application backend or authorized operators.
Do not place it in browser code, URLs, or agent binaries.

Install the supplied user service:

```sh
mkdir -p "$HOME/.config/systemd/user"
cp backend/deploy/inquisitor-ml.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now inquisitor-ml.service
```

The service binds only to remote `127.0.0.1:8765`. Use an approved HTTPS reverse
proxy or a verified SSH tunnel from the application backend. For an operator
tunnel, substitute the SSH destination from private configuration:

```sh
ssh -N -L 127.0.0.1:18765:127.0.0.1:8765 "$ML_SSH_DESTINATION"
```

Configure the API client using environment values, not source-code credentials:

```sh
export ML_API_URL=http://127.0.0.1:18765
curl --fail-with-body "$ML_API_URL/model" \
  -H "Authorization: Bearer $(cat "$ML_TOKEN_FILE")" \
  -H "X-User-ID: $ML_PUBLISHED_USER_ID" -H "X-Device-ID: $ML_PUBLISHED_DEVICE_ID"
curl --fail-with-body "$ML_API_URL/score" -X POST \
  -H "Authorization: Bearer $(cat "$ML_TOKEN_FILE")" \
  -H "X-User-ID: $ML_PUBLISHED_USER_ID" -H "X-Device-ID: $ML_PUBLISHED_DEVICE_ID" \
  -H 'Content-Type: application/json' --data-binary @request.json
```

`request.json` must contain `{"window": <authorized-device aggregate window>}`.
The application backend must derive identity headers from its authenticated user
or paired device. The model API checks its configured owner/device; these headers
alone are not end-user authentication.

Responses include `model_version`, the canonical artifact SHA-256, `state`, `score`,
`threshold`, and `score_delta`. Positive `score_delta` is above the alert threshold;
zero or negative is normal. These margins are not identity probabilities and their
magnitudes are not comparable across different model families. Low activity returns
`INSUFFICIENT_DATA` with null score values. Missing credentials return 401, wrong
ownership returns 403, and invalid windows return 422. Inference performs no fitting.

Replace the versioned artifact path and restart the service to deploy another
published version. The artifact is validated and loaded once at startup. Keep
previous artifacts for rollback. Confirm user-service persistence under the host's
policy; lingering may be required. Never disable TLS or SSH host-key validation.

To export a future trusted service-created labeled artifact on the model host:

```sh
uv run --project backend python -m backend.ml.export_weights \
  --model-dir "$ML_DATA_DIR/MODEL_ID" --version labeled-overlap-v3 \
  --output backend/artifacts/labeled-overlap-v3.json
```

The exporter reads a trusted local joblib artifact, never a user-uploaded pickle.
Published inference reads only numeric JSON. Schema and artifact hashes use sorted,
compact JSON so Git line-ending conversions do not change their identity.
