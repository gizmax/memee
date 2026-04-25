# Memee roadmap — shipped, deferred, gamechangers

This document is the canonical record of every audit-driven change since
v1.1.0: what shipped, what's gated to a future trigger and why, and which
deferred items I think have **gamechanger** potential — single bets that
would visibly reshape the product, not incremental percentage gains.

For commit-level granularity see `git log v1.1.0..main`. For the full
design write-up of each round see:

- `CHANGELOG.md` — the per-round summary that ships in the package
- `docs/r8-r10-graph-ltr-perf.md` — design rationale for R8-R10
- `docs/r12-eval-expansion.md` — 207-query × 255-memory harness story
- `docs/r14-cross-encoder-rerank.md`, `docs/r14-severity-intent-boost.md`,
  `docs/r14-maturity-gated-expansion.md`, `docs/r14-field-aware-bm25.md`
  — R14 four-way audit reports

---

## Retrieval delta vs v1.1.0 (207-query × 255-memory harness)

|   | nDCG@10 | Recall@5 | Recall@10 | MRR |
|---|---:|---:|---:|---:|
| v1.1.0 (R7 ship)                | 0.7110 | 0.5589 | 0.6065 | 0.8213 |
| HEAD, BM25-only default-on path | 0.7273 | 0.5701 | 0.6292 | 0.8277 |
| HEAD with `MEMEE_RERANK_MODEL`  | **0.7628** | **0.5950** | **0.6477** | **0.8676** |

R7 → HEAD default: +1.63 nDCG points (porter tokenizer + RRF refinements
+ tag-graph third retriever + project-aware boost).
R7 → HEAD + cross-encoder rerank: **+5.18 nDCG points** at p = 0.0002.

---

## Shipped since v1.1.0

| Round | Item | Default | Status |
|---|---|---|---|
| R8 | RRF (BM25 ∪ vector), fallback scoping fix, hook compose contract, /agents N+1 | on | core |
| R9 | Memory graph (depends_on / supersedes), LTR plumbing, hard-neg mining, BEIR 55q harness | on | core |
| R10 | Embedding-matrix cache (116×), 6 indexes, dream early-exits, expansion gate | on | core |
| R11 | porter unicode61 tokenizer (+0.0317 nDCG), WAL synchronous=NORMAL, BEGIN EXCLUSIVE on dream, MinHash dedup (~6.7×) | on | core |
| R12 P0 | Truth alignment (benchmark drift, hot-path count, hybrid label, [ltr] extra, lint) | on | bugfixes |
| R12 P1 | 207q × 255m eval harness; confidence calibration substrate (Brier/ECE/MCE, isotonic, Beta-Binomial) | on (substrate); gate `MEMEE_CALIBRATED_CONFIDENCE=1` | substrate |
| R13 | Project-aware reranking, tag-graph as 3rd RRF retriever, propagation perf | on | core |
| R14 #2 | Cross-encoder reranker (stage 5a) | **off** — opt-in `MEMEE_RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2` + `pip install memee[rerank]` | accuracy |
| R14 #3 | Severity-weighted intent boost (substrate) | off — opt-in `MEMEE_SEVERITY_INTENT_BOOST=1` | substrate |
| R14 #4 | Maturity-gated query expansion (substrate) | off — opt-in `MEMEE_MATURITY_GATED_EXPANSION=1` | substrate |
| R14 #1 | Field-aware BM25 column weights | — | **honest negative** (every tuple regressed lexical_gap_hard) |

---

## Deferred — P1 perf

Ordered by impact / effort.

