# R14 — Cross-encoder reranker

## TL;DR

A cross-encoder reranker is now wired as **stage 5** of `search_memories`,
between RRF candidate generation and the optional LTR rerank. Default OFF;
opt in by setting `MEMEE_RERANK_MODEL=ms-marco-MiniLM-L-6-v2`. On the 207-query
× 255-memory eval harness it lifts macro nDCG@10 by **+0.0355 at p=0.0002**
and the `paraphrastic` cluster (the headroom cluster the audit predicted)
by **+0.0298 at p=0.08**. Latency cost: **p50 41 ms, p95 78 ms** vs ~1–2 ms
without rerank.

## Why rerank, why now

The R12 retrieval eval expansion (255-memory corpus, seven difficulty
clusters) put numbers on what we already suspected: BM25 ∪ vector RRF wins
big on `code_specific` (rare identifiers, BM25-friendly) but plateaus on the
clusters where the query and the gold doc don't share tokens:

| cluster              | n  | BM25-only nDCG@10 |
|----------------------|----|-------------------|
| `paraphrastic`       | 43 | 0.6795            |
| `lexical_gap_hard`   | 15 | 0.7446            |
| `diff_review`        | 30 | 0.5557            |
| `onboarding_to_stack`| 25 | 0.6605            |

These are the queries where the bi-encoder vector model also under-recalls,
because cosine similarity over independently-encoded query and document
loses the cross-attention signal that resolves paraphrases. A **cross-
encoder** sees both texts in one forward pass and is the textbook tool for
this exact failure mode. The R12 audit roadmap predicted +3-7 nDCG@10 on
those clusters specifically.

## Mechanism

A new module `src/memee/engine/reranker.py` defines `CrossEncoderReranker`
with three public methods:

* `is_enabled()` — environment + import gate.
* `rerank(query, candidates, top_k=30)` — reorder the top-K of the candidate
  list by cross-encoder relevance score; below-K candidates keep their RRF
  order.
* `cache_state()` — diagnostics for the `memee ranker rerank-status` CLI.

Stage 5 of `search_memories` runs the reranker before the LTR rerank block,
so when both are configured LTR sees the cross-encoder's higher-quality
candidate ordering as input.

```python
# stage 5a — cross-encoder
rr = CrossEncoderReranker()
if rr.is_enabled():
    results = rr.rerank(query, results)

# stage 5b — LTR (existing)
final = _ltr_rerank_if_active(...)
```

The reranker scores `(query, memory.title + " " + memory.content[:200])`
pairs. Tags aren't included on the document side — the cross-encoder is good
at semantic matching and tag noise tends to dilute scores; the heuristic
stack already weights tag overlap as a separate signal in the post-RRF
boost.

The model is held in a process-level cache behind a `threading.Lock`. The
first search after process start pays the ~30 s cold load (or ~1-2 s if
the HF cache is warm); subsequent searches pay only the per-pair forward
pass.

### Fail-safe

If the model can't be loaded (no internet + no HF cache, or
`sentence-transformers` not installed), `_LOAD_FAILED` is set and every
subsequent `is_enabled()` returns `False`. Search continues to operate on
RRF + heuristic stack alone — no exceptions, no half-broken state. The
A/B harness honours the same contract: it returns `model_unavailable` and
exits cleanly rather than reporting fake numbers.

## Configuration

| Env var               | Default | Effect |
|-----------------------|---------|--------|
| `MEMEE_RERANK_MODEL`  | unset   | Rerank OFF. Set to `ms-marco-MiniLM-L-6-v2` (or any HF cross-encoder id / local path) to enable. |
| `MEMEE_RERANK_TOP_K`  | `30`    | Number of top RRF candidates to score with the cross-encoder. Linear in latency. |

The shorthand `ms-marco-MiniLM-L-6-v2` expands to
`cross-encoder/ms-marco-MiniLM-L-6-v2` (the canonical lightweight cross-
encoder: 22M params, ~80 MB, ~2-5 ms / pair on CPU).

To install:

```bash
pip install memee[rerank]                 # explicit extra
# or
pip install memee[vectors]                # already includes sentence-transformers
```

The extra is sentence-transformers (already a `[vectors]` dep). We expose
`[rerank]` separately for installs that opt into rerank but not bi-encoder
vectors.

## CLI

```bash
$ memee ranker rerank-status
Cross-encoder rerank: ON
  Model:   cross-encoder/ms-marco-MiniLM-L-6-v2
  Top-K:   30
  Cache:   loaded (cross-encoder/ms-marco-MiniLM-L-6-v2)
```

Or, if disabled, the command prints the env-var hint and the optional
install line — same pattern as `memee ranker status` for LTR.

## A/B results — `tests.r14_cross_encoder_eval`

