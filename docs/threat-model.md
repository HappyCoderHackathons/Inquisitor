# Threat model

This section states what Inquisitor is designed to resist, what it does not claim, and
where residual risk remains. It complements [architecture decisions](architecture.md) and
[privacy](privacy.md).

## Assets

- Account credentials and device pairing tokens
- Aggregate keyboard and optional mouse timing features per user
- Per-user trained model artifacts and inference results
- Hosted relay and dashboard session integrity

## Adversaries and scenarios

| Threat | Description | Mitigation |
| --- | --- | --- |
| Forgery | Another person replays or synthesizes windows without matching behavior | Device-scoped auth, schema validation, per-user models; no identity guarantee |
| Replay | Attacker resubmits stored aggregate windows | Per window device/session/sequence uniqueness; no raw event capture |
| Local tampering | Malware alters the agent or browser before upload | Visible agent control, HTTPS/WSS, server-side validation; client compromise is accepted risk |
| Similar users | Consenting second person with overlapping timing | Pilot uses consenting second-user data only; other users are out of scope |
| Data leakage | Sensitive content leaves the device | No key identities, coordinates, or ordered events in storage or ML payloads |

## Residual and accepted risks

- A compromised Windows session can send crafted aggregates until revocation.
- Shared PCs without lock-step user separation can blur session boundaries.
- Training metrics on consenting second-user sessions do not establish population accuracy.
- Hosted and ML components have not completed full production hardening or pen testing.

## Operator controls

Users can pause the agent. The hosted setup page can disconnect the current agent, which
revokes its credential, and delete the account's behavioral data and models, which also
revokes every agent. The local arcade supports data deletion and enrollment reset.
Monitoring retention is configurable on the ML service. The retention period is one row
for that process, and a purge removes old monitoring rows for every account on that store.

## Response headers

The website and the model service set headers on every response, including errors.

| Header | Website | Model service |
| --- | --- | --- |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'` | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'` |
| `X-Content-Type-Options` | `nosniff` | `nosniff` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` when the request is HTTPS | same, only when the request is HTTPS |

Plain HTTP omits HSTS so local `http://127.0.0.1` is not pinned to HTTPS. Pages load
same-origin stylesheets and scripts, and the website policy does not allow
`unsafe-inline`. The Better Auth process listens on `127.0.0.1:3001`. Browsers reach it
through Flask, which adds these headers to the proxied response. `includeSubDomains`
applies to the hostname that served the HTTPS response.

## Same-site state changes