| Item | Impact | Effort | Gating signal | Evidence |
|---|---|---|---|---|
| **sqlite-vec / pgvector ANN backend** | 10-180× vector search at 50k embedded; trades 3-7 % recall for sub-ms latency | 2 weeks | ≥ 5k embedded memories in any production install | R10 speed audit; ann-benchmarks.com |
| **Hybrid candidate union > limit × 3** | +1-2 nDCG@10 (more candidates per retriever before RRF); cost is downstream rerank work | 1 day | none — ready | RRF design (Cormack 2009 recommends k up to 1000) |
| **Async telemetry queue + sampling** | 0.76 ms / search → ≤ 0.1 ms; write batched | 3-5 days | ≥ 100 RPS sustained search OR OTel customer ask | R11 concurrency audit |
| **OpenTelemetry GenAI conventions** | Drop-in dashboards for Prometheus / Grafana / Honeycomb | 1 week | first OTel-collector customer | OTel GenAI WG conventions stable as of 2026-Q1 |
| **Tier hot/warm/cold migration** | 2-5× memory footprint reduction at 100k+ | 2-3 weeks | ≥ 50k memories in any install | R10 architect design |
| **EXCLUSIVE on dream subphases** | Another 5-10 % on dream wall (cycle-level already shipped) | 1 day | dream wall > 1 min on smallest tier | R11 concurrency audit |

## Deferred — P1 accuracy

| Item | Impact | Effort | Gating signal | Evidence |
|---|---|---|---|---|
| **Counterfactual / shadow ranker logging** | Unblocks LTR training without label censoring (today "rows above accepted" are coded as positive; really, agent never saw them) | 1 week | LTR rollout starts (≥ 500 accepted SearchEvents) | R9 LTR roadmap, R12 P1 algo audit |
| **LTR ranker training + canary** | +5-10 % hit@1 once enough acceptance data; replaces rotting heuristic constants (W_BM25=0.42, INTENT_BOOSTS multipliers) | 1-2 weeks | ≥ 500 accepted SearchEvents | R9 plumbing already shipped |
| **Hard-negative mining cron** | LTR retraining stable across drift | 1 week | LTR v1 in production first | R9 plumbing shipped (`memee ranker mine-negatives`) |
| **Query-side lexical synonyms via porter** | Closes some `paraphrastic` cluster gap (n=43, BM25-only nDCG = 0.6795); cross-encoder already partly covers it | 3-4 days | none — ready | R12 P1 expansion shows the cluster baseline |

## Deferred — P1 quality / governance

| Item | Impact | Effort | Gating signal | Evidence |
|---|---|---|---|---|
| **Structured memory schema (claim / when / why / evidence / counter / scope / expiry)** | +5-10 % retrieval precision on `anti_pattern_intent` (parses structure into separate FTS columns); unblocks evidence graph below | 2-3 weeks | first customer asks "show me the evidence" | Audit explicitly flagged WHY/WHEN heuristics as fragile |
| **PII / secret redaction before embedding** | Unlocks regulated-industry adoption (HIPAA, GDPR DPA) | 1 week | first regulated prospect | R10 architect design |
| **Per-scope retention + evidence-chain pruning** | -10-30 % storage at 100k+; compliance footing | 1 week | same as above | same |

---

## ★ Gamechangers — bold bets worth a design pass

These six items, if done well, would each be a step-change for Memee, not
an incremental percentage gain. They're not on a release schedule because
each needs its own design round before estimation. Listed by my honest
read of *which one would change the most about the product*.

**For the in-depth analysis of every gamechanger** — what each one does,
how the product changes, what users gain, what we'd lose, sequencing —
see [`docs/gamechangers.md`](gamechangers.md). The summaries below are
the headline pitch only.

### 1. ★★★ Cross-encoder rerank — flipping default-on with the model bundled

**Status:** R14 #2 already shipped, opt-in only. Measured Δ = +0.0355
nDCG@10 (p = 0.0002), and on weak clusters: onboarding +0.1124 (p=0.03),
diff_review +0.0636 (p=0.03), paraphrastic +0.0298. Latency p50 1.3 → 41
ms — under the 50 ms agent-tolerable budget.

**Why it's a gamechanger:** the difference between an opt-in feature and
a default-on feature is the difference between Memee's marketing claim
and Memee's measured behaviour. Right now the README has to qualify *"if
you opt in, +5 nDCG points"*. Default-on flips that to *"out of the box,
search ranks like a paid IR product"*.

**What it would take:** ship the `cross-encoder/ms-marco-MiniLM-L-6-v2`
model in the `[rerank]` extra, default-on when the optional dep is
installed, lazy-load on first search, document the latency cost
prominently. Estimated 1 week.

