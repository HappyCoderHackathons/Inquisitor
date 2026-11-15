# Project rules

## Workflow

- Do not use Superpowers skills or workflows in this repository. Proceed directly
  with the requested work without Superpowers planning or approval gates.
- Do not generate Superpowers artifacts. Keep any existing artifacts untracked
  using the repository's `.gitignore`.
- State material assumptions before implementation. Ask when the requested behavior
  is unclear, after checking the repository and linked issue for an answer.
- Make the smallest complete change. Read affected files, callers, shared utilities,
  and existing configuration first. Match local conventions and avoid unrelated fixes.
- Inspect package manager, formatter, linter, and test configuration before choosing
  commands. Run relevant checks and report anything that could not be verified.

## Architecture and stack

- Follow [docs/architecture.md](docs/architecture.md) for the selected stack and
  component boundaries. It supersedes open options in the original project spec.
- Windows client: Rust owns native keyboard and mouse I/O, local orchestration, and transport.
- ML: Python 3.13+ with scikit-learn. The current labeled pilot uses logistic
  regression on hold-time variability and press overlap; Isolation Forest artifacts
  remain supported for comparison and rollback. Training and inference run
  on the designated remote host behind an authenticated Flask service. Keep model
  artifacts there; keep deployment identities and credentials out of the repository.
  Training is disabled by default and must only be enabled on that host.
- Web: Flask with Jinja templates, and browser TypeScript bundled by esbuild, building
  on the existing app in `Frontend/`. Format TypeScript with Prettier at 120 columns.
  Flask hosts account, pairing, persistence, and status endpoints, with a
  persistent WebSocket relay for the desktop connection. Do not introduce SvelteKit
  based on general stack preferences.
- Storage: MongoDB Atlas. Use server-side credentials; do not introduce PostgreSQL
  or Drizzle based on general stack preferences.
- Authentication: Better Auth in the local Node service, with MongoDB-backed password accounts and sessions.
  Flask forwards same-origin authentication requests and validates sessions through that service.
  Enforce account/device authorization in the application;
  use short-lived, single-use pairing codes and revocable device credentials.
- Hosting: HTTPS for the hosted service and authenticated outbound WSS from the
  Windows client. Do not require inbound Internet access to the user's PC.
- The arcade games in `Frontend/` remain a prototype collection surface. Their local
  SQLite storage is not the hosted Atlas store.

## Input and ML boundaries

- Authentication v1 uses keyboard and mouse, collected by the arcade for calibration
  and by the Windows agent for monitoring with matching feature definitions. Mouse
  collection needs its own opt-in toggle. Controller input does not expand that scope.
- Require explicit opt-in and visible pause/exit controls. Do not bypass secure
  desktop protections, lock Windows, or block keyboard or mouse input.
- Key identity may exist transiently in memory only to pair press/release events.
  Never persist, log, or transmit key identities, typed content, absolute pointer
  coordinates, click targets, window titles, or ordered events.
  Only aggregate numeric features may enter model requests or telemetry.
- Preserve session boundaries for ML evaluation. Label synthetic fixtures and replay
  data clearly; do not claim they establish model accuracy or identity certainty.

## Code, writing, and handoff

- Use real TypeScript types, never `any`. Prefer Python type hints and avoid
  unnecessary abstractions or comments.
- Use pytest for requested Python tests, unless an existing component already uses
  a different runner (the backend, collector, and `Frontend/` tests use unittest).
- Use conventional commits. Never add a `Co-Authored-By: Claude` trailer or a
  `Generated with Claude Code` line to commits or PRs.
- Write concise, professional feedback. No emojis, em dashes, invented facts,
  dates, links, or unverified success claims.
- At handoff, briefly state what changed, material assumptions, verification, and
  known risks. Distinguish architecture decisions from working integrations.

## Testing

- Keep tests simple and concise. Cover normal behavior and specific, meaningful
  edge cases that could cause real failures.
- Do not add tests just to increase test count or coverage. Reuse existing checks
  and add only what is needed to verify the change.
- Skip redundant tests and tests that merely mirror the implementation. No new
  tests are needed for documentation-only or trivial changes.
