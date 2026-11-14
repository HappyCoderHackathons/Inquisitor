# Architecture decisions

This document resolves [issue #1](https://github.com/hack-the-nest-but-happier/Inquisitor/issues/1).
It supersedes the open stack options in sections 11 and 19 of the
[original project specification](../InputPrint_Continuous_Behavioral_Authentication_Project_Spec.md).
These are implementation decisions, not claims that the integrations already work.

## Selected stack

| Area | Decision | Reason and source |
| --- | --- | --- |
| Windows agent | Rust, with native Windows keyboard input and a visible pause/exit control | The local client and system I/O are assigned to Rust in [#43](https://github.com/hack-the-nest-but-happier/Inquisitor/issues/43#issuecomment-6882866683) and [#46](https://github.com/hack-the-nest-but-happier/Inquisitor/issues/46#issuecomment-6882888019). |
| ML | Python 3.13+ with PyTorch | [#42](https://github.com/hack-the-nest-but-happier/Inquisitor/issues/42#issuecomment-6882822449) selects Python and suggests PyTorch. Use Python for the model, alongside the Rust client specified in the later decisions. |
| Website | SvelteKit, TypeScript, Vite, Tailwind CSS, Iconify, and Prettier at 120 columns | Follow the project's preferred web stack. |
| Backend arrangement | Local Rust detector API calling the Python model, plus a hosted web service for accounts, pairing, persistence, and status relay | Preserve the local detector and web status connection selected in #43. SvelteKit handles hosted HTTP endpoints; the deployment also needs a persistent WebSocket relay. |
| Database | MongoDB Atlas | [#44](https://github.com/hack-the-nest-but-happier/Inquisitor/issues/44#issuecomment-6882901927) selects Atlas. Use MongoDB collections rather than adding PostgreSQL or Drizzle. |
| Authentication | Managed SSOJet authentication | [#45](https://github.com/hack-the-nest-but-happier/Inquisitor/issues/45#issuecomment-6882879091) selects SSOJet. Application authorization and device pairing remain application responsibilities. |
| Demo hosting | Hosted HTTPS website/service; Rust client and Python model on the user's Windows PC | Follow #46. The server host must support long-lived WSS connections. |
| Input scope | Keyboard-only v1 | Matches the spec's essential input signal and keeps the background collector and model contract focused. Mouse and controller signals are outside the v1 authentication model. |

The language split reconciles the Python/PyTorch comment in #42 with the explicit
Rust client decisions in #43 and #46. Python is the ML runtime; Rust owns system
I/O, local orchestration, and transport. Selecting PyTorch does not require a
neural network or remove the spec's simple statistical baseline comparison.

## Component boundaries and data flow

1. The consenting user enables the visible Windows agent. Rust captures transient
   keyboard timing, pairs press/release events in memory, and produces numeric
   feature windows. Key identity and ordered input events never enter persistence,
   logs, model requests, or network messages.
2. Rust invokes a local Python model process with aggregate JSON requests over
   standard input/output. Training and inference run locally, with model artifacts
   associated with the paired account and device. Define versioned messages and
   explicit error responses when implementing this boundary.
3. The user signs into the hosted SvelteKit app through SSOJet and pairs the agent
   using a short-lived, single-use code. The agent receives a revocable,
   device-scoped credential; it never receives the web password.
4. The agent opens an authenticated outbound WSS connection to the hosted relay.
   The dashboard connects to the hosted service using its authenticated browser
   session. The relay routes device status, scores, and authorized control messages
   between the two connections.
5. The hosted service stores account identity mappings, devices, enrollment/model
   metadata, and authorized monitoring results in Atlas. Model artifacts remain on
   the PC. Cloud storage of aggregate training windows is deferred unless an
   explicit product need and retention policy are established.

The outbound connection is the implementation choice for #43's server-to-local
status channel. The hosted server sends messages through the agent's established
connection; it does not initiate a connection to a private PC address. Do not
require public inbound ports or expose the detector directly to the Internet.
The server must bind both connections to the same authorized account/device and
reject messages after revocation. If the agent disconnects, show offline status
rather than displaying stale scores as live results.

## Alternatives and integration cost

- All-in-one FastAPI would put HTTP, training, and inference in one Python
  process, reducing the Rust/Python boundary. It would change #43's chosen local
  Rust API and move model execution into the hosted backend if used centrally.
- SvelteKit API plus a hosted Python ML worker would retain the preferred web
  stack but add a deployed worker, service authentication, and model/data transfer.
  The selected local Rust/Python split instead requires process lifecycle and
  message handling on Windows, plus a hosted status relay.
- A Python-only agent would reduce language boundaries, while C#/.NET would offer
  another native Windows implementation. Neither matches the Rust client decision.
  No comparative capture benchmark is available in this repository.
- SQLite is suitable for the existing local arcade prototype but does not match
  the selected hosted database. PostgreSQL would follow the usual project
  preference, but the explicit Atlas decision takes precedence. Atlas credentials
  belong only on the hosted service, never in the browser or desktop client.
- Framework-native accounts would require local password storage and password
  lifecycle implementation. SSOJet owns authentication instead. Use its supported
  OIDC flow and verify the configured integration; do not invent password hashing.
- A laptop-only demo would reduce hosted infrastructure but diverge from #46.
  An aggregate-only replay may be prepared as a clearly labeled fallback.

## Scope and existing prototype

The `test/` arcade remains a standalone Flask/TypeScript/SQLite collection
prototype. Its mouse and controller gameplay does not expand the Windows
authentication MVP's scope. This decision does not migrate or alter that prototype.

For v1, collect keyboard timing only, require explicit opt-in, retain pause/exit
controls, and never bypass the Windows secure desktop. An anomaly may request
application-level verification; it must not lock Windows. Enrollment and evaluation
must preserve session boundaries, and synthetic fixtures must not be presented as
evidence of model accuracy.

## Implementation verification still required

- Prove Rust background input capture and timing quality on the target Windows PC.
- Verify Python 3.13+ and the chosen PyTorch build together on that PC.
- Configure SSOJet, validate its login/callback/logout flow, and enforce account
  isolation. [SSOJet's OIDC documentation](https://ssojet.com/docs/en/enterprise-sso/oidc-configuration)
  describes issuer configuration and redirect URIs; no tenant setup has been tested here.
- Provision Atlas and test the hosted service connection using TLS and restricted
  database credentials. [Atlas connection documentation](https://www.mongodb.com/docs/atlas/driver-connection/)
  requires TLS-capable clients. No promotional credit or existing cluster is assumed.
- Deploy the chosen host, verify its HTTPS certificate and WSS upgrade, then test
  an authenticated agent-to-relay-to-dashboard round trip, disconnect, and revocation.
  No deployment endpoint or successful HTTPS test is recorded in the decision issues.

These checks belong to implementation and deployment work. This document completes
the stack and scope decisions without claiming those checks have passed.