**Why it isn't already done:** the dep is ~250 MB. Same trade as
`[vectors]` was at v1.1.0 — and we made that one optional too. A "fast"
default + "smart" optional is a defensible product choice. But the
measured uplift on weak clusters is large enough that *not* shipping
default-on starts to look like leaving the lift on the table.

### 2. ★★★ Evidence graph as canon ledger

**Status:** plumbing shipped (R9 — `MemoryConnection` schema, dream
inference of `depends_on` and `supersedes` with strict gates, briefing
prepends predecessors, lifecycle gates deprecation when CANON depends).
The graph **exists**; what's missing is reading it as a canon ledger.

**Why it's a gamechanger:** today canon is a flat collection of high-
confidence Memory rows. With the ledger, canon becomes a *graph state* —
"the current set of memories that no other CANON memory has marked as
contradicted or superseded, with all their evidence chains and a timestamped
provenance trail." That's a paradigm shift from "search a memory store"
to "query a knowledge base" — different product narrative, different
buyer (compliance, audit, legal teams care about provenance).

**What it would take:** a CLI / API surface for graph queries (`memee
why <memory_id>`, `memee timeline <project>`, `memee canon-state`); a
dashboard view that renders the connection graph; a rule engine that
fails closed when a CANON memory is marked `contradicts` by another
CANON memory. Estimated 3-4 weeks.

**Why it isn't already done:** the schema landed only in R9. Two cycles
of dream output need to be analysed before we trust the inferences
enough to expose them as a primary surface. Once the precision data
backs us, this becomes the next major feature.

### 3. ★★ LTR + counterfactual logging — closing the learning loop

**Status:** R9 shipped the plumbing (SearchRankingSnapshot, LTRModel
registry, MEMEE_LTR_ENABLED canary flag, `memee ranker
train/promote/mine-negatives`). The training code path runs end-to-end;
what's missing is the data volume.

**Why it's a gamechanger:** LTR is what differentiates a memory product
from a search library. Today every ranker constant
(W_BM25=0.42, INTENT_BOOSTS={"test": 1.1, ...}, RRF_K=40) is a number we
guessed. With LTR, those constants get replaced by a model trained on
*your team's* acceptance data — and the model improves nightly. That's
the loop competitors (Mem0, Zep, Letta) don't have because they don't
log per-search feature snapshots.

