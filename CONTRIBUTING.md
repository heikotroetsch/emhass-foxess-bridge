# Contributing

Issues and pull requests are welcome.

## Development setup

```bash
scripts/setup      # uv venv with Python 3.14, Home Assistant and the test tooling
scripts/test       # pytest, all tiers that run offline
scripts/lint       # ruff format + check
scripts/develop    # local Home Assistant on http://localhost:8123
```

The repository is test-driven. Write the failing test first, then the
smallest change that makes it pass. Tiers and conventions are described in
`docs/development-plan.md`.

- `tests/unit`: planner and scheduler model, pure Python.
- `tests/component`: the integration inside the Home Assistant test harness
  against `tests/component/fake_foxess_openapi.py`.
- `tests/contract`: the fake is checked against recordings from a real
  installation in `tests/fixtures/foxess_openapi`. Re-record with
  `scripts/record_contract.py` when ha-foxess-openapi changes.

Component tests freeze time with the `freezer` fixture, which also freezes
asyncio sleeps: never sleep in integration code paths the tests exercise.

## Field scripts

`scripts/deploy` copies the integration to a Home Assistant OS host over SSH
and restarts it. `scripts/write_cycle.py` writes one probe group through the
real ha-foxess-openapi services and restores the snapshot; it needs
`FOX_ALLOW_WRITES=1`. Both read `.env`, see `.env.example`.

## Releasing

`scripts/release 0.2.0` bumps `manifest.json`, moves the *Unreleased*
section of `CHANGELOG.md` under the new version, commits and tags `v0.2.0`.
Push the tag, publish a GitHub release for it, and the *Release* workflow
attaches the zip HACS installs.

## Rules

- Never commit API keys, serial numbers, account numbers or `.env` files.
  Fixtures are anonymised (`60SH5TEST000001`, `test_plant`).
- New user-facing strings go into `strings.json` and `translations/en.json`.
- Every change that touches behaviour gets a `CHANGELOG.md` entry.

By contributing you agree that your contribution is licensed under the MIT
License of this repository.
