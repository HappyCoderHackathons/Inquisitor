# Inquisitor

[Architecture and stack decisions](docs/architecture.md)

The local Flask and TypeScript arcade prototype lives in [`Frontend/`](Frontend/).
From the repository root, run `cd Frontend` before following its
[run instructions](Frontend/README.md). It serves an introduction at `/` and
the playable arcade at `/arcade`.
[Synthetic sample windows and generator instructions](sample-data/README.md)

[Remote ML service and API](backend/README.md)

## Contributing

PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/), for example
`feat(agent): add pause control` or `fix(ml): reject empty windows`. The `PR title` check enforces this.
Commit messages should use the same format. The `Backend` check runs Ruff and the Python tests.

Feature-window contract fixtures live in `shared/fixtures/feature-window-v1.json`. The backend and collector
tests both validate them, so update the fixtures with any schema change.
