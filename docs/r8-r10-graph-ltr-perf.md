# R8–R10 design notes — hybrid recall, memory graph, LTR plumbing, perf sweep

This document captures the three rounds that follow R7 (1.1.0). They live
on a single feature branch (`feat/r9-graph-ltr-mining`) and are not yet
released — the merge order, scope splits, and rollback plan all matter
because R9 in particular changes schema in non-trivial ways.

For commit-level granularity see `git log feat/r9-graph-ltr-mining`. The
short version of "what landed and why" is below.

## Why these three rounds

R7 closed a set of P0/P1 correctness bugs that surfaced from a manual
design review. The same review surfaced **architectural** items that
weren't bugs but limited the ceiling of every future improvement:

1. The vector retriever ran only over BM25 candidates → semantic-only
   matches were invisible. **R8 fix.**
2. The fallback search bypassed scoping → tenant leak path. **R8 fix.**
3. The `apply_visibility` hook contract was permissive — a misbehaving
   integrator could widen the candidate set silently. **R8 fix.**
4. There was no graph reasoning over memories → briefing surfaced
   prerequisites only by coincidence. **R9 work.**
5. There was no learned ranker, only a heuristic stack with hard-coded
   weights. The acceptance signal in `SearchEvent` was ground truth nobody
   trained on. **R9 plumbing, R10 future.**