Cookie-authenticated hosted posts require a JSON object. They reject an `Origin` other
than `BETTER_AUTH_URL`, and they reject `Sec-Fetch-Site: cross-site`. A missing `Origin`
stays allowed so the agent and other non-browser clients can call the API. Session
cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` when the public origin is HTTPS.
Sessions expire after 12 hours, are not cached in the cookie, and are revoked on sign-out.

The same origin rule covers every local `POST`, `PUT`, `PATCH`, and `DELETE`. The allowed
origins are the request host and, when set, `BETTER_AUTH_URL`. Agent pairing and window
upload use a pairing code or a bearer token. They still pass through that JSON and origin
check. The model service has no browser session and does not use a CSRF token. Callers
authenticate with the server-side bearer token.

These checks stop a browser page on another origin from using the victim's session cookie.
They do not stop a caller that already holds the cookie, the device token, or the model
token, or a client that can set both `Host` and `Origin`.

## Endpoint owner checklist

JSON bodies are not an account selector. The model service rejects a JSON key outside
that endpoint's contract, so a body `user_id` is HTTP 422.

### Local website

There is no login. The dev server binds `127.0.0.1`. Fixed ids `local-user` and
`local-device` mark the single local account. They are not taken from the body.

| Method and path | Who it acts for |
| --- | --- |
| `GET /`, `/arcade`, `/dashboard`, `/privacy` | Public page |
| `GET /onboarding` when hosted auth is off | Public notice that sign-in is not configured |
| `GET /api/summary`, `/api/feature-windows`, `/api/feature-windows/export`, `/api/retention` | The local process. Anyone who can open the port can read those aggregates |
| `POST /api/feature-windows` | The local process. `device_id` is a field of the feature window |
| `POST /api/samples` | The local arcade sample table. No user id |
| `DELETE /api/samples` | Clears that local sample table |
| `DELETE /api/local-data` and `DELETE /me/data` | Requires the exact confirmation `DELETE`. Erases `local-user` |
| `POST /api/enrollment/reset` | Requires `RESET`. Resets `local-user` on `local-device` |
| `POST /api/model/deactivate` | Requires `DEACTIVATE`. Deactivates that same local pair |
| `PUT /api/retention` and `POST /api/retention/purge` | One retention row for the local process |

### Hosted cookie session

The principal is the Better Auth session. Flask stores it as `better-auth:` plus the
auth user id. An empty id is rejected.

| Method and path | Who it acts for |
| --- | --- |
| `POST /api/auth/sign-in/email`, `/api/auth/sign-up/email`, `/api/auth/sign-out` | The email and password, or the session being revoked. Unknown auth paths are 404 |
| `GET /api/auth/get-session` | Proxied session read |
| `GET /api/me`, `GET /api/onboarding` | The signed-in account |
| `GET /onboarding`, `GET /login` | Page, or redirect to onboarding |
| `POST /api/pairing-codes` | The signed-in account. The code is stored as a hash |
| `POST /api/onboarding/rounds` and `POST /api/onboarding/rounds/current/<outcome>` | The signed-in account's round. The body selects a game or an outcome |
| `POST /api/onboarding/train` | The signed-in account's onboarding |
| `POST /api/devices/<device_id>/revoke` | The signed-in account, and only a device row whose `user_id` matches that account |
| `DELETE /api/me/data` | The signed-in account. Device ids come from that account's device records |

### Agent bearer

| Method and path | Who it acts for |
| --- | --- |
| `POST /api/agent/pair` | The account that owns the unused, unexpired pairing code. The token is stored only as a hash |
| `GET /api/agent/onboarding` | The device for that bearer token. A revoked device is rejected |
| `POST /api/agent/windows` | That device. The stored `user_id` comes from the device record. `window.device_id` must equal the device id |

### Model service bearer

`GET /health` requires the bearer token only. Every other route also requires
`X-User-ID` and `X-Device-ID`, each 1 to 256 characters. The application backend sets
those headers from the signed-in user or the paired device. The browser and the collector
do not hold the service token.

| Method and path | Who it acts for |
| --- | --- |
| `GET /model`, `POST /score` | The configured published owner and device. Other header pairs are 403 |
| `POST /models/train`, `GET /models/active`, `POST /models/active/score`, `POST /models/active/deactivate`, `POST /enrollment/reset` | The header user and device |
| `GET /models/<id>`, `POST /models/<id>/score`, `PUT /models/<id>/active`, `DELETE /models/<id>` | The header pair must match the stored model owner |
| `POST /windows` | The header user. The window's `device_id` must match the header device. Monitoring also requires that pair's active model |
| `DELETE /me/data` | Artifacts and rows for the header user id |
| `GET /retention`, `PUT /retention`, `POST /retention/purge` | Any holder of the service token. The policy is process-wide |

## Dependency updates

[`.github/dependabot.yml`](../.github/dependabot.yml) enrolls weekly updates for
GitHub Actions, npm and pip in `Frontend/`, uv in `backend/`, and cargo in
`backend/rust/`. A version bump still goes through the existing CI checks.

## Forgery, replay, and leakage in the request path

Forgery of a browser state change is limited by the signed session cookie, `SameSite=Lax`,
the origin check, and JSON-only bodies. Auth posts are limited to 30 per minute per IP.
Pairing codes are single-use, hashed, and expire after 10 minutes. The model token is
compared with `compare_digest`. Account routes take the user from the session or the
device record.

Agent window uploads require a strict shared feature-window schema (v1-v4), are capped
at 64 KiB, and are limited to 60 requests per minute per device. Replay of a captured
window is limited by device, session, and sequence uniqueness (a duplicate is HTTP 409),
session expiry, and sign-out revocation. Audit events record only metadata such as the
authenticated device, sequence, schema version, mode, and outcome; they omit
feature values and request bodies. Pairing attempts are limited to 10 per 10 minutes per
IP. A stolen device token works until that device is revoked. Score requests have no
separate nonce. The service token stays in server configuration.

Local files are ordinary SQLite databases, model artifacts, and token files. The operator
of the host can edit them. Header and origin checks do not make those files tamper-evident.
Hosted account data stays in MongoDB Atlas. The model token is not in browser JavaScript.

Similar timing remains an accepted limit of the one-person, one-guest pilot. The score is
an anomaly signal. See [model evaluation](model-evaluation.md). Response headers do not
change that limit.

Data leaving the machine is timing aggregates. Key identity, typed content, pointer
coordinates, and ordered input events are outside the stored contract. Auth responses and
the feature-window export use `Cache-Control: no-store`. The local summary and export
routes have no login. Keeping the dev server on loopback keeps those aggregates on the
machine.