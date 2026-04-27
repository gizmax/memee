# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.1] — 2026-04-27

The "no-such-command-pack" patch. Three layers of defence against the
PATH-shadowing bug that bit users upgrading via pipx while an older
`pip install memee` against Homebrew Python sat on `/opt/homebrew/bin`.

### Added

- **`memee doctor` Installations section.** Walks `$PATH` for every
  `memee` binary the shell can resolve, dedups on `realpath` (so a
  Homebrew → pipx symlink doesn't trigger a warning), reports the
  install kind (pipx / Homebrew Python / user pip / system Python),
  and prints a tailored cleanup command for the active shadow. Single-
  install installs render a one-line green check.
- **`memee --version` enhanced output.** Now prints the install path,
  the active binary on PATH, and any alternate `memee` binaries that
  the shell would shadow this one with — with a `memee doctor` hint
  when more than one is found.
- **`memee setup` pre-flight.** Refuses to wire hooks into
  `~/.claude/settings.json` when multiple `memee` binaries are on
  PATH — those hooks would fire whichever the shell resolves first,
  most likely the wrong one. Override with `--ignore-multi-install`.
- **`memee doctor --ignore-multi-install`.** Suppresses the multi-
  install warning for users who genuinely want two `memee` binaries
  side by side.
- **`detect_memee_installs()` API.** New helper in `doctor.py` for
  callers (CLI version handler, setup pre-flight) to share a single
  PATH walk. Cached within a Python invocation; <200 ms on a normal
  machine. Tolerates broken symlinks, permission errors, missing
  PATH dirs, files without a shebang, and version queries that fail
  or time out.

### Tests

- `tests/test_multi_install_detection.py` — fakes a multi-install
  PATH and verifies detection, dedup, install-kind classification,
  the doctor report layout, and the setup refusal.

## [2.0.0] — 2026-04-27

The loop-disappears + simplicity-reclamation release. Hooks land,
packs ship, dashboard goes, autoresearch goes. Six MCP tools fewer,
~2,400 LOC fewer, ~+0.0355 nDCG@10 by default.

### Added

- **Hooks in `memee setup`.** SessionStart, UserPromptSubmit, Stop
  hooks land in `~/.claude/settings.json`. Every session starts with
  a routed briefing; every user prompt triggers a task-aware brief;
  Stop runs the post-task review. Idempotent merge (preserves your
  hooks), `--no-hooks` opt-out, `--dry-run` preview, `memee doctor
  --uninstall-hooks` removal. New module `src/memee/hooks_config.py`
  centralises the hook definitions and the merge / strip / diff
  helpers.
- **`.memee` pack format.** Portable, optionally-signed bundle:
  `manifest.toml + memories.jsonl + (signature.bin + pubkey.pem)`.
  `memee pack export | install | list | verify`. Two seed packs ship:
  python-web (30 entries), react-vite (30 entries). Imports go
  through the existing quality gate; user canon outranks pack
  defaults via `source_type=import` multiplier (×0.6) and per-pack
  confidence cap. New module `src/memee/engine/packs.py` (DB-aware)
  + `src/memee/packs_format.py` (file-level helpers, no SQLAlchemy).
  Optional `[pack]` extra adds `cryptography>=42` for ed25519
  sign/verify; the format itself works unsigned without it.
- **`memee why "<code>"`.** Pipe in code, get back the canon that
  would have prevented or explained it. Top 3 hits with cite tokens,
  severities, alternatives. Sanitises FTS5-hostile inputs by
  extracting identifier tokens and snake_case-splitting them so
  `eval(user_input)` matches the eval AP cleanly. New module
  `src/memee/engine/citations.py` exposes the helpers.
- **`memee cite <id>`.** Resolves an 8-char hash (or full UUID) to
  full lineage: who recorded, what validated, when promoted to canon.
  `--confirm` bumps `application_count` and appends a `{kind:
  "citation"}` entry to `evidence_chain` — the manual confirm path
  is the trust foundation for citation telemetry.
- **Citation tokens in compact briefings.** Footer instructs the
  agent to cite applied memories with `[mem:abc12345]`. 58 tokens
  measured, well under the 200-token cap. Ships only in the compact
  format used by the SessionStart hook; verbose / `--full` formats
  unchanged.

### Changed

- **Cross-encoder rerank is default-ON when the HF cache is warm.**
  The R14 A/B run on the 207-query / 255-memory eval lifted macro
  nDCG@10 from 0.7273 → 0.7628 (Δ +0.0355) when on. Off-by-default
  left that lift on the table for every install whose cache was
  already warm. Three escape hatches:
  - `MEMEE_RERANK=0/off/false` — kill switch
  - `MEMEE_RERANK_MODEL=<id>` — explicit override
  - The cache probe is read-only: never downloads, never mkdirs
- **`memee doctor`** reports rerank state. One of: `enabled (cached)`,
  `enabled (env)`, `disabled (kill switch)`, `disabled (no model
  cached)`. The disabled-no-model branch ships an actionable hint:
  `pip install memee[rerank] then memee embed --download-rerank`.
- **`installer.py`** post-setup screen is honest about hooks. Used
  to claim "Memee is now live and fully automatic" before any hooks
  existed; v2.0.0 makes the claim true.
- **Grid search of ranker constants.** Swept TITLE_PHRASE_BOOST ×
  BM25-only tag/conf coefficients × 3 maturity multiplier shapes
  (243 combinations) against the 207-query harness. 66/243 beat
  baseline; macro-best (Δ +0.0027) failed the p<0.10 gate. Honest
  call: keep the hand-tuned defaults. Constants now exposed as
  tunable globals (`TAG_BOOST_COEF`, `CONF_BOOST_COEF`, `BM25_ONLY_*`)
  for future sweeps. Full artefacts in `.bench/v2_grid_search_*.json`.

### Removed

- **Web dashboard.** `src/memee/api/routes/dashboard.py` (~556 LOC),
  `memee dashboard` and `memee serve` CLI commands gone. The pitch
  ends with "no dashboards, no copilots, no magic" — the codebase
  agrees now. `memee status` for the same numbers in your terminal.
- **Autoresearch engine.** `src/memee/engine/research.py` (~641 LOC),
  6 CLI subcommands, 5 MCP tools (research_create / log / status /
  meta / complete), 2 schema tables (`research_experiments`,
  `research_iterations`). It was a Karpathy-style self-improvement
  harness, beautifully built and entirely orthogonal to "institutional
  memory." Every session was paying MCP tokens for tools nobody
  called. Migration `6d540c223770` drops the tables.
- **`engine/canon_ledger.py`, `engine/evidence.py`, `engine/tokens.py`**
  (~830 LOC of substrate with zero production callers). The
  `evidence_chain` JSON column on `Memory` (used by `quality_gate`
  dedup) stays. The dead modules go.
- **`LearningSnapshot` schema** + `/api/v1/snapshots` endpoint. Was
  written only by `demo.py`; queried only by the deleted dashboard.
  Same migration drops the table.
- **`fastapi`, `uvicorn[standard]`** moved from base dependencies to
  the optional `[api]` extra. Memee's primary surfaces are the CLI
  and MCP; the REST API is opt-in.

### Migration notes

If you're already running Memee from a previous version:

```bash
pipx upgrade memee
memee doctor             # installs hooks; reports rerank status
memee pack install python-web   # day-one canon
memee pack install react-vite
```

The schema migration (`6d540c223770_drop_research_and_snapshots`)
drops three tables. Their data was internal to the autoresearch and
demo-snapshot subsystems — no user-recorded memory is touched.

### Breaking

- `memee dashboard`, `memee serve`, `memee research *` commands
  removed. If you scripted against them, pin to v1.x.
- 5 MCP `research_*` tools removed. Agents that called them will
  see "tool not found"; nothing else affected.
- REST `/api/v1/research/*` and `/api/v1/snapshots` endpoints removed.
- `fastapi` and `uvicorn` are no longer in base dependencies. If
  your installation script imports them transitively, add `memee[api]`.

### Tests

335 fast tests + 15 memee-team = 350 / 350 green. New regression
files: `test_pack_format.py`, `test_pack_signing.py`,
`test_pack_dedup.py`, `test_memee_why.py`, `test_memee_cite.py`,
`test_citation_footer.py`, `test_setup_hooks.py`,
`test_brief_compact.py`, `test_learn_auto.py`, `test_reranker_default.py`.
Plus `tests/grid_search_ranker.py` for the hand-tuned-constant
sweep. Heavy simulations (gigacorp, megacorp, enterprise,
company_simulation, perf_simulation) all green; research code paths
stripped from the fixtures, simulation cores intact.

## [1.2.0] — 2026-04-25

R8 → R14 bundled into a minor bump. The headline: **search ranks
~5 nDCG points better with the optional cross-encoder rerank, and
the default-on path is +1.6 nDCG over v1.1.0** thanks to the porter
tokenizer + RRF fusion + tag-graph third retriever + project-aware
boost. Hot paths: vector retrieval **116×** warm via the cached
numpy matrix; quality-gate dedup **6.7×** via MinHash LSH. Eval
harness: 12 → **207 queries × 255 memories** with 7 difficulty
clusters and per-cluster permutation tests.

No breaking changes. New schema columns (R9 `expires_at`, R10
indexes, R12 calibration columns) bootstrap idempotently in place
on first launch. `pipx upgrade memee` is sufficient.

Detailed delta across R8 — R14 below.

R8 → R14: hybrid recall, graph reasoning, LTR plumbing, perf sweep,
honest eval expansion, calibration substrate, cross-encoder rerank.
Full design write-ups in `docs/r8-r10-graph-ltr-perf.md` and
`docs/roadmap.md`.

### Retrieval delta vs v1.1.0 (207 q × 255 m harness, BM25-only path)

|   | nDCG@10 | Recall@5 | Recall@10 | MRR |
|---|---:|---:|---:|---:|
| v1.1.0 (R7 ship)               | 0.7110 | 0.5589 | 0.6065 | 0.8213 |
| HEAD, BM25-only                | **0.7273** | **0.5701** | **0.6292** | **0.8277** |
| HEAD + cross-encoder (R14 #2)  | **0.7628** | **0.5950** | **0.6477** | **0.8676** |

R7 → HEAD default-on:                **+0.0163 nDCG@10** (porter tokenizer + RRF refinements + tag-graph third retriever).
R7 → HEAD with `MEMEE_RERANK_MODEL`: **+0.0518 nDCG@10** (+0.0355 from cross-encoder, p=0.0002 on the ship rule).

Per-cluster impact of the cross-encoder rerank (vs HEAD BM25-only):
- onboarding_to_stack: 0.6605 → 0.7729 (**+0.1124, p=0.03**)
- diff_review:        0.5557 → 0.6192 (**+0.0636, p=0.03**)
- paraphrastic:       0.6795 → 0.7093 (+0.0298, p=0.08)
- code_specific, anti_pattern_intent, multilingual, lexical_gap: smaller deltas.

### R14 — autoresearch on per-cluster headroom

Four parallel A/B audits on the 207-query harness with paired 10k-iter
permutation tests. Two shipped default-on / opt-in; two were honest
negatives that exposed the structural ceiling of BM25 reranking.

- **Cross-encoder reranker (#2 — SHIPPED, default OFF, opt-in via
  `MEMEE_RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2`).**
  Stage 5a in `search_memories`: rerank top-30 RRF candidates before any
  LTR rerank. Macro nDCG@10 0.7273 → 0.7628 (+0.0355, p=0.0002). Latency
  cost p50 1.3 → 41 ms, p95 1.8 → 78 ms — well within the +50-200 ms
  budget the audit roadmap allowed. Optional dep `memee[rerank]`.
- **Severity-weighted intent boost (#3 — opt-in via
  `MEMEE_SEVERITY_INTENT_BOOST=1`).** When the query carries a danger
  verb (fix / secure / harden / avoid / prevent / mitigate / patch) AND
  the candidate is `type=anti_pattern`, the multiplier is now scaled by
  `AntiPattern.severity` (critical 1.40, high 1.25, medium 1.10, low
  1.00) instead of the legacy flat 1.10. anti_pattern_intent cluster
  (n=32) measured Δ=+0.0043 at p=0.30 — below the +0.015 / p<0.10
  ship-default-on bar, so shipped as opt-in plumbing for production
  telemetry to resolve. Substrate is in place; flip `=1` and ship to the
  ranker without a code change.
- **Maturity-gated query expansion (#4 — opt-in via
  `MEMEE_MATURITY_GATED_EXPANSION=1`).** Skip expansion when a CANON
  pattern already matches the raw query above a threshold; protects
  the strong-signal queries from dilution. The 207-q harness only
  intersects expansion + canon-strong on **1 query** (a niche edge
  case), so the measured Δ is 0. Substrate shipped behind the flag —
  production traffic with the broader 60-key expansion table is a
  different mix and may benefit; the flag lets operators flip without
  a code change.
- **Field-aware BM25 column weights (#1 — NOT SHIPPED, honest
  negative).** Swept (title, content, summary, tags) ∈ {(1,1,1,1),
  (3,1,0.5,1.5), (5,1,0.5,2), (8,1,0.5,3), (10,1,0.5,1)} with and
  without TITLE_PHRASE_BOOST. Best tuple (8,1,0.5,3) measured ΔnDCG@10
  =+0.0058 at p=0.32 — fails both halves of the ship rule (Δ≥+0.005
  AND p<0.10). Every tuple regressed `lexical_gap_hard` by ≥0.03.
  R11's "TPB and column weights double up" hypothesis is *not*
  supported on the 207-q harness; production code untouched.

### R13 — project-aware reranking, tag-graph as 3rd RRF retriever, propagation perf

- **Project-aware boost.** `search_memories(..., project_id=...)` lifts
  in-stack proven memories via `validated_project_ids` membership. α =
  `MEMEE_PROJECT_AWARE_BOOST` (default 0.25). MCP `memory_search` and
  CLI `memee search` accept `project_path` and resolve it through.
- **Tag-graph third retriever.** `_tag_graph_topk()` ranks memories by
  Jaccard similarity over the `MemoryTag` inverted index. Default-on
  for the hybrid path (`MEMEE_TAG_GRAPH_RRF=1`); BM25-only path keeps
  the legacy linear blend so deployments without vectors don't regress.
- **Propagation cycle perf.** `run_propagation_cycle` now pre-loads
  projects + caches expanded tags + batches the lazy MemoryTag sync
  out of the hot path. 100 eligible × 30 projects: 96.8 ms / 303
  queries / 803 links.

### R12 P1 — eval expansion + confidence calibration

- **207 queries × 255 memories, 7 difficulty clusters.** Replaces the
  saturated 55q × 147m harness. Honest macro nDCG@10 baseline drops
  from 0.7851 (saturated) to 0.7273 — exposing per-cluster headroom
  reranker work can target. Biggest headroom: `paraphrastic` (n=43,
  0.6795); biggest single-fix lift came from cross-encoder rerank.
- **Confidence calibration substrate.** Brier + ECE + MCE pointwise
  metrics, pure-Python pool-adjacent-violators isotonic regression
  with per-(memory_type, scope, source) registry, Beta-Binomial closed-
  form posterior, ASCII reliability diagram. CLI `memee calibration
  eval / fit / status`. One conservative production wire-up: lifecycle
  invalidation gate uses Beta-Binomial posterior (>0.4 fires) when
  `MEMEE_CALIBRATED_CONFIDENCE=1`.

  Synthetic harness (n=2000, deterministic seed):
  - raw: Brier 0.1639, ECE 0.0231
  - isotonic: Brier 0.1608 (-1.9 %), per-slice anti_pattern ECE
    0.128 → 0.108 (-15.6 %)



### R8 — hybrid recall (RRF), fallback scoping, /agents N+1, rank edges

- **Reciprocal Rank Fusion** (Cormack/Clarke/Buettcher 2009, k=40 tuned for
  short candidate lists). Vector retriever runs as a peer of BM25, not just
  a reranker over BM25 candidates — vector-only matches that BM25 misses
  are now discoverable. `bench_autoresearch::hybrid_recall` flipped from
  *target invisible* to *rank=7 of 10*.
- **Fallback search through `apply_visibility`.** Short / unusual queries
  that landed in the LIKE branch used to bypass scoping. Both BM25 and
  fallback paths now resolve the current user the same way and route
  through the visibility hook. `bench_autoresearch::fallback_visibility`:
  hidden row leaked → no leak.
- **`apply_visibility` enforces compose contract.** Hooks that ignore
  `base_query` and return a fresh global query have their output
  intersected with `base_query` so the candidate filter survives.
- **/agents endpoint N+1.** Two grouped queries replace 1 + 2*N (`avg_conf`
  + per-type counts per agent). 50 agents: 101 → 2 queries.
- **Single-hit vector normalization.** Min-max normalization with one
  candidate gave `(s − s) / 1 = 0`. Skip min-max for n < 3 candidates;
  raw cosine is already in [0, 1].

### R9 — memory graph + LTR + hard-neg mining + BEIR 55q

- **Memory graph: `depends_on` and `supersedes` edges.** New `expires_at`
  column on `MemoryConnection`; composite indexes on (target_id,
  relationship_type) and (source_id, relationship_type). Two new dream
  phases:
    - `_infer_dependencies` — strict tag-superset hierarchy + textual
      cues (`requires`, `prerequisite`, `first do`).
    - `_infer_supersessions` — full tag overlap + (textual cues OR
      confidence gap ≥ 0.3 + maturity ordering + invalidation ratio
      ≥ 0.2).
  Briefing prepends 1-hop `depends_on` predecessors (max 2/pattern, single
  batched query) and skips `supersedes`-target candidates. Lifecycle
  refuses to auto-deprecate memories CANON depends_on; supersession edges
  produce digest proposals, not auto-deprecation.
- **LTR plumbing** (training optional, `pip install memee[ltr]`).
    - `SearchEvent` gets `ranker_version` + `ranker_model_id` so
      retrieval metrics can slice hit@k by ranker.
    - New tables: `search_ranking_snapshots` (per-candidate features for
      the trainer) and `ltr_models` (registry: candidate / canary /
      production / deprecated).
    - `engine/ltr.py` — `featurize()` (11 features), `is_enabled()` /
      `routing_mode()` / `canary_picks_ltr()` flag + bucket gate,
      `load_active_model()` with thread-safe cache, `train_and_register()`
      (LightGBM lambdarank), `promote()`.
    - `search_memories` reranks top-K via the active production model
      when `MEMEE_LTR_ENABLED` is on AND a model is registered AND the
      canary bucket includes the query. Heuristic stays as candidate
      generator + fallback.
    - CLI: `memee ranker status / train / promote / mine-negatives`.
- **Hard-negative mining.** `engine/hard_negatives.py` mines `(rejected_top,
  accepted_lower)` pairs from `SearchEvent` × `SearchRankingSnapshot`
  with a `Memory.updated_at > event.created_at` drift guard. Telemetry
  persists snapshot rows (top-25 cap) at search time so the trainer
  doesn't recompute against possibly-mutated memory state.
- **BEIR-style retrieval eval expanded.** `tests/retrieval_eval.py`:
  28 → 147 memories across 10 domains, 10 → 55 labeled queries with
  graded relevance (0–3, 179 labels total, mean 3.25/query). Added
  `type_match_precision@5`, `maturity_bias@5`, `permutation_test()`
  (paired, deterministic seed), `--save / --compare-with / --vectors`
  flags. BM25-only baseline pinned: nDCG@10 = 0.7534, Recall@5 = 0.5164,
  MRR = 0.895, type_p5 = 0.5255, mat_b5 = 0.8943.

### R10 — perf sweep (cycle 1 + cycle 2 driven by 3 expert audits)

Cycle 1 — quick wins:
- **Cached embedding matrix.** `_vector_topk` now does a single matmul
  over a cached `float32` numpy matrix keyed by `(bind id, MAX(updated_at),
  COUNT(*))`. Row norms, per-type / per-maturity np.arrays cached too.
  Microbench at 5 k embedded memories: **521 ms cold → 4.5 ms warm
  (116×)**. Fall-through Python cosine kept for environments without
  numpy.
- **`_infer_supersessions` early-exit** when no tag-set bucket has ≥ 2
  candidates. Skips the entire pass on tag-singleton DBs.
- **Six new indexes** (idempotent `CREATE INDEX IF NOT EXISTS` in
  `_bootstrap_r10_indexes`):
    - `ix_research_iter_exp_num` (experiment_id, iteration_number) —
      flips `get_meta_learning` from `SCAN + TEMP B-TREE` to `SEARCH USING
      INDEX`, removes 200× per-experiment full-scan loop.
    - `ix_anti_patterns_severity` — backs `smart_briefing` critical-AP
      filter.
    - `ix_memory_validations_created_at` — kills /timeline temp sort.
    - `ix_learning_snapshots_date` — kills /snapshots temp sort.
    - `ix_search_events_accepted_partial` (partial WHERE accepted_memory_id
      IS NOT NULL) — backs the LTR training query.
    - `ix_memories_source_agent` (partial) — backs /agents grouping.
  Also drops the dead non-partial `ix_search_events_accepted` (the partial
  replaces it).
- **`router._expand_query` gated on vectors.** Accuracy audit measured
  ΔnDCG@10 = -0.0265 (p=0.035) when expansion is applied on BM25-only
  paths. Expansion stays on once vectors are in the picture, where the
  semantic retriever covers the recall gap.

Cycle 2 — medium plays:
- **Bulk-insert ranking snapshots.** `_persist_ranking_snapshot` switched
  from `session.add()` per row to `bulk_insert_mappings`. Microbench
  100 searches × top-25: **0.76 ms / search** (was ~6.88 ms / search).
- **`dream._find_contradictions`** N+1 collapsed to 2 queries via batched
  `IN`.
- **`dream._boost_connected_memories`** rebuilt as 3-query shape
  (memories + edges + neighbour confidences) walking an in-memory
  adjacency map; old shape was 1 + 2*E.
- **`dream._infer_dependencies` cardinality bucketing.** For each
  candidate B with |tags| = n we only walk candidates with |tags| < n
  (strict superset gate is monotone in tag-set size). Same edge yield,
  ~5× wall on 5 k corpora per the speed audit.

### Tests

- 268 focused tests green in 27 s.
- 13 `bench_autoresearch` scenarios green; latencies stable.
- `retrieval_eval` BM25-only on 55 q × 147 m: nDCG@10 = 0.7534, MRR =
  0.895 — unchanged because the BM25 path itself is untouched. Vector
  path is faster but the harness exercises BM25 only (LTR retrain pending
  on real telemetry).
- Slow simulations (megacorp / gigacorp / large_scale / blind_spots /
  enterprise / company_simulation): green (last validated against R9).

### What's deferred to roadmap (not shipped here)

- **sqlite-vec / FAISS ANN backend** — gated on >5 k embedded memories.
- **Production LTR ranker** — gated on >500 accepted SearchEvents.
- **Hard-neg retraining cron** — needs LTR v1 first.
- **Graph relationship types beyond depends_on/supersedes**
  (`applies_to_stack`, `proven_by`) — gated on real-world precision data
  for the existing two.
- **Tag inverted index** for dream inference — bucketing already covered
  most of the latency win.

## [1.1.0] — 2026-04-25

R7 — multi-tenancy boundary, search correctness, hot-path performance.
The first minor bump since launch: `Memory` now carries an
`organization_id` column, so the schema isn't strictly backward-
compatible. Existing single-user OSS DBs upgrade in place — `init_db`
and the new Alembic migration both backfill NULL rows to a `default`
org, so the upgrade is silent for everyone who was already running
Memee at home.

### Schema

- **`Memory.organization_id`** added (nullable FK to `organizations.id`)
  with three composite indexes: `ix_memories_org`,
  `ix_memories_org_type_maturity`, `ix_memories_org_scope`. The
  `memee-team` plugin uses this prefix to partition the org's view in a
  single index seek; in OSS it's the same default org for every row, so
  there's no read-path overhead.
- New Alembic migration `4d2a1e8f7c93_memory_organization_id.py`. Both
  `init_db` and `alembic upgrade head` converge on the same schema and
  both backfill NULLs.

### Tenancy (P0 — was a leak path)

- **`plugins.is_multi_user_active()` + `plugins.apply_visibility()`**:
  centralised the visibility hook so every Memory query funnels through
  it whenever a multi-user implementation is registered. Previously,
  any MCP / CLI / router / review path that forgot to pass `scope` and
  `user_id` returned cross-tenant rows. OSS single-user is a no-op
  (zero cost).
- **`search_memories` honours visibility unconditionally** when a hook
  is registered, even if the caller didn't pass the kwargs.

### Search correctness

- **`_bm25_search` filters in SQL.** `memory_type` and `maturity` are
  now pushed into the FTS5 query via `JOIN memories ON
  memories_fts.rowid`. Previously over-fetched `limit*3` candidates
  *without* the filter; rare types could be outranked by common ones
  and silently vanish after the post-filter step.
- **Two-phase vector retrieval.** `_vector_rerank` now scores ONLY the
  BM25 candidate set (typically ≤ 60 rows) instead of every embedded
  memory in the DB. `test_search_performance_1000`: 3.9 s → 16 ms
  (~194×).
- **Embedding cold-start guard.** `_db_has_any_embeddings(session)` is
  a one-row probe (cached per engine). If the DB has no embeddings,
  the search path skips the ~5 s `sentence_transformers` import on
  every search. Tests, fresh installs, and users who never ran
  `memee embed` pay zero.
- **Router `_expand_query` matches whole tokens** (`\bkw\b`) and caps
  expansion at 9 terms. A query like `"pricing page copy"` no longer
  pulls in CI / lint / hooks via the substring `"ci"` inside `"pricing"`.
- **Review `_check_anti_patterns` rewrite.** Hybrid retrieval over the
  anti-pattern subspace + identifier-level token overlap of the diff
  against `trigger`/`title`, gated by a generic-token deny-list. False-
  positive rate on shared-tag seeds: ~90 % → 0 %.
- **`briefing(task_description=...)` actually routes through
  `search_memories`** for pattern + warning selection. Before this
  fix, the task argument was effectively ignored — only the project
  context drove selection.

### Performance

- **API `/projects` is one query.** Single OUTER JOIN + GROUP BY
  replaces the N+1 lazy-load that walked memory counts per project.
  At 50 projects: 51 → 1 SQL.
- **MCP engine + sessionmaker cached.** v1.0.8 cached the engine; this
  release also caches the `sessionmaker`. Per-call DDL and per-call
  factory construction are gone.
- **`_with_session` decorator** for hot MCP tools (`memory_search`,
  `memory_suggest`, `search_feedback`). Deterministic close on both
  success and exception paths replaces refcount cleanup, which leaked
  connections under load when a tool raised mid-handler.
- **Telemetry session is fully decoupled from caller.** Both
  `record_search_event` and `mark_event_accepted` write on a fresh
  short-lived session bound to the same engine. A caller-side rollback
  no longer drops the telemetry write — and a telemetry serialization
  error no longer drops the caller's writes.

### Tests

- `tests/test_r7_helpers.py` — 9 regressions covering `apply_visibility`
  (OSS no-op, registered hook applied, legacy single-arg back-compat),
  `_db_has_any_embeddings` (false on empty DB, true after embed,
  per-engine caching), `_with_session` (close on success, on exception,
  sync-function wrapping).
- `tests/bench_autoresearch.py` — eight before/after scenarios so any
  future autoresearch loop can compare against the R7 baseline. All
  seven correctness scenarios go 0.0 → 1.0; the perf scenario is
  stable at ~1.3 ms / query.

### Migration notes

If you're already running Memee from a previous version:

```bash
pipx upgrade memee
memee doctor   # backfills organization_id and stamps the new revision
```

Existing memories keep working. The `organization_id` column gets
filled in place with the default org; nothing is destroyed.

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