6. The hot paths had three known performance hazards (vector cosine over
   JSON, dream inference at O(N²), N+1 endpoints we hadn't profiled).
   **R10 sweep.**

Each round was triggered by a specific design review or expert audit, not
by velocity goals. The bench harness (`tests/bench_autoresearch.py`) and
the retrieval eval (`tests/retrieval_eval.py`) gate every change.

## R8 — hybrid recall, fallback scoping, /agents N+1, rank edges

Five issues, all closed in one commit (`fix/r8-hybrid-recall-rrf`).

### Reciprocal Rank Fusion

Source: Cormack, Clarke, Buettcher (2009), *Reciprocal Rank Fusion outperforms
Condorcet and individual rank learning methods*. Standard k = 60 in the
paper; we use k = 40 because our candidate lists are short (typically ≤ 60
per retriever).

The R7 design had vector reranking BM25 candidates. R8 splits the two
retrievers as **peers**: each generates its own top-K, then we union and
score by RRF. A doc that ranks well in both gets the strongest signal; a
doc that ranks in only one still scores something. Tag overlap and
confidence become **multiplicative boosts** on the RRF score, not weights
in a linear blend.

The `bench_autoresearch::hybrid_recall` scenario constructs a memory whose
embedding is a perfect match for the query but whose title/content shares
no lexical token. Pre-R8 it was invisible (BM25 candidates didn't include
it, so the reranker never saw it). Post-R8 it's at rank 7 of 10.

### Fallback scoping

Pre-R8: when both BM25 and vector returned zero, `_fallback_search` ran
a raw LIKE without `apply_visibility`. A short or unusual query that
landed there could leak hidden rows. Post-R8: fallback resolves the
current user via the same plugin path the main search uses and routes
through `apply_visibility`. The bench scenario seeded a hidden + a
visible row, ran a query crafted to bypass FTS5 → only the visible row
returned.

### Hook compose contract

Pre-R8: a `visible_memories` hook that ignored its `base_query` argument
and returned a fresh global Memory query effectively threw away every
candidate filter the engine had already applied. Post-R8:
`apply_visibility` always intersects the hook's output with `base_query`
(`base_query.filter(Memory.id.in_(result.with_entities(Memory.id)))`).
SQLite collapses the nested `IN` into a semi-join cleanly. A bad hook
can only narrow, never widen.

### /agents N+1 collapse

50 agents, old shape was 1 (group) + 2*N (avg + type counts per agent) =
101 queries / 16 ms. Post-R8: two grouped queries (count+avg, then
agent×type) merged in Python = 2 queries / ~2 ms.

### Single-hit normalization

Min-max with one candidate gave `(s − s) / 1 = 0`. Skip min-max when
n < 3; raw cosine is already in [0, 1] because all-MiniLM-L6-v2 returns
L2-normalised vectors.

## R9 — memory graph, LTR plumbing, hard-neg mining, BEIR 55q

Three feature buckets under one PR.

### Memory graph: `depends_on` and `supersedes`

Schema change: `MemoryConnection` gets `expires_at` (nullable) for
time-bounded edges. Two new composite indexes (`(target_id,
relationship_type)` and `(source_id, relationship_type)`) — briefing
fans out from a candidate to its predecessors via `target_id`, lifecycle
scans CANON dependents via `source_id`.

Two new dream phases:

- **`_infer_dependencies`** — strict tag-superset gate. A candidate B
  whose tags are a strict superset of A's gets a `depends_on` edge to A
  (B is the more specialised pattern, A is the prerequisite). A
  textual-cue pass (`requires`, `prerequisite`, `first do`, `after
  setting`) covers cases where tags don't capture the relationship.
- **`_infer_supersessions`** — full tag-set match between A and B AND
  (textual cue OR confidence gap ≥ 0.3 + maturity ordering + invalidation
  ratio ≥ 0.2). Strict gates because a wrong supersedes edge directly
  hides the wrong memory in briefing.

Briefing surfaces 1-hop dependency predecessors (max 2 per pattern via
a single batched query) and skips supersedes-target candidates.
Lifecycle refuses to auto-deprecate memories CANON depends_on (chain
integrity); supersedes proposals go to the digest, not auto-deprecation
(thrashing protection on small corpora).

### LTR plumbing — `engine/ltr.py`

The training data was already on disk (`SearchEvent.top_memory_id` vs
`SearchEvent.accepted_memory_id`). What was missing:

1. A way to persist per-candidate **features at search time** so the
   trainer doesn't have to recompute against possibly-mutated `Memory`
   rows. Solved with `SearchRankingSnapshot` (top-25 cap per event).
2. A registry for trained models (candidate / canary / production /
   deprecated). Solved with `LTRModel`.
3. A way to A/B test rollouts safely. Solved with `MEMEE_LTR_ENABLED =
   0|1|canary` plus a stable per-query bucket (`canary_picks_ltr`).
4. The integration point in `search_memories` — rerank top-K, not
   replace the heuristic stack. The heuristic stays as candidate generator
   AND as fallback when no model is registered.

Feature shortlist (11): BM25 score + rank, vector cosine + rank, RRF
score, confidence, maturity multiplier, validation count, type encoded,
query length, has-question-mark. Optional dep: `lightgbm` (`pip install
memee[ltr]`).

### Hard-negative mining — `engine/hard_negatives.py`

`mine_hard_negatives()` returns `(rejected_top, accepted_lower)` pairs
from `SearchEvent ⨝ SearchRankingSnapshot`, with a stale-feature guard
(`Memory.updated_at > event.created_at` → drop). Export to JSONL via
the CLI: `memee ranker mine-negatives`.

### BEIR-style retrieval eval at scale

`tests/retrieval_eval.py` expanded by a worktree agent:
- 28 → 147 memories spanning 10 domains.
- 10 → 55 labeled queries with graded relevance 0–3 (179 labels total).
- New metrics: `type_match_precision@5`, `maturity_bias@5`.
- `permutation_test()` (paired, deterministic seed) for variant
  comparison.
- `--save / --compare-with / --vectors` flags.

BM25-only baseline pinned: nDCG@10 = 0.7534, Recall@5 = 0.5164, Recall@10
= 0.6115, MRR = 0.895, type_p5 = 0.5255, mat_b5 = 0.8943.

## R10 — perf sweep driven by three parallel expert audits

Each audit ran in an isolated worktree with a 1500-word report mandate
and a custom benchmark script:

- **Speed sweep** (`perf_sweep.py`) — 6 hot-path scenarios at 1k/5k/10k
  with cold + warm timing.
- **Accuracy sweep** (`accuracy_sweep.py`) — 9 ranker variants on the
  55-query harness, runtime monkeypatching, paired permutation test.
- **DB audit** (`db_audit.py`) — `EXPLAIN QUERY PLAN` for every hot
  path on a 10k-memory + 1k-event seed, plus index re-verification.

### Cycle 1 — quick wins

| change | measured impact | source |
|---|---|---|
| Cached embedding matrix (numpy float32, row norms + per-type/maturity arrays cached) | warm `_vector_topk` 521 ms → 4.5 ms (116×) at 5k embedded | speed audit |
| `_infer_supersessions` early-exit when no tag-set bucket has ≥ 2 entries | full skip on tag-singleton DBs | speed audit |
| `ix_research_iter_exp_num` (experiment_id, iteration_number) | `get_meta_learning` SCAN + TEMP B-TREE → SEARCH USING INDEX, kills 200× per-experiment loop | DB audit |
| `ix_anti_patterns_severity` | smart_briefing critical-AP filter SCAN → SEARCH | DB audit |
| `ix_memory_validations_created_at` | /timeline temp sort gone | DB audit |
| `ix_learning_snapshots_date` | /snapshots temp sort gone | DB audit |
| `ix_search_events_accepted_partial` | LTR training query: full scan → partial-index seek | DB audit |
| `ix_memories_source_agent` (partial) | /agents grouping query: SCAN → SEARCH | DB audit |
| Drop dead `ix_search_events_accepted` | -1 write overhead per event | DB audit |
| `_expand_query` gated on `_db_has_any_embeddings` | ΔnDCG@10 = -0.0265 (p=0.035) defended on BM25-only DBs | accuracy audit |

### Cycle 2 — medium plays

| change | measured impact | source |
|---|---|---|
| Bulk-insert `SearchRankingSnapshot` (`bulk_insert_mappings`) | 6.88 ms → 0.76 ms / search (top-25 cap) | speed audit + microbench |
| `dream._find_contradictions`: 2N `session.get` → 2 queries (batched IN) | 401 → 2 queries on 200 contradicts edges | DB audit |
| `dream._boost_connected_memories`: 1 + 2*E queries → 3 queries (memories + edges + neighbour confs) walking adjacency map | ~50× on 1k mems × 3k edges | speed audit |
| `dream._infer_dependencies`: cardinality bucketing (only walk smaller-tag candidates per B) | ~5× wall on 5k mems per audit; same edge yield | speed audit |

### What was rejected

- **Most ranker knobs** (title boost, intent multipliers, fallback
  floor, OR-union merge, AND→OR fan-out): all measured Δ = +0.0000 on
  the 55-query harness. The harness saturates BM25 in top-10; these
  knobs only matter once a vector retriever participates.
- **`ix_search_events_ranker`**: DB audit flagged it as a dead index
  but it's forward-looking analytics for slicing hit@k by ranker. Kept.
- **Tag-graph as third RRF retriever**: deferred until ANN backend
  lands. Marginal gain at OSS scale, full Jaccard scan dominates
  latency.

## Roadmap (not shipped in R8–R10)

| item | trigger | effort estimate |
|---|---|---|
| sqlite-vec ANN backend | corpus growth signal: ≥ 5k embedded memories | 2–3 weeks |
| Production LTR ranker train + canary | telemetry growth signal: ≥ 500 accepted `SearchEvent` rows | 1–2 weeks once trigger |
| Hard-neg retraining cron | LTR v1 in production first | 1 week |
| `applies_to_stack`, `proven_by` graph relationship types | precision data on existing depends_on/supersedes inferences (≥ 4 weeks of dream-cycle digest review) | 3–5 days |
| Tag-inverted-index for dream inference | only if measurements show > 5 s wall after R10 bucketing | 1 day |
| 50+ → 200 query harness | LTR retraining feedback loop wants larger held-out set | 1 week |

## Test surface as of this write-up

- **Focused suite** (everything except slow sims): 268 tests, ~27 s.
- **`bench_autoresearch.py`**: 13 scenarios from R7+R8; all green; BM25
  latency stable at ~1.2 ms / query at 1k memories.
- **`retrieval_eval.py`**: BM25-only baseline pinned (nDCG@10 = 0.7534).
  Will move when LTR retrain ships.
- **Slow simulations** (megacorp 100 projects, gigacorp 200 projects,
  large_scale_simulation, blind_spots, company_simulation, enterprise):
  all green, last full validation against R9 commit.

## Operational notes

- The `expires_at` and `ranker_version` / `ranker_model_id` columns are
  added in-place via `_bootstrap_*` functions in `init_db`. No Alembic
  migration is strictly required — but one will be cut alongside the
  release tag for users who run `alembic upgrade head` directly.
- `pip install memee[ltr]` is the optional dep that pulls in `lightgbm`.
  Without it, `memee ranker train` exits cleanly with a hint;
  `search_memories` runs the heuristic ranker.
- The cached embedding matrix lives in process memory keyed by engine
  identity. Across test runs that share an engine (none in our suite,
  but possible in a long-lived FastAPI worker), revisions roll forward
  on `MAX(updated_at)` of embedded rows; no manual invalidation needed.
- All performance gains in R10 are independent — turning any one off
  doesn't compromise correctness, only the relevant microbench.
