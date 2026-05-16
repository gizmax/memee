# Memee 2026 — Research-grounded improvement roadmap

**Status:** synthesis of 5 parallel deep-research dossiers (math/statistics,
AI/ML SOTA 2024-2026, cognitive science + classical IR, production
engineering, internal code review) executed 2026-05-15 against Memee
v2.3.2 (commit 040345f).

The point of this document is not to ship every recommendation. It is to
record what serious sources converge on so the next 3–6 release cycles
work from cross-validated signal instead of hunch.

## Method

Five independent agents researched, each with a fixed budget and a
strict output format. Cross-validation = a recommendation surfaced by
≥2 dossiers from disjoint angles (e.g. a math/stats paper and a
production blog independently endorsing the same change). 14
recommendations cleared that bar. Each is tagged below with the
dossiers that endorsed it.

Scale bench (measured against current HEAD, 2026-05-15):

| Memories | DB | Insert/s | Search p50 | Brief 500 p50 | Brief 200 p50 (hook) |
|---:|---:|---:|---:|---:|---:|
| 100 | <1 MB | 6 196 | 17 ms | 34 ms | 34 ms |
| 1 000 | <1 MB | 13 275 | 30 ms | 35 ms | 35 ms |
| 5 000 | <1 MB | 10 346 | 32 ms | 46 ms | 41 ms |
| 10 000 | 6.6 MB | 10 197 | 33 ms | 56 ms | 57 ms |

Hook latency holds well under 100 ms even at 10 k. Agents flag 50 k–100 k
as the next bottleneck.

## Cross-validated recommendations (ranked)

Each: title • dossiers that endorsed • current state • change • impact •
LOC estimate.

### Tier 0 — bug/drift fixes (ship next, ≤1 day)

**0.1 Dream-cycle atomicity bug** — endorsed by code-review.
`engine/quality_gate.py:585` calls `session.commit()` inside
`merge_duplicate()`. Dream's `_semantic_dedup_pass` calls this in a
loop *inside* the `BEGIN EXCLUSIVE` block that `run_dream_cycle` opens.
Net effect: partial commits land mid-cycle, contradicting the
"one transaction per cycle, 1.21× faster" promise documented at
`engine/dream.py:128`. **Fix:** thread a `commit=True` flag through
`merge_duplicate`, default True for backwards compat, dream passes
False. ~10 LOC. **Severity: high.**

**0.2 `is_authoritative` partial index missing from models** — endorsed
by code-review. `storage/database.py:172` adds the partial index on
upgrade, but `storage/models.py` `__table_args__` doesn't declare it.
Fresh DBs from `Base.metadata.create_all()` ship without the index;
Layer 0.5 query becomes a full table scan. ~3 LOC.

**0.3 Layer 0.5 tag-overlap pushdown** — endorsed by code-review,
production. `engine/router.py:258-289` loads every authoritative
memory with `.all()` and filters tag overlap in Python. At 10 k
authoritative ≈ 500 ms; the agents project 5 s at 100 k. **Fix:** push
the overlap into SQL via `MemoryTag` join + `IN (scope_tags)`. ~25 LOC.

**0.4 CLAUDE.md drift** — endorsed by code-review.
Claimed 63 files / 18 899 LOC → actual 70 / 23 352. "16 engine modules"
→ actual 25. "~500 tests" → actual 546. OrgMemEval headline updated to
95.3% in v2.3.2 but engine module count, file count, and feature list
still stale. ~30 lines of text.

### Tier 1 — high-value patches (1 session each, ≤200 LOC)

