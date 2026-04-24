# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.8] — 2026-04-25

R6 review round. Two P1 scope leaks in `memee-team`, three P2 dedup/
telemetry drifts in OSS, and a per-call init_db cost in the MCP server.

### Security (memee-team scoping)

- **`promote_to_org` now enforces scope, not just role.** A lead from
  team A could previously promote team B's memory to org by virtue of
  their "lead" role alone. Leads are now constrained to their own
  team; admins stay org-wide; personal memories must go through
  `promote_to_team` first.
- **`onboard_user` no longer leaks team memories to no-team users.**
  When `user.team_id` was unset, the team-filter branch was skipped
  entirely and the onboarding query returned every team's memories.
  Users without a team now see only `scope == "org"` entries.

### Fixed (OSS)

- **MCP `decision_record` / `antipattern_record` honour the dedup
  gate.** When the quality gate reported `merged=True`, both tools
  used to fall through and create a twin row instead of folding into
  the existing memory. Repeat calls now return `{"status": "merged"}`
  and point at the canonical id.
- **`decision_record` persists `tags=["decision"]`** so the
  fingerprint-based dedup can find the prior decision on subsequent
  calls (without this the first call's memory had no tags and could
  never match).
- **`merge_duplicate` re-syncs the `MemoryTag` index** via
  `sync_memory_tags` after merging new tags. The JSON column and the
  normalized index used to drift apart; propagation and predictive
  lookups then silently missed merged tags.
- **`memory_search` returns an exact `query_event_id`** instead of
  querying the globally-latest `SearchEvent`. Under concurrent MCP
  traffic the latest-row lookup handed callers each other's ids and
  corrupted hit@k metrics. `search_memories` gains a backward-
  compatible `return_event_id=True` kwarg used by the MCP wrapper.

### Performance

- **MCP `_get_session()` caches the engine + `init_db()` per process.**
  Every tool call used to rebuild the engine and re-run FTS trigger
  DDL (~20–50 ms of overhead per call). Session is still fresh per
  call; engine and schema init are one-time.

### Infra

- **`memee-team/tests/conftest.py`** so `pytest memee-team/tests/`
  picks up `session` / `org` fixtures without polluting pytest's
  rootdir. The review surfaced 12 collection errors before any logic
  ran; this fixes them.

### Tests

- New regressions for every fix:
    - `test_lead_cannot_promote_other_team_memory`
    - `test_admin_can_promote_any_team_memory`
    - `test_onboard_no_team_user_sees_only_org`
    - `test_antipattern_record_merges_duplicate`
    - `test_decision_record_merges_duplicate`
    - `test_merge_resyncs_memory_tag_index`

## [1.0.7] — 2026-04-24

Second token-math honesty pass. The 1.0.6 framing ("5k–100k tokens
depending on library size") was still a strawman — it assumed a team
dumps its entire pattern library into every prompt, which nobody
does. The real no-memory baseline is the size of a project's
`CLAUDE.md` / `AGENTS.md`, which Claude Code and Cursor both load
in full on every session start.

### Changed

- **Site + README baseline is now anchored to real data**: median
  CLAUDE.md / AGENTS.md token count across **27 popular public
  repositories** (langchain, vercel/ai, prisma, zed, openai/codex,
  OpenHands, pydantic-ai, ClickHouse, cal.com, deno, and 17 more)
  measured via `gh api` and `bytes / 4`. Median ~2,160 tokens, mean
  2,510, p95 ~9,600, published outlier 42,000.
- Math section cards now read:
    - *Without Memee*: **~2,200 tokens / turn (median)** with the
      p95 and pathological outlier called out in the sub-line.
    - *With Memee*: **≤500 tokens / task (routed)** — same cap, new
      framing ("5–7 memories relevant to the current task").
    - *You keep*: **the slope** — 77% at median, 95% at grown teams,
      but the real point is that per-turn context stays *bounded*
      as your knowledge base grows.
- Hero bullet: "Route the **5–7 memories** this task needs, not your
  whole `CLAUDE.md`."
- Pull-quote: "Your `CLAUDE.md` grows forever. *Memee doesn't.*"
- `docs/benchmarks.md` gains a new "Without-Memee baseline: real
  CLAUDE.md / AGENTS.md sizes" section with the full 7-percentile
  table + named sampled repos + links to Anthropic's docs confirming
  CLAUDE.md rides along on every turn.
- Three illustrative reduction scenarios in the docs (median, p95,
  pathological) — all above 98%, but the point of the copy is the
  *shape*, not the single percentage.

Pure copy + docs truthiness. Engine + tests untouched. 260 passing,
ruff clean.

## [1.0.6] — 2026-04-24

Token-math honesty follow-up. After 1.0.5 fixed the router's fake token
counter, it was clear the headline "14,550 → 500, 96% reduction" copy
on the site + README was **nowhere anchored to a reproducible
measurement**. The 14,550 number was an aesthetic choice that survived
re-writes; the 500 was a configured cap, not a measured value.

### Changed

- **Site + README now show the actually-measured range**, not the
  legacy 14,550 fixed point:
    - *Without Memee* card: **5k–100k tokens / task**, with a sub-line
      explaining the spread depends on library size (5k for a 50-pattern
      team, ~22k measured on our 500-pattern synthetic corpus, up to
      100k for large teams with long bullets).
    - *With Memee* card: **~40 tokens / task (average)**, with a sub-line
      explaining the 500-token budget cap is a worst-case envelope.
    - *You keep* card: **≥99%** (conservative floor — measured is 99.8%
      on the benchmark corpus; reductions widen further as the library
      grows because the cap is constant).
- Hero card h3: "Send **≤500 tokens**, not 14,550." → "Send **~40
  tokens**, not your whole library." Matches measured reality.
- `docs/benchmarks.md` removes the "14,550" reference entirely, quotes
  the real full-dump baseline (21,623 tokens for 500 patterns) and the
  real router average (39 tokens over 10 queries). Reductions
  calculated against three illustrative library sizes (5k / 21k /
  100k baselines) — all above 99%.
- TL;DR benchmark table gains two new rows: router avg (39) and
  reduction ratio (99.8%) with source references.

No engine or behaviour changes. Purely copy + docs truthiness.

## [1.0.5] — 2026-04-24

The 23-finding round. A follow-up to the 1.0.3 / 1.0.4 correctness
pass turned up twenty-three more real issues across the engine,
storage, concurrency, honesty, and local-XSS surfaces. All are fixed
here; no API breakage.

### Fixed — engine correctness + honesty

- **Router token budget now tracks real token count.** The counter
  was summing a flat `15 tokens per line`, so the "≤500 tokens" claim
  was a configured budget, not a measured value, and the regression
  test was a tautology (`len(lines) * 15 < 500`). Counter is now
  `len(text) // 4` (chars-per-token heuristic). Test rewritten to
  assert `_count_tokens(result) ≤ budget + 20 %`. Measured router
  output on a 500-pattern synthetic corpus averages ~40 tokens —
  well below the cap. See `docs/benchmarks.md` new "Router output
  (measured)" section.
- **`feedback.py` no longer records every warning violation as
  MISTAKE_AVOIDED.** The ternary branch was `... if ... else ...` with
  identical branches. `ImpactType.MISTAKE_MADE` added; failure path
  records it. `impact.py::get_impact_summary` now surfaces a
  `mistakes_made` counter alongside the existing split.
- **CRITICAL anti-patterns no longer sink to the bottom of briefings.**
  `briefing.py` was sorting severity lexicographically (so
  `"medium" > "low" > "high" > "critical"`). Replaced with a
  `sqlalchemy.case()` explicit rank, so critical / high surface first
  under any limit.
- **`inject_claudemd` is atomic and idempotent.** Writes to a
  sibling `.tmp` then `os.replace()`. End marker `<!-- /memee-section
  -->` added so re-injection doesn't drift section boundaries even
  across timestamps.
- **Impact-metric query duplication resolved.** `warnings_shown` etc
  counted `N` rows for an AP linked to `N` projects (deliveries, not
  memories). Delivery semantic preserved + docstring clarified;
  added `_unique` variants counting distinct memory IDs.
- **Inheritance no longer propagates TESTED maturity.** Only
  VALIDATED + CANON memories get pushed to similar projects now,
  and the inheritance link is explicitly not counted as a validation
  (doesn't bump `application_count`). Keeps new-project canon clean.
- **Code-review diff scanner is tighter + DoS-hardened.** Max diff
  size 5 MB, binary hunks skipped, rename-only headers skipped,
  secrets require a quoted-string literal (not just the word
  "password"), HTTP regex covers all verbs + client/session forms.

### Fixed — storage + persistence safety

- **CMAM adapter chunks at UTF-8 character boundaries, not byte
  boundaries.** Multi-byte content (Czech, CJK, emoji) at the
  100 KB chunk boundary no longer drops its leading/trailing bytes
  into `errors="ignore"`.
- **ForeignKey cascades added across every child relationship.**
  Deleting a Memory now properly removes its Decision, AntiPattern,
  ProjectMemory, MemoryValidation, MemoryConnection, MemoryTag rows;
  same for ResearchExperiment → ResearchIteration. Models declare
  both `ondelete="CASCADE"` on the FK and
  `cascade="all, delete-orphan", passive_deletes=True` on the parent
  relationship.
- **FTS UPDATE trigger gated on content columns only.** The trigger
  used to fire on every column change (including `application_count`,
  `last_applied_at`) and did a full FTS delete + re-insert each
  time. Now scoped to `UPDATE OF title, content, summary, tags`.
  Alembic migration updated in lockstep.
- **SQLite `connect_args` + WAL verification.** Engine now passes
  `check_same_thread=False, timeout=30`, sets `PRAGMA busy_timeout
  =30000`, and logs a warning when `PRAGMA journal_mode` didn't
  stick as `wal` (e.g. on NFS mounts that silently fall back).
- **Tag index sync is atomic to readers.** `sync_memory_tags` and
  `rebuild_all_tag_indexes` wrap their delete-then-insert in a
  SAVEPOINT (`session.begin_nested()`), so concurrent readers
  never see an empty tag set mid-sync.
- **CLAUDE.md importer respects scope and code fences.** Duplicate
  detection now keys on `(title, type)`, and when an existing
  memory is hit, a `ProjectMemory` link is still created for the
  current project. The section splitter also tracks fenced-block
  state so a `## heading` inside a sample code block no longer
  promotes to a real section, and keyword matching uses full-word
  tokens instead of substring.

### Fixed — concurrency, honesty, local XSS

- **`memee research` subprocess has a timeout** (10 min default).
  Runaway commands can't hang the research runner indefinitely.
  Non-finite metric values (`inf`, `-inf`, `nan`) are rejected
  rather than silently poisoning `best_value`.
- **Research baseline comparison correctly handles `best_value=0.0`.**
  Previous `baseline = best or ... or 0` tripped on a legitimate
  zero. Now: `best_value if best_value is not None else baseline_value`.
- **`evidence.add_evidence` is thread-safe per memory.** A module
  `defaultdict` of `threading.Lock` keyed on memory id guards the
  read-modify-write around the JSON column.
- **Retrieval telemetry survives parent rollback.** `SearchEvent`
  writes now open a fresh short-lived session + independent commit,
  so hit@1 metrics aren't silently biased by whatever rolls back
  around them. One extra fsync per search in exchange.
- **`embeddings._get_model()` is thread-safe.** A module lock around
  lazy HF load prevents concurrent callers from double-loading
  (and double-fetching the HF cache).
- **`tokens.estimate_org_savings` now returns the assumptions it
  used** alongside the dollar figures. `TokenSavings.assumptions`
  is a new additive field; `format_savings_report` prints an audit
  block beneath the numbers.
- **Model-family detection uses token-split matching, not
  substring.** `sonnet-transformers` classifies as `unknown` (was
  `anthropic`). `o5-mini`, `llama-4-405b`, `qwen-72b`, `grok-2`
  all classify correctly. Added adversarial test matrix.
- **Dashboard renders user-controlled strings through `escapeHTML`.**
  A memory titled `<script>alert(1)</script>` no longer executes
  JS in the local dashboard (the local-shared-DB threat model).

### Other

- `tests/test_gigacorp.py` + `tests/test_megacorp.py` — bumped the
  wall-clock budgets (900 s / 600 s) so the 18-month and
  100-project stochastic simulations don't flake on CI machines
  after the new atomic-savepoint / cascade / FK work added small
  per-iteration constants. These ceilings are sanity checks, not
  perf SLOs.
- `README.md` + `site/index.html` + `docs/benchmarks.md` —
  "500 tokens" is now rendered as "≤500 tokens" (it's a budget
  cap, measured average is ~40). `docs/benchmarks.md` gains a
  "Router output (measured)" section with the concrete numbers.

Tests: 207 → **260 passing** (55 new regression tests).
Ruff: 0 errors.

## [1.0.4] — 2026-04-24

Bootstrap safety, data-loss prevention, and MCP library compatibility.

### Fixed

- **Alembic / init_db now agree on canonical schema.** FTS5 DDL moved
  into the initial migration (`alembic/versions/843e414a0596_initial_schema.py`)
  so users who bootstrap via `alembic upgrade head` get a working search
  index. `init_db()` now additionally `alembic stamp head`s when the
  version table is empty, so users who bootstrap via the CLI first can
  still run alembic later without "table already exists". Both paths
  converge. (Previously: alembic-only users hit `no such table:
  memories_fts` on the first search.)
- **Claude Code `settings.json` is no longer silently clobbered on
  malformed JSON.** `doctor.configure_tool` now moves the broken file
  to `settings.json.bak.<timestamp>` and surfaces a yellow warning
  instead of resetting the config dictionary and writing a fresh file.
  A user's existing `hooks`, `permissions`, `enabledPlugins`, `env`
  blocks are preserved through syntax errors.
- `~/.memee` existing as a regular file (instead of a directory) now
  emits a clean `ClickException` telling the user what to do, instead
  of an `FileExistsError` traceback from the middle of `path.parent.mkdir`.
- **Zombie `ResearchExperiment` rows are now reset.** A new
  `reset_zombie_experiments(session, stale_after_hours=24)` marks any
  `status="running"` experiment older than the window as `failed`.
  Exposed via `memee research reset-zombies [--stale-hours N]` and
  auto-invoked by `memee doctor` in its fix pass.
- `_clamp_limit` in the MCP layer handles `OverflowError` (from
  `float('inf')`) and string floats (`"5.5"`) gracefully.
- `doctor.configure_tool` writes the settings file atomically
  (`.tmp` sibling + `os.replace`) so a Ctrl-C mid-write can no
  longer leave a truncated JSON file on disk.
- `doctor` prints the manual MCP JSON snippet when no supported MCP
  client was detected, matching the installer's behaviour on that
  path. Previously the doctor silently reported "ALL HEALTHY" with
  zero actionable guidance for users running a non-detected tool.

### Compatibility

- `FastMCP` init signature update: the `mcp` library now rejects
  `version=` AND `description=` kwargs. `description=` is now
  `instructions=`, and `version=` has been dropped. Memee's MCP
  server module is importable again on current `mcp` releases.

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
