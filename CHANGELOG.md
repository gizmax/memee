# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.3] — 2026-04-24

Correctness, performance, and API hygiene pass. Fourteen findings from
two independent audits (engine bugs + perf profile + API review),
addressed in one batch.

### Fixed

**Confidence engine (`src/memee/engine/confidence.py`)**

- One-time backfill is actually one-time. Both `validated_project_ids`
  and `model_families_seen` now set an in-memory sentinel after the
  first backfill attempt so subsequent calls skip the query entirely.
  Profile showed the old code re-querying on ~53% of validation calls
  for memories whose first event was an invalidation.
- Cross-model diversity bonus fires only on the first time each model
  family validates a memory, including the author's own family. The
  previous check compared the new validator to `memory.source_model`
  only, so the same family could trigger the bonus repeatedly across
  separate validations. Honest semantic: validator_family ∉ families_seen.
- `application_count` no longer bumps on invalidation. An invalidation
  signal is counted separately via `invalidation_count`; auto-deprecation
  and the tested-maturity gate now read `application_count + invalidation_count`
  so a memory exercised via invalidations is still eligible for
  deprecation at low confidence, without conflating "tried and failed"
  with "applied successfully".
- LLM quarantine tightened: removed the `validation_count ≥ 3` escape
  hatch that let a single chatty model self-validate out of hypothesis.
  Promotion now requires real diversity (cross-model or cross-project)
  for VALIDATED, and cross-model evidence specifically for CANON of
  LLM-sourced memories.

**Search and lifecycle**

- FTS5 query rewrite is AND-by-default with an OR fallback on zero
  results, and sanitized-to-empty queries no longer leak the raw
  input into a syntax error. Before, a 6-word query would OR across
  all tokens and return any memory touching any token — correct
  memories were drowned by noise. (`src/memee/engine/search.py`)
- `get_expiring_memories` now honours the `within_days` parameter
  it always accepted; the function computed a `warn_threshold` but
  never added it to the WHERE clause.
- Dream-mode connection-boost now caps the neighbor-count multiplier
  at 5. The previous unbounded formula let 20 weakly-connected tag
  neighbours inflate confidence by ~0.074 per nightly cycle without
  any real new validation.

**API / CLI / MCP hygiene**

- FastAPI `get_db()` is a proper yield-and-close generator, so request
  scopes no longer leak SQLite connections. The prior `return` pattern
  bypassed the closer FastAPI runs only for generators.
- Global FastAPI exception handler: any unhandled exception now logs
  the traceback server-side and returns a clean
  `{"error": "internal_server_error", "detail": ...}` JSON payload.
- `memee.cli:main` entry wraps the Click group. Any uncaught exception
  prints `memee: <message>` to stderr and exits 1 instead of dumping
  a raw Python traceback; set `MEMEE_DEBUG=1` to re-raise for local
  debugging.
- MCP tools bound their response sizes: `memory_search` and
  `memory_suggest` clamp `limit` to [1, 200], and `canon_list` added
  a `limit: int = 100` param (max 500). Unbounded lists could have
  exceeded MCP JSON-RPC frame limits at scale.
- `memory_record(context=...)` and `decision_record(alternatives=...,
  criteria=...)` replace `json.loads` with a safe helper that returns
  a structured rejection instead of a 500 when the model passes raw
  text in what should be a JSON string.
- `research_create(baseline: float = -1)` is now `Optional[float] =
  None`, so metrics where `-1` is a legitimate value (size deltas,
  signed offsets) no longer silently collapse into "baseline unknown".

### Added

- `tests/test_search.py`: AND-semantics tests, OR-fallback test, and
  sanitize-empty test.
- `tests/test_lifecycle.py`: `test_get_expiring_memories_filters_by_age`.
- `tests/test_improvements.py`: dream-boost bounded test for weak
  neighbour clusters.

Tests: 201 → **207 passing**. Ruff: 0 errors.

## [1.0.2] — 2026-04-24

Release-hygiene pass triggered by a third-party pre-launch review.
No behavioural or API changes; everything below is packaging, docs, or
lint cleanliness.

### Changed

- `ruff check .` is now clean across the whole codebase. Pragmatic
  ignores for `E402`, `E741`, and `F841` are documented in
  `pyproject.toml` alongside why each one is safe in Memee's context.
- `sdist` packaging excludes tightened so a build from the private
  monorepo cannot accidentally ship the paid `memee-team/` package or
  any internal-only docs (`trial-and-licensing.md`, `launch-posts.md`,
  `microsite-brief.md`, `publish-oss.md`, `project_split.md`).
- `scripts/publish_oss.sh` excludes the same three internal-only docs
  so a public-mirror sync cannot leak operator-only material either.
- README benchmark numbers reconciled with `docs/benchmarks.md` ground
  truth: OrgMemEval 92.4/100 (was 93.8), GigaCorp 100 projects and 3×
  annual ROI at the flat $49/mo Team tier (was "200 projects / 7×"
  from the deprecated $199/mo Org tier), A/B ROI 10.7×, competitor
  baseline expressed as a range (0.9–3.5) instead of a single figure,
  reproducibility block updated to "~60s" full-suite runtime. The
  "No account, no network call" line softened to reflect that the
  optional `sentence-transformers` embedding path fetches a HuggingFace
  model on first use (skipped with `TRANSFORMERS_OFFLINE=1`).
- `SECURITY.md`: explicit note that `memee research` runs user-supplied
  `verify_command` / `guard_command` via `subprocess.run(..., shell=True)`,
  with guidance for shared/untrusted environments.

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
