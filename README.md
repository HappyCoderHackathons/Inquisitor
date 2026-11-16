# Inquisitor

Continuous behavioral authentication prototype: aggregate keyboard and optional mouse
timing, per-user models, and dashboard alerts.

> [!NOTE]
> The hosted deployment has been shut down. The Fly.io app was deleted, and the remote ML host's
> model service and reverse tunnel were removed. Redeploying needs a new Fly app (`fly.toml`) and a
> new ML host (see `backend/deploy/README.md`).
>
> - The model it served is committed as `backend/artifacts/tray-two-device-v1.json`; retired
>   weights are in `archive_models/`.
> - `backend/ml/evaluate_tray.py` was recovered from that host. It scores new genuine and impostor
>   recordings against a frozen model without refitting or moving the threshold.
> - Per-account models, enrollment windows, and service credentials were not committed. They were
>   copied to a private local backup only.

## Prerequisites

- Windows 10 or later for the Rust agent (see `backend/rust/README.md`)
- Python 3.13+ with [uv](https://docs.astral.sh/uv/) for backend and ML tests
- Node.js 22.18+ for Better Auth and Frontend bundles
- MongoDB Atlas URI for hosted onboarding (optional for local arcade-only work)

## Run components

| Component | Directory | Command |
| --- | --- | --- |
| Arcade / local Flask app | `Frontend/` | `python3 -m pip install -r requirements.txt`, `npm install`, `npm run build`, then `python3 server.py` |
| Better Auth (hosted) | `Frontend/` | `npm ci`, then `npm run auth` with `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, `MONGODB_URI`, and `MONGODB_DB` set |
| ML service | repo root | `ML_TOKEN_FILE=... ML_DATA_DIR=... MONGODB_URI=... MONGODB_DB=... ML_ALLOW_TRAINING=0 uv run --project backend python -m flask --app backend.ml.app:create_app run` |
| Backend tests | repo root | `uv sync --project backend --locked` then `uv run --project backend python -m unittest discover -s tests -v` |
| Rust agent | `backend/rust/` | `cargo build --release` (see README there) |

Hosted onboarding needs every variable listed in `Frontend/hosted.py` (`ENV`). Local arcade mode
works without them.

## Documentation

- [Architecture](docs/architecture.md)
- [Threat model](docs/threat-model.md)
- [Privacy](docs/privacy.md)
- [Data persistence](docs/data-persistence.md)
- [Model evaluation](docs/model-evaluation.md)
- [Demo runbook](docs/demo.md)
- [Pitch script](docs/pitch.md)
- [Remote ML API](backend/README.md)

## Contributing

PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/), for example
`feat(agent): add pause control` or `fix(ml): reject empty windows`. The `PR title` check enforces this.
Commit messages should use the same format. The `Backend` check runs Ruff and the Python tests.
The `Web` check type-checks and Prettier-checks `Frontend/` TypeScript, verifies the committed bundles match the
source, and runs the `Frontend/` tests.

Feature-window contract fixtures live in `shared/fixtures/feature-window-v1.json`. The backend and collector
tests both validate them, so update the fixtures with any schema change.