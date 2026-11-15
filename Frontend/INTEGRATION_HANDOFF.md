# Dashboard integration handoff

The user dashboard is a UI scaffold. It does not currently authenticate users,
connect to a model, detect non-user activity, or open a WebSocket. The labels
remain in a disconnected state so the page does not present placeholder data as
live.

## Code locations

- `server.py`: `/dashboard` route. Add authenticated page data/API handling here
  only if this Flask prototype remains the selected host.
- `templates/dashboard.html`: connection status, detection summary, SSOJet
  profile fields, and data/account controls. The `agentConnectionStatus`,
  `modelConnectionStatus`, `detectionConnectionStatus`, `accountName`,
  `accountEmail`, and `accountId` elements are hydration targets for the future
  integration.
- `static/dashboard.js`: currently only handles deletion of local arcade
  samples through the existing `/api/samples` endpoint. Add authenticated
  dashboard behavior here only after its API contract is defined.
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
2. Authenticate the dashboard with SSOJet and authorize every status/event
   against the same account and paired device. Keep device credentials on the
   agent/service, not in browser JavaScript.
3. Keep raw key identity, typed content, and ordered input events out of
   WebSocket messages, logs, and persistence. Send only the approved aggregate
   features and result summaries.
4. Mark stale connections offline and avoid showing old detection results as
   current. A detection is a verification prompt, not proof of identity.
5. Implement account-scoped deletion on the hosted service, with authorization,
   confirmation, and an explicit result. The current local delete button clears
   all arcade samples from this prototype database and does not delete an
   authenticated user's account.
6. Add tests for authorization, account/device isolation, message validation,
   disconnect/revocation, stale status, and deletion before presenting the
   integration as complete.

Account deletion remains disabled in the UI until SSOJet and account-scoped
storage are implemented. The profile fields intentionally contain no fabricated
user information.
