# Dashboard integration handoff

The user dashboard supports opt-in local feature-window collection and JSONL
export. When hosted sign-in is configured, it polls monitoring status, anomaly
score history, and alerts for the signed-in account. It does not open a
WebSocket. A stale agent check-in is labeled offline, and the last score is
not presented as a live signal.

## Code locations

- `server.py`: `/dashboard` route. Add authenticated page data/API handling here
  only if this Flask prototype remains the selected host.
- `templates/index.html`: landing pitch, Create Account, Log In, the timing-summary diagram, the Windows agent requirement, and the limitations section.
- `templates/privacy.html`: accepted feature-window fields and units, the checked-in version 2 sample payload, and the prohibited collection list. `GET /privacy` renders it.
- `templates/dashboard.html`: local collection controls, connection status,
  anomaly-signal summary, Better Auth profile fields, and data/account controls.
- `frontend/dashboard.ts`: browser-only timing aggregation, opt-in collection,
  and API calls. It keeps key identifiers transient and sends only feature
  windows that match the v1 contract.
- `server.py`: `/api/feature-windows` validates and stores windows,
  `/api/feature-windows/export` returns JSONL, and the danger-zone actions
  require an exact confirmation phrase. `DELETE /me/data` with `DELETE` removes
  arcade samples, feature windows, scores, alerts, and local model files for
  the local user. `POST /api/enrollment/reset` with `RESET` removes enrollment
  windows and the active local model. `POST /api/model/deactivate` with
  `DEACTIVATE` marks that model inactive and keeps its files. `PUT /api/retention`
  sets the monitoring retention period, and `POST /api/retention/purge` deletes
  monitoring rows older than that period.
- `shared/feature-window-v1.schema.json`: shared schema used to validate saved
  windows.
- `static/dashboard.js`: generated from `frontend/dashboard.ts` by `npm run build`.
- `static/style.css`: styles for dashboard panels and responsive layout.
- `docs/architecture.md`: selected product architecture and trust boundaries.

## WebSocket and service work still required

The selected architecture uses an authenticated outbound `wss://` connection
from the Windows agent to a hosted relay, with the signed-in dashboard connected
to that relay. Do not make the browser connect directly to a private PC or
expose the local detector.

The agent half exists in hosted mode: the Rust tray holds a socket at
`GET /api/agent/ws` (`hosted.py`, `AgentLink`) for presence, pause state,
collection status, and revocation. Windows still upload over HTTPS. The
dashboard half does not exist yet; the dashboard keeps polling and reads the
presence the socket records. A push channel to the browser, if wanted, is
follow-up work.

The follow-up implementer should:

1. Define and version messages for agent/model health, timestamps, disconnects,
   and anomaly-signal summaries before wiring status updates into the dashboard.
2. Authenticate the dashboard with Better Auth and authorize every status/event
   against the same account and paired device. Keep device credentials on the
   agent/service, not in browser JavaScript.
3. Keep raw key identity, typed content, and ordered input events out of
   WebSocket messages, logs, and persistence. Send only the approved aggregate
   features and result summaries.
4. Mark stale connections offline and avoid showing old anomaly signals as
   current. A signal is a verification prompt, not proof of identity.
5. Forward the website `DELETE /me/data` action to the model service with the
   server-side credential. The website already deletes its own rows and local
   artifact files after a typed phrase. The model service deletes that account's
   stored windows, scores, alerts, and model artifacts at `DELETE /me/data`.
   Those two stores are not yet linked, and neither one deletes a Better Auth account.
6. Add tests for authorization, account/device isolation, message validation,
   disconnect/revocation, stale status, and deletion before presenting the
   integration as complete.

Account deletion remains disabled in the UI until Better Auth and account-scoped
storage are implemented. The profile fields intentionally contain no fabricated
user information.
