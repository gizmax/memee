# Contributing to Memee

Contributions are welcome. Before you invest significant time on a large
change, open an issue sketching the direction — it saves everyone rework.

## Quick start

```bash
git clone https://github.com/gizmax-cz/memee.git
cd memee
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

The first `pytest` run takes ~60 seconds. All network calls are blocked in
tests via `TRANSFORMERS_OFFLINE=1` / `HF_HUB_OFFLINE=1` (set in
`tests/conftest.py`), so tests stay deterministic regardless of connectivity.

## Coding style

- Type hints on every public function.
- Docstrings in English, short.
- Lines under 100 characters.
- `ruff check .` must pass.
- No new runtime dependencies without opening an issue first.
- Prefer plain SQL or SQLAlchemy Core over ORM-level magic for hot paths.

## Tests

Every bug fix ships with a test that fails before your change and passes
after. New features ship with at least one positive and one negative test.
Testing against real LLMs isn't required — mock the `source_model` /
validation metadata directly.

## Commits

- One topic per commit. Small commits win.
- Conventional Commits style (`feat:`, `fix:`, `perf:`, `docs:`) helps the
  changelog but is not enforced.
- Sign off every commit with `git commit -s` (DCO — Developer Certificate
  of Origin). No CLA is required.

## Pull requests

- One topic per PR.
- Link the issue it resolves (or explain why no issue exists).
- Add an entry to `CHANGELOG.md` under `## [Unreleased]` for any change
  visible to end users.
- CI (GitHub Actions) must be green.
- A maintainer will usually respond within a few working days; poke the PR
  if nothing happens within a week.

## What lives where

- `src/memee/engine/` — the core engines (confidence, search, router,
  quality gate, dream, propagation, etc).
- `src/memee/adapters/` — external integrations (CMAM bridge; others
  welcome).
- `src/memee/plugins.py` — hook registry for external extensions.
- `tests/` — everything is pytest. Test files named after what they prove.
- `docs/` — long-form documentation; markdown only.

## The paid package

`memee-team` (multi-user scope, SSO, audit log) is a separate proprietary
package, not in this repository. Contributions to it happen through the
licence-holder channel at [memee.eu](https://memee.eu). Plugin hooks in OSS
(`src/memee/plugins.py`) are the seam — if you need a new extension point
in OSS to support `memee-team` or any third-party plugin, open an issue
here.

## Code of Conduct

This project adopts the Contributor Covenant, v2.1. See
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Report any concerns to
`info@memee.eu`.
