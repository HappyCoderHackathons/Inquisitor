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
- Windows client: Rust owns native keyboard I/O, local orchestration, and transport.
- ML: Python 3.13+ with PyTorch. Training and inference run locally through the Rust
  client's aggregate JSON process boundary. Keep model artifacts on the PC.
- Web: SvelteKit, TypeScript, Vite, Tailwind CSS, Iconify, and Prettier at 120 columns.
  SvelteKit hosts account, pairing, persistence, and status endpoints, with a
  persistent WebSocket relay for the desktop connection.
- Storage: MongoDB Atlas. Use server-side credentials; do not introduce PostgreSQL
  or Drizzle based on general stack preferences.
- Authentication: SSOJet. Enforce account/device authorization in the application;
  use short-lived, single-use pairing codes and revocable device credentials.
- Hosting: HTTPS for the hosted service and authenticated outbound WSS from the
  Windows client. Do not require inbound Internet access to the user's PC.
- The Flask/TypeScript/SQLite arcade in `test/` is a standalone prototype. Preserve
  its existing stack unless the task explicitly requests a migration.

## Input and ML boundaries

- Authentication v1 is keyboard-only. Arcade mouse/controller gameplay does not
  expand that scope.
- Require explicit opt-in and visible pause/exit controls. Do not bypass secure
  desktop protections, lock Windows, or block keyboard input.
- Key identity may exist transiently in memory only to pair press/release events.
  Never persist, log, or transmit key identities, typed content, or ordered events.
  Only aggregate numeric features may enter model requests or telemetry.
- Preserve session boundaries for ML evaluation. Label synthetic fixtures and replay
  data clearly; do not claim they establish model accuracy or identity certainty.

## Code, writing, and handoff

- Use real TypeScript types, never `any`. Prefer Python type hints and avoid
  unnecessary abstractions or comments.
- Use Vitest for requested web tests and pytest for requested Python tests, unless
  an existing component already uses a different runner.
- Use conventional commits. Never add a `Co-Authored-By: Claude` trailer or a
  `Generated with Claude Code` line to commits or PRs.
- Write concise, professional feedback. No emojis, em dashes, invented facts,
  dates, links, or unverified success claims.
- At handoff, briefly state what changed, material assumptions, verification, and
  known risks. Distinguish architecture decisions from working integrations.
