# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] — 2026-04-24

Installer UX fixes and cosmetics. No API changes.

### Changed

- `memee setup` welcome box borders now land correctly when lines contain
  ANSI colour codes. A new `_visible_len()` helper strips escapes before
  `ljust` padding — previously the right `│` drifted left by ~8 columns.
- MEMEE ASCII logo dropped its blue→pink gradient and is now a single
  cyan-mint tone (#00E5C7 via 24-bit truecolor), matching the brand
  accent on [memee.eu](https://memee.eu). Added a matching `REMEMBER`
  farewell logo at the end of the wizard.
- Post-setup screen rewritten as `YOU'RE DONE` / `YOU CAN JUST TALK TO
  YOUR AGENT` / `CLI (OPTIONAL)`. Leads with "Memee is now live and
  fully automatic." and bullets what happens automatically: routed
  memories per task, cross-model sharing, org-wide mistake memory.
- Command examples in the post-setup screen no longer show the leading
  `$ ` prompt marker — users kept copy-pasting it and hitting
  `command not found: $`.
- The "Claude Code Integration" hint only prints when no MCP client was
  actually detected. If `memee doctor` already wired Claude Code /
  Cursor / Continue / Windsurf during setup, the screen says so
  instead of nudging the user to edit `~/.claude/settings.json` again.
- Install instructions in README and on the website switched from
  `pip install memee` to `pipx install memee` (with `python3 -m pip
  install memee` as fallback) — `pip` often isn't on PATH for fresh
  macOS users.

### Fixed

- Docs / site: removed the deprecated `gizmax-cz/memee` URL placeholder;
  all links now point to the real `gizmax/memee` repository.

## [1.0.0] — 2026-04-24

First public release.

### Added

- Sixteen core engines in `src/memee/engine/`: confidence scoring,
  hybrid search (BM25 + vector + tags), router with token budget,
  quality gate with dedup, lifecycle, dream mode, propagation, predictive
  warnings, inheritance, review, briefing, feedback, embeddings, research,
  impact, tokens, plus telemetry.
- `src/memee/adapters/cmam.py` — Claude Managed Agents Memory bridge with
  `fs` and `api` backends, secret redaction, auto-chunking, store caps.
- `src/memee/plugins.py` — hook registry so the paid `memee-team` package
  (multi-user scope, SSO, audit log) can plug in without OSS changes.
- 24 MCP tools exposed via FastMCP; 12+ REST endpoints including
  `/api/v1/retrieval`, `/api/v1/impact`.
- 25+ CLI commands (`record`, `search`, `suggest`, `check`, `propagate`,
  `dream`, `review`, `brief`, `inject`, `benchmark`, `cmam sync`,
  `feedback`, …).
- OrgMemEval v1.0 benchmark suite (8 scenarios), impact A/B harness,
  GigaCorp multi-month simulation.
- Alembic migration baseline matching the current model set.
- 201 tests; CI configured for Python 3.11 and 3.12 with `TRANSFORMERS_OFFLINE`.

### Pricing (memee-team, proprietary)

- Free OSS: $0 forever, MIT, single-user.
- Team: $49 / month flat, up to 15 seats, annual.
- Enterprise: from $12 000 / year, unlimited seats, SOC 2 Type II, SCIM,
  air-gap, dedicated CSM.
- 15–100 seats with no SOC 2 requirement → custom Growth plan by email.

Flat-per-team pricing (not per-seat) because Memee is shared memory
infrastructure, not a per-developer productivity tool. Value scales
sublinearly with headcount.

### Known limitations

- Vector search reads all embeddings into Python; scales cleanly to
  ~50 k memories. An ANN adapter (sqlite-vec / sqlite-vss) is on the
  post-launch backlog.
- Simulation numbers (see `docs/benchmarks.md`) are internal and
  synthetic. Third-party replication is welcome.
- Dedup thresholds need calibration on real customer data after a few
  weeks of production use.

See `docs/review-fixes.md` for the full pre-launch audit trail.