Two configs over the full 207-query set (255-memory corpus, BM25-only
upstream so the cross-encoder lift is measured cleanly without the bi-
encoder confounding it):

```
metric            baseline   candidate      delta
--------------------------------------------------
nDCG@10             0.7273      0.7628    +0.0355   (p = 0.0002)
Recall@5            0.5701      0.5950    +0.0249
MRR                 0.8277      0.8676    +0.0400
p50 (ms)            1.34         41.4     +40.1
p95 (ms)            1.84         78.1     +76.3
```

Per cluster:

```
cluster                  n   base    cand   delta    p
-----------------------------------------------------------
code_specific           42  0.8355  0.8497 +0.0142  0.31
paraphrastic            43  0.6795  0.7093 +0.0298  0.08
anti_pattern_intent     32  0.8266  0.8194 -0.0072  0.63
onboarding_to_stack     25  0.6605  0.7729 +0.1124  0.03
diff_review             30  0.5557  0.6192 +0.0636  0.03
multilingual_lite       20  0.7724  0.7915 +0.0191  0.48
lexical_gap_hard        15  0.7446  0.7846 +0.0400  0.38
```

### Reading the numbers

* **Macro ship-rule passes**: +0.0355 at p=0.0002. The cross-encoder is
  better, on the whole eval set, with very high statistical confidence.
* **`paraphrastic` ship-rule narrowly misses**: Δ=+0.0298 vs the +0.03
  bar required, and p=0.08 vs the p<0.05 bar required. The point estimate
  matches the audit's prediction (+3-7 nDCG@10 on this cluster), but with
  n=43 the variance is high enough that the bar isn't cleared.
* **The biggest unambiguous wins are `onboarding_to_stack` (+0.1124, p=0.03)
  and `diff_review` (+0.0636, p=0.03)** — both clusters where the query is
  long-form and the gold doc shares intent rather than tokens. These are
  exactly the cases a cross-encoder's cross-attention is supposed to nail.
* **`anti_pattern_intent` regresses slightly** (Δ=-0.0072, p=0.63 — not
  significant). Plausibly the heuristic AP intent boost is doing useful
  work that the cross-encoder, trained on web search relevance, doesn't
  reproduce. Worth revisiting if we ever flip the default ON for AP-heavy
  deployments.
* **Latency cost is in budget**: p95 went from 1.8 ms to 78 ms. The brief
  budgeted +50-200 ms; we landed at the low end. p50 of 41 ms means a
  typical search costs the user ~40 ms more than before.

## Default OFF — but ship the wiring

The macro ship-rule is satisfied; we *could* flip the default ON. We're
not, and here's why:

1. **Latency budget is workload-specific.** The MCP path serves agents
   that fire many small searches per task; an extra 40 ms p50 is
   noticeable. The CLI path serves humans who'll never notice. Letting
   each deployment decide is the right move for OSS.
2. **`paraphrastic` didn't clear its specific ship-rule.** The audit
   predicted +3-7 nDCG@10 on that cluster; we got +2.98 nDCG@10 at p=0.08.
   That's the cluster that motivated the work, and it's the cluster the
   default-ON case rests on. We can revisit if a better model lands or
   if we extend the eval to n>50 on that cluster.
3. **Some clusters regress slightly.** Not significantly, but the
   reordering hurts `anti_pattern_intent` on point estimate. Default OFF
   means deployments that lean heavily on AP search aren't paying for a
   net loss.

The wiring is the substrate. A future R14.x with a stronger model
(`ms-marco-MiniLM-L-12-v2`, or one of the BAAI rerankers) can flip the
flag without touching code.

## Files touched

* `src/memee/engine/reranker.py` — new (220 LOC): cross-encoder wrapper +
  lazy load + flag.
* `src/memee/engine/search.py` — stage 5a wire (~10 LOC).
* `src/memee/cli.py` — `memee ranker rerank-status` command.
* `pyproject.toml` — `[rerank]` extra (`sentence-transformers>=3.0`).
* `tests/r14_cross_encoder_eval.py` — new A/B harness with paired
  permutation tests.
* `docs/r14-cross-encoder-rerank.md` — this file.

## Reproduce

```bash
# offline — uses HF cache if present, skips cleanly otherwise
.venv/bin/python -m tests.r14_cross_encoder_eval

# allow HF download to warm the cache once
.venv/bin/python -m tests.r14_cross_encoder_eval --allow-network

# exercise the hybrid path (BM25 ∪ vector ∪ tag-graph upstream)
.venv/bin/python -m tests.r14_cross_encoder_eval --vectors
```

The harness saves a JSON artefact at `.bench/r14_cross_encoder_eval.json`
with the full per-query nDCG arrays so a downstream change can run
`permutation_test` against it without re-running the eval.