**What it would take:** counterfactual / shadow-ranker logging in
SearchEvent so every "rows above accepted" label gets a confidence
score (today they're naively positive); a nightly cron that calls
`memee ranker train` and emits a candidate model; a per-customer
canary (10 % of searches go to candidate); a regression gate via
`retrieval_eval` before promoting. Estimated 1-2 weeks once we have
the SearchEvent volume.

**Why it isn't already done:** gated on ≥ 500 accepted SearchEvents in
production. Memee at launch is at <100 / week. Realistic timeline:
8-12 weeks of organic customer traffic OR seeded by demo data which
defeats the purpose.

### 4. ★★ Neuro-symbolic review — tree-sitter / AST + memory

**Status:** entirely unbuilt. Today `review.review_diff` extracts
keywords with regex and runs them through `search_memories` over the
anti-pattern subspace.

**Why it's a gamechanger:** the `diff_review` cluster on the 207q
harness has BM25-only baseline 0.5557 — the *worst* cluster. Cross-
encoder lifts it to 0.6192. AST-aware review would push it past 0.85
because the signal stops being "diff contains the token timeout" and
becomes "diff added `requests.get(url)` without a `timeout=` kwarg
on line 47, and Memee canon `pat-http-timeout` (confidence 0.94, 8
projects validated) says always-set-timeout for outbound HTTP."

The competitive landscape: CodeRabbit / Greptile / Codeium have AST,
but none of them combine it with a *cross-project shared memory*.
That's the unfair advantage — every Memee install accumulates exactly
the constraints the AST review needs.

**What it would take:** add `tree-sitter` (Python bindings + compiled
language packs are now stable) as an optional dep, parse changed hunks
into AST diffs, extract identifier-with-args triples (e.g.
`requests.get(...)`), match those against `AntiPattern.trigger` /
`AntiPattern.detection` columns, fuse with the existing semantic
match. Estimated 2-3 weeks for a multi-language v1.

**Why it isn't already done:** review.py was the lowest-priority
module before R14 measured diff_review as the worst cluster. It's
*now* the obvious place to push.

### 5. ★ Expected-value router — token-cost aware briefing

**Status:** unbuilt. Today `briefing()` returns top-N by RRF score
within a static token budget; it doesn't know that some memories are
worth more tokens than others.

**Why it's a gamechanger:** token cost is customer's #1 sticker shock.
Today's narrative is "save tokens by routing." Tomorrow's narrative
becomes "rank by P(relevance) × impact − token_cost — show only what
beats the budget on expected value."

The structural advantage: every Bayesian decision-theory IR system
proposes this; nobody ships it because they don't have an `impact`
signal. Memee does — `application_count` (how many times it was used),
`mistakes_avoided` (impact tracker on outcome_evidence_type),
`validation_count` (how strong the signal). The math is straightforward
once those are wired into the router.

**What it would take:** define `impact(memory)` as a calibrated function
of (application_count, mistakes_avoided, project_count); define
`P(relevance | query)` as the calibrated cross-encoder score (R12 P1
calibration substrate is already in place); replace `briefing()` rank
with `score = P × impact − tokens × token_price`. Estimated 1-2 weeks.

**Why it isn't already done:** needs production telemetry to calibrate
P(relevance) and impact. Same gating signal as LTR — but unlike LTR,
the rule isn't "we need 500 events," it's "we need outcome_evidence_type
populated on enough ProjectMemory rows." That's a slower roll because
agents have to opt in to feedback.

### 6. ★ Privacy-first embeddings + per-scope encryption

**Status:** unbuilt. Today embeddings use the local
`all-MiniLM-L6-v2` model (so PII never leaves the install — partial
credit), but evidence chains, source URLs and raw memory content
all sit unredacted on disk.

**Why it's a gamechanger:** every regulated-industry prospect we
talk to (healthcare, finance, legal) asks the same three questions:
who can see what; how long is it kept; what happens if I delete a
project. We answer all three approximately today. With privacy-first
embeddings + scope-aware encryption + retention windows wired into
evidence_chain, we answer them precisely. That converts a "no" into
a "yes, here's our DPA."

**What it would take:** PII / secret regex scanners before
`memee record`; per-org encryption key derived from a master in
`~/.memee/keys/` (OSS uses local key, memee-team uses HSM); retention
policy on `evidence_chain` and `ProjectMemory.outcome_evidence_ref`;
`memee privacy report` CLI. Estimated 2 weeks for the scanner +
encryption layer; 1 more week for retention.

**Why it isn't already done:** no regulated customer signed yet. But
this is the *enabler* for that conversation; we're inverted on the
"build it then they'll come" / "wait for the ask" axis.

---

## Honest non-gamechangers (don't let them eat the calendar)

A few items in the deferred lists *sound* impressive but on
measurement they're incremental:

- **`ix_search_events_ranker` index drop** (R10 DB audit flagged as
  dead). Saves 1 row per write at p99. Don't bother.
- **EXCLUSIVE on dream subphases.** Cycle-level already at +20 %; the
  subphase variant is +5-10 % more, with extra lock-contention risk on
  multi-process installs. Not worth.
- **Hybrid candidate union > limit × 3.** R14 sweep already showed BM25
  reranking is at ceiling; adding more candidates won't move the
  needle until cross-encoder is default-on (gamechanger #1).
- **Tier hot/warm/cold migration.** 100k+ scale is a real but
  rare-today problem; structural answer is "memee-team Postgres" not
  a tier juggle inside SQLite.

The rule: an item belongs in this list if its impact / effort ratio is
worse than 1 nDCG point per engineer-week or 50 ms wall-clock saved
per engineer-week. We will rebuild this list quarterly as priorities
shift.

---

## What this document is not

- A release plan. Items are listed by impact / effort, not by chronology.
- A commitment. Each gamechanger still needs its own design pass
  before estimation hardens.
- A complete commit log — see `CHANGELOG.md` and `git log`.

Last updated: 2026-04-25.
