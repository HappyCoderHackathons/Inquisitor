# Dashboard integration handoff

The user dashboard supports opt-in local feature-window collection and JSONL
export. It does not currently authenticate users, connect to a model, detect
non-user activity, or open a WebSocket. Connection labels remain disconnected
so the page does not present placeholder data as live.

## Code locations

- `server.py`: `/dashboard` route. Add authenticated page data/API handling here
  only if this Flask prototype remains the selected host.
- `templates/dashboard.html`: local collection controls, connection status,
  detection summary, Better Auth profile fields, and data/account controls.
- `frontend/dashboard.ts`: browser-only timing aggregation, opt-in collection,
  and API calls. It keeps key identifiers transient and sends only feature
  windows that match the v1 contract.
- `server.py`: `/api/feature-windows` validates and stores windows,
  `/api/feature-windows/export` returns JSONL, and `/api/local-data` clears the
  local prototype data.
- `shared/feature-window-v1.schema.json`: shared schema used to validate saved
  windows.
- `static/dashboard.js`: generated from `frontend/dashboard.ts` by `npm run build`.
- `static/style.css`: styles for dashboard panels and responsive layout.
- `docs/architecture.md`: selected product architecture and trust boundaries.

## WebSocket and service work still required

The selected architecture uses an authenticated outbound `wss://` connection
from the Windows agent to a hosted relay, with the signed-in dashboard connected
to that relay. This local Flask prototype is not the hosted relay; do not make
the browser connect directly to a private PC or expose the local detector.

The follow-up implementer should:

1. Define and version messages for agent/model health, timestamps, disconnects,
   and detection summaries before wiring status updates into the dashboard.
2. Authenticate the dashboard with Better Auth and authorize every status/event
   against the same account and paired device. Keep device credentials on the
   agent/service, not in browser JavaScript.
3. Keep raw key identity, typed content, and ordered input events out of
   WebSocket messages, logs, and persistence. Send only the approved aggregate
   features and result summaries.
4. Mark stale connections offline and avoid showing old detection results as
   current. A detection is a verification prompt, not proof of identity.
5. Implement account-scoped deletion on the hosted service, with authorization,
   confirmation, and an explicit result.    The local delete button clears arcade samples and collected windows from this
   prototype database; it does not delete an authenticated user's account.
6. Add tests for authorization, account/device isolation, message validation,
   disconnect/revocation, stale status, and deletion before presenting the
   integration as complete.

Account deletion remains disabled in the UI until Better Auth and account-scoped
storage are implemented. The profile fields intentionally contain no fabricated
user information.