**1.1 RRF unification on the BM25-only path** — endorsed by
math/stats (#4), code-review (#3 algorithmic), cog sci (§6).
Memee already uses RRF on the hybrid (BM25 + vector) path, but the
BM25-only branch (`engine/search.py:460-465`) still uses linear blend
(`W_BM25 + W_TAGS + W_CONFIDENCE`). The known failure mode is scale
mismatch (BM25 0–15 vs cosine 0–1). Cormack et al. SIGIR 2009 + Bruch
et al. TOIS 2023 both endorse RRF when scales drift across query
types. ~30 LOC.

**1.2 Beta-Binomial confidence model with credible intervals** —
endorsed by math/stats (#1, #3, #5), cog sci (§8 D-S analogy),
code-review (arch #1). Replace
`conf = conf + w·(1-conf)` and `conf -= 0.12·conf` with
`α += w_validation`, `β += w_invalidation`. Posterior mean = α/(α+β);
95% HDI from `scipy.stats.beta.ppf`. Source multipliers (human 1.2,
llm 0.8) become *prior strengths*, not score nudges. Foundation for
SPRT (1.3) and Thompson sampling (3.x). ~80 LOC + a one-liner backcompat
mapper. **References:** Bayes Rules! ch. 3; Paun et al. 2018 (Bayesian
hierarchical D-S).

**1.3 SPRT for canon promotion + deprecation** — endorsed by
math/stats (#6). Replace hand-tuned thresholds
(`validation_count ≥ 10`, `invalidation_ratio > 60%`) with Wald's
SPRT. Each validation = `log(p₁/p₀)`, invalidation =
`log((1-p₁)/(1-p₀))`. Boundaries `log(19) ≈ 2.94` /
`-log(19) ≈ -2.94` for α = β = 0.05. Gives a calibratable Type-I/II
dial. ~40 LOC. Builds on 1.2.

**1.4 Rocchio pseudo-relevance feedback + BM25+ floor** — endorsed by
cog sci (§6, #2 top pick). On every search, take top-3 results,
average their embeddings, re-search once with `q' = 0.7·q + 0.3·centroid`.
BM25 → BM25+ via one-line `δ = 1.0` in FTS5. Both are textbook IR
moves with well-documented gains on short corpora. ~50 LOC.

**1.5 ACT-R / FSRS-light per-memory decay** — endorsed by math/stats
(#2), cog sci (#2, #3). Replace 60-day archive cliff in
`engine/lifecycle.py` with retrievability
`R(t) = 2^(-Δt/h)` where `h` (half-life) re-stabilises on every
successful retrieval:
`h ← h · (1 + γ·(1-R))` (FSRS-style). Canon-tier memories skip
decay entirely (Bahrick 1984 permastore). ~120 LOC + 2 new columns
(`half_life`, `last_retrieved`). **References:** Settles & Meeder ACL
2016 HLR; FSRS bench at open-spaced-repetition.

**1.6 Active re-validation surface (FSRS scheduler in dream)** —
endorsed by cog sci (#3, #1 top pick). Dream pass selects N canon
memories whose predicted retrievability drops below 0.85 and injects
them into the next briefing as "verify" candidates. Agent application
= pass; explicit invalidation = fail; ignore-3-times = soft fail.
*"Canon is currently unfalsifiable"* — the cog-sci dossier flags this
as the single biggest honesty win. ~250 LOC. Pairs with 1.5.

**1.7 Beta calibration of confidence outputs** — endorsed by
math/stats (#3), cog sci (§7). Nightly: fit beta calibration on
(predicted_conf, was_validated) pairs in `engine/calibration.py`.
Map raw → calibrated at read time. Adaptive (equal-mass) ECE binning
in tests. Kull et al. AISTATS 2017 beat Platt on small-n binary data
(Memee's regime). ~60 LOC.

### Tier 2 — strategic moves (≥1 week each)

**2.1 sqlite-vec migration (BLOB float32 + KNN)** — endorsed by
production (#1 top pick), code-review (perf #7, #10). Embeddings live
in a JSON column; cosine runs in Python. Migrate to `sqlite-vec`
BLOB + KNN MATCH syntax. 5–10× speed-up at 10 k–100 k. Preserves
one-file deploy. ~200 LOC + Alembic migration + `memee embed` rewrite.

**2.2 HippoRAG2 Personalized PageRank over the existing graph** —
endorsed by AI/ML (#2 top pick), code-review (arch #3). Memee already
has the graph (`MemoryConnection` with `depends_on`, `supersedes`,
`contradicts`, `related_to`, `supports`) but `search_memories` never
walks it. Add PPR seeded by BM25+vector top-k via `networkx.pagerank(personalization=…)`. Paper:
arxiv.org/abs/2502.14802 (Feb 2025). +5–10 pp hit@3 on multi-hop /
associative queries. ~200 LOC.

**2.3 Embedding upgrade — bge-m3 or nomic-embed-v2** — endorsed by
AI/ML (#3 top pick), production (#5 top pick). all-MiniLM-L6-v2
(2021, 22 M params, MTEB ~41) → bge-m3 (2024, 568 M, MTEB ~58, ships
dense + sparse + ColBERT from one forward pass). +17 pp MTEB
retrieval. Matryoshka means we can store 384-dim slices (same disk)
and re-embed at 1024 only on top results. ~100 LOC + re-embed
(~10 min on current corpus). **Decision needed:** OSS default
upgrade or paid-tier-only?

**2.4 Relevance feedback loop closure** — endorsed by production
(#2 top pick), code-review (arch #2). `SearchEvent.accepted_memory_id`
is already recorded; `hard_negatives.py` exists; `LTRModel` registry
exists with candidate / canary / production states. **Missing the
glue:** scheduled retraining trigger, automated canary promotion when
nDCG@10 lifts ≥0.01 at p < 0.05. The wiring is 80% done. ~150 LOC.

**2.5 Mem0-style write-time UPDATE/DELETE pass** — endorsed by AI/ML
(#1 top pick). At `memee record` time, run an LLM pass over the top-k
similar existing memories and emit explicit ADD / UPDATE / DELETE /
NOOP operations. Closes the staleness gap that 0.85-similarity dedup
leaves open. Paper: arxiv.org/abs/2504.19413 (Apr 2025).
**Caveat:** adds an LLM call per write — gate behind opt-in flag, not
default. ~250 LOC.

**2.6 Bi-temporal edges (`valid_from` / `valid_to`)** — endorsed by
AI/ML (#4). Graphiti-style temporal model on `MemoryConnection` rows.
Lets "we used Postgres" be true in 2024 and false in 2026 without
deleting history. ~150 LOC + Alembic migration. Paper:
arxiv.org/abs/2501.13956 (Jan 2025).

**2.7 Cross-model 2-of-N agreement gated by `--verify`** — endorsed by
AI/ML (#5). Optional second cheap model votes on a proposed memory
before persistence; resolves the "Mem0 #4573 — 97.8% junk" failure
mode at the source. ~150 LOC. Paper anchors: CREAM-RAG (openreview
56DSmK9GnS), Reflexion / MAR.

### Tier 3 — architectural projects (week+ scope)

**3.1 Dawid-Skene reliability matrix for per-model validation** —
endorsed by cog sci (#8, #4 top pick), math/stats (#1). Replace the
fixed cross-model multiplier (×1.95) with a learned 2×2 per
model-family confusion matrix, fit weekly via 20-line numpy EM. Adds
~5 min/week to dream cycle. **Reference:** Dawid & Skene 1979, JRSS C.

**3.2 Synaptic homeostasis + replay in dream** — endorsed by cog sci
(§5). Phase X: replay last 200 retrieval queries from
`telemetry.py` against the current index, compute nDCG vs the version
served; surface drift > 0.1. Phase Y: multiply all activation scores
by 0.97 nightly (Tononi & Cirelli 2014 SHY). Stops absolute weights
drifting unbounded. ~150 LOC.

**3.3 Thompson Sampling for memory selection under budget** —
endorsed by math/stats (#5). Treat memory inclusion in briefing as
contextual bandit. Per-memory Beta(α, β) from 1.2. Rank by
`θ ~ Beta(α, β) × relevance`. Falls out for free once 1.2 lands.
**Reference:** Agrawal & Goyal ICML 2013.

**3.4 ADWIN drift detection on per-memory validation streams** —
endorsed by math/stats (#7). Replace
`invalidation_ratio > 60%` with adaptive sliding window. Bifet &
Gavaldà SDM 2007. Detects *recent* drift on long-canon memories that
the ratio rule misses. ~100 LOC, or one `river` dependency.

**3.5 Episodic buffer with consolidation gate** — endorsed by cog sci
(#1). New `EpisodicBuffer` table; session-scoped writes land there
first, promote to durable `memories` only after threshold (2 sessions
× 1 quality gate). Atkinson-Shiffrin 1968 modal model. ~150 LOC.

## Topics endorsed by zero dossiers (consciously parked)

- Graph rewrite to property-graph DB. Memee's edge table works.
- Embedding fine-tuning. Frozen pretrained beats fine-tune at this scale.
- Federation across orgs. Out of scope until Enterprise tier exists.
- LLM-fabricated importance-scoring at write time. Worth revisiting in
  v2.5+.

## Recommended next 3 ship windows

**v2.3.3** (this session if budget permits) — Tier 0 fixes: 0.1, 0.2,
0.3, 0.4. ~50 LOC, demonstrable correctness + perf win.

**v2.4.0** (1–2 sessions) — Tier 1.1 (RRF), 1.2 (Beta-Binomial),
1.3 (SPRT), 1.4 (Rocchio + BM25+). ~200 LOC. Compounds into a
calibrated, principled ranking + lifecycle. Builds the foundation
for Tier 3.

**v2.5.0** (1–2 weeks) — Tier 2.1 (sqlite-vec), 2.4 (LTR retraining
loop), 2.2 (PPR retrieval). Performance and quality at scale.

## Sources

Five dossier transcripts saved as separate task outputs (not in repo).
External citations consolidated:

**Math / statistics**
- Bayes Rules! ch. 3 (Beta-Binomial)
- Paun et al. 2018 (Bayesian D-S)
- Wixted & Ebbesen 1997 (power-law forgetting)
- Settles & Meeder ACL 2016 (HLR)
- Kull et al. AISTATS 2017 (Beta calibration)
- Cormack et al. SIGIR 2009 (RRF)
- Bruch et al. TOIS 2023 (fusion functions)
- Agrawal & Goyal ICML 2013 (Thompson Sampling)
- Wald SPRT
- Bifet & Gavaldà SDM 2007 (ADWIN)

**AI/ML 2024–2026**
- arxiv.org/abs/2502.14802 (HippoRAG 2)
- arxiv.org/abs/2502.12110 (A-MEM)
- arxiv.org/abs/2504.19413 (Mem0 / Mem0g)
- arxiv.org/abs/2501.13956 (Zep / Graphiti)
- arxiv.org/abs/2505.22101 (MemOS)
- arxiv.org/abs/2310.08560 (MemGPT)
- arxiv.org/abs/2303.11366 (Reflexion)
- arxiv.org/abs/2411.03538 (Long Context RAG)
- HuggingFace MTEB leaderboard

**Cognitive science / classical IR**
- Atkinson & Shiffrin 1968 (modal model)
- Bahrick 1984 (permastore)
- Anderson 1990s (ACT-R activation)
- FSRS (Ye et al. KDD 2022, Anki 23.10)
- Stickgold & Walker 2005 (sleep consolidation)
- Tononi & Cirelli 2014 (SHY)
- Rocchio 1971 (PRF)
- Lv & Zhai SIGIR 2011 (BM25+)
- Formal et al. SIGIR 2022 (SPLADE)
- Santhanam et al. NAACL 2022 (ColBERT v2)
- Brier 1950 / Tetlock 2015 (superforecasting)
- Dawid & Skene 1979

**Production engineering**
- cursor.com/blog/secure-codebase-indexing
- turbopuffer.com/blog/turbopuffer
- github.blog Copilot embedding model
- atlassian.com Rovo search architecture
- alexgarcia.xyz sqlite-vec
- litestream.io
- github.com/vlcn-io/cr-sqlite

**Internal**
- Memee source HEAD at commit 040345f
- Scale bench captured 2026-05-15
- OrgMemEval 95.3% (v2.3.2 calibration)

## Honest disclosure

- All "expected impact" numbers in this doc are derived from
  paper-reported deltas on different benchmarks, not validated against
  Memee's actual corpus. Validate with an autoresearch run before
  committing engineering time on the larger items.
- The cog-sci dossier proposed an `EpisodicBuffer` table; this overlaps
  with Memee's `MemoryUsage` table but is not identical. Worth a closer
  look before building.
- The math dossier's SPRT recommendation assumes IID Bernoulli
  validations. Reality has clustering (one project keeps validating).
  Hierarchical version is harder and not on the recommended list.
- HippoRAG 2 (2.2) is the most theoretically promising win in the
  whole list but also the riskiest to integrate — measure with an A/B
  before promoting to default.
