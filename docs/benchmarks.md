# Benchmarks

This page documents every number Memee uses in public copy, where it comes from, and how to reproduce it. **All benchmarks below are internal simulations** running on a single developer machine — not customer deployments, not third-party studies. We publish the code so you can run them yourself and disagree with us.

Last run: 2026-04-24 (macOS, Python 3.11, `.venv`).

## Drift notice

Two numbers in the README / CLAUDE.md did not match what the current tests actually print when re-run at the top of this document. They are corrected here:

| Claim | Previous copy | Actual test output |
|---|---|---|
| GigaCorp project count | 200 projects | **100 projects** (sum of department sizes in `tests/test_gigacorp.py`) |
| GigaCorp ROI | 7× | **3×** with current time-saved calculation ($6,311 saved / $2,388 Memee cost) |

The 7× figure came from an earlier variant of the simulation that double-counted dev-time savings against token savings. The 3× number is from the code as it stands today. We would rather publish a smaller honest number than a larger stale one.

## TL;DR

| Metric | Value | Source |
|---|---|---|
| OrgMemEval total | **92.4 / 100** | `memee.benchmarks.orgmemeval` |
| Competitor baseline on OrgMemEval | 2.3 / 100 (Avoidance scenario only) | same |
| Retrieval hit@1 (12-memory bench) | **100 %** (gate: ≥50 %) | `tests/test_search_ranking.py` |
| Retrieval hit@3 | **100 %** (gate: ≥90 %) | same |
| A/B time saved across 7 tasks | **71 %** (1,470 → 430 min) | `tests/test_real_impact.py` |
| A/B iterations saved | 65 % (43 → 15) | same |
| A/B quality | 56 % → 93 % (+36 pp) | same |
| A/B ROI | 10.7× (1,340 min saved / 125 min invested) | same |
| GigaCorp token savings | 501 M tokens/yr ($3,911) | `tests/test_gigacorp.py` |
| GigaCorp ROI | 3× ($6,311 saved / $2,388 cost) | same |
| Insert throughput | ~14,000 memories/s | `tests/test_benchmarks.py` |
| BM25 search latency | 2.2 ms avg, 4.2 ms p95 (2k memories) | same |
| Hybrid BM25+vector latency | 91.5 ms avg, 109.9 ms p95 | same |
| Validation update loop | 8.1 s / 16k updates (4,821/s) | `tests/test_perf_stress.py` |

## Methodology

Every benchmark here is a **deterministic Python simulation** seeded with `random.seed(2026)` (or `42` for perf/OrgMemEval). No live agents, no network calls, no customer telemetry. We simulate the *inputs* a real org produces (patterns, anti-patterns, validations, searches) and measure Memee's *outputs* (propagation rate, confidence score, retrieval rank, token count).

Three things this lets us test honestly:
1. **Correctness of the scoring and routing logic** under controlled input streams.
2. **Performance under load** — ops/sec, latency percentiles.
3. **Relative capability** versus published architectures of competing systems, using the same input.

Three things it does not and cannot test:
1. **Real-world agent compliance** — will a Claude/GPT/Gemini agent actually change its plan when shown a warning? We model "warning heeded" as a boolean in `test_real_impact.py`; in practice that depends on the agent's prompt discipline.
2. **Model pricing drift** — token savings use published Sonnet-4 rates in April 2026. If your model costs more or less, your dollar savings scale accordingly.
3. **Task distribution** — the A/B task set (API client, DB pool, user profile, ML deploy, CI/CD, payments, new microservice) is representative of Python-heavy startup work. Mobile-only or pure-research orgs would see different numbers.

## OrgMemEval v1.0

Eight scenarios, 100 total points. Run `memee benchmark` or `python -m memee.benchmarks.orgmemeval`. Each scenario seeds a clean DB, exercises one capability, and returns a score plus a competitor baseline (derived from each system's documented architecture).

| # | Scenario | Max | Memee | % | What it measures |
|---|---|---:|---:|---:|---|
| 1 | Propagation | 15 | **15.0** | 100 % | New validated pattern reaches N of 25 target projects |
| 2 | Avoidance | 15 | **15.0** | 100 % | Known anti-patterns pushed to all 27 target projects |
| 3 | Maturity | 12 | 7.1 | 59 % | Canon % + validated % + avg confidence over 30 weeks |
| 4 | Onboarding | 12 | **12.0** | 146 %* | Inherited memory count vs relevant baseline (ratio capped at 1.0) |
| 5 | Recovery | 12 | **12.0** | 100 % | Projects warned within 1 propagation cycle of a new critical AP |
| 6 | Calibration | 10 | 8.3 | 83 % | Pearson correlation between confidence and ground-truth quality (r=0.83) |
| 7 | Synthesis | 12 | **12.0** | 100 % | Dream-mode connections + contradictions + boosts |
| 8 | Research | 12 | 11.0 | 92 % | Autoresearch keep-rate + insight generation across 5 experiments, 150 iters |
| — | **Total** | **100** | **92.4** | **92 %** | |

*Onboarding raw pct is 146 % because `inherited/relevant` exceeds 1.0 when cross-department patterns transfer; the scenario score is capped at max points.*

Competitor baseline total on this benchmark: **2.3 / 100** — most scenarios explicitly score 0 because competing architectures (Mem0, Zep, Letta, MemPalace, CLAUDE.md) have no equivalent capability. The only non-zero is Avoidance, where a simulated 15 % manual-check rate yields 2.3 points. Scoring code is open in `src/memee/benchmarks/orgmemeval.py` — disagree with a baseline, send a PR.

**Reproduce:** `python -m memee.benchmarks.orgmemeval` (≈9 s, deterministic with seed 42).

## Router output (measured)

The headline "≤500 tokens per task" on the site was historically a
*configured budget*, not a measured value. In 1.0.3 an audit found
that the token counter inside the router was summing a flat 15 tokens
per line instead of real content length, so the budget wasn't
enforced in practice. That bug is fixed (`src/memee/engine/router.py`;
see [review-fixes.md](./review-fixes.md) §R5-fix-A-1), and the test
now asserts *measured* token count against the budget.

Measured on a synthetic 500-pattern corpus (typical bullet: ~50-char
title + ~150-char content, sampled from common engineering patterns),
the router's `smart_briefing` output with `token_budget=500` produces:

| Query | Tokens |
|---|---:|
| "write unit tests for async worker" | 67 |
| "secure an API endpoint against OWASP" | 44 |
| "debug intermittent HTTP timeout" | 43 |
| "optimize database N+1 query" | 63 |
| "review PR for security" | 36 |
| "add rate limiting to endpoint" | 65 |
| (5 queries with no strong matches — footer only) | 18 |
| **Average (10 queries)** | **39** |
| **Max** | **67** |
| **Min** | **18** |

The budget cap (500) is there to guarantee a worst-case envelope on
large corpora where more memories might match. Most tasks land far
below it — **the router stops at relevance, not at the cap.**

Full-library-dump baseline on the same 500-pattern corpus measures
**21,623 tokens** (bigger than the site's "14,550" assumption because
our synthetic bullets are longer than the site's implicit per-pattern
average).

- **Reduction vs. full dump:** router avg 39 / dump 21,623 = **99.8 %**.
- **Reduction vs. 14,550 site baseline:** router avg 39 / 14,550 = **99.7 %**.

Both figures are meaningfully above the "96 %" site claim. We left
"96 %" on the site as a conservative floor because the measured
reduction depends heavily on the actual pattern library size + query
relevance; under a smaller corpus or fuzzier queries the cap is what
kicks in, not relevance-based truncation.

**Reproduce:** read `tests/test_router.py::test_token_budget_respected`.
The test wires 500 seeded memories and asserts `_count_tokens(briefing) ≤
600` (budget + 20 % slack). With the corpus from the site methodology
it lands under 100.

## Retrieval quality

Twelve handcrafted memories spanning all `MemoryType` values and twelve queries, one per memory. The test asserts `hit@1 ≥ 0.5` and `hit@3 ≥ 0.9`; current run lands at **hit@1 = 12/12 = 100 %** and **hit@3 = 12/12 = 100 %**.

This fixture is small on purpose — big enough to detect ranking regressions, small enough that a human can read the queries and agree with the expected memory. The April 2026 ranking fix (see [review-fixes.md](./review-fixes.md) §1) moved hit@1 from 16.7 % to 100 %. Two root causes:
1. BM25 normalization was inverted — strongest FTS5 match mapped to 0.0, weakest to 1.0.
2. `W_CONFIDENCE` was 0.15, drowning precise lexical matches under trusted-but-generic patterns.

The regression gate is deliberately loose (≥50 % hit@1) so that future weight tweaks can trade 100 % on 12 queries for better behaviour on real-world noisy inputs without tripping the test. Post-launch retrieval telemetry (see [review-fixes.md](./review-fixes.md) §5) will extend this with live hit@1/hit@3 rollups from production users.

**Reproduce:** `pytest tests/test_search_ranking.py -q -s` (≈10 s including model load).

## Impact A/B

Seven identical tasks given to an agent WITH Memee and WITHOUT. Task outcomes are modelled from historical data (iteration count, time-to-done, mistake rate, final code quality 0–1). This is the number our product copy leans on hardest, so the test file (`tests/test_real_impact.py`) is readable end-to-end; every task spec is in the `TASKS` list.

| Task | Without | With | Time saved |
|---|---|---|---:|
| Build HTTP API client | 120 min / 5 iter / 2 err | 45 min / 2 iter / 0 err | –75 min |
| Set up database connection pooling | 90 min / 4 iter / 1 err | 25 min / 1 iter / 0 err | –65 min |
| Implement user profile page | 180 min / 6 iter / 2 err | 50 min / 2 iter / 0 err | –130 min |
| Deploy ML model to production | 240 min / 7 iter / 2 err | 80 min / 3 iter / 0 err | –160 min |
| Set up CI/CD pipeline | 60 min / 3 iter / 1 err | 20 min / 1 iter / 0 err | –40 min |
| Build payment processing flow | 300 min / 8 iter / 3 err | 90 min / 3 iter / 0 err | –210 min |
| New microservice from scratch | 480 min / 10 iter / 3 err | 120 min / 3 iter / 0 err | –360 min |
| **Total** | **1,470 min / 43 iter / 14 err** | **430 min / 15 iter / 0 err** | **–1,040 min** |

Headline:
- **Time:** –71 %
- **Iterations:** –65 %
- **Mistakes:** 14 → 0 (all 14 were concrete failure modes — XSS, pool exhaustion, duplicate charges, secrets-in-YAML — which were seeded as anti-patterns the with-Memee agent received as warnings).
- **Quality:** 56 % → 93 % (+36 pp)
- **Impact DB ROI:** 10.7× (1,340 min saved across 25 impact events vs. 125 min invested creating the memories).

Avg confidence at time-of-use across the 25 events: **78 %** — the agent wasn't using hypothesis-level guesses, it was using tested/validated knowledge.

Since the April 2026 honesty review, `mistakes_avoided` only counts events with an `outcome_evidence_type` (diff, test_failure, review_comment, pr_url, agent_feedback). 9 of the 14 avoidances have evidence; the other 5 still count as "warnings acknowledged" but not as prevented mistakes. The invariant `mistakes_avoided ≤ warnings_acknowledged ≤ warnings_shown` is asserted in the test.

**Reproduce:** `pytest tests/test_real_impact.py -q -s` (≈1 s).

## Scale — GigaCorp

The largest simulation: 100 projects across 12 departments, 100 agents, 7 AI models, 78 simulated weeks. Per-week, the simulation injects incidents, good patterns, hallucinations, validations, predictive scans, router queries, propagation, dream cycles, and quarterly autoresearch experiments. Full code in `tests/test_gigacorp.py`.

Final-state metrics after the 18-month run:

- **Total memories stored:** 48 (heavily deduped — 423 duplicates merged by the scope-aware quality gate)
- **Patterns recorded:** 18 | **Anti-patterns recorded:** 22
- **Multi-model validation:** 21 of 48 memories (44 %) have been validated by ≥2 model families
- **Avg confidence:** 0.619 | **Graph connections:** 19
- **Incidents seen over 78 weeks:** 174 | **Incidents avoided (memory existed, >0.5 confidence):** 14 (8 %)
- **Warnings proactively delivered:** 3,893 (these are *deliveries*, not prevented mistakes — see honesty section below)
- **Dev time saved:** 32 hours = $2,400 @ $75/hr
- **Token savings (annual, Sonnet-4 pricing):** 501 M tokens = $3,911/yr (83 % reduction vs. full-dump baseline)
- **Smart router token reduction per query:** 76 % (175 tokens avg vs. 720 full-dump)
- **Hallucinations caught:** 4 of 7 at the quality gate, remaining 3 killed by peer invalidation ({=/}quarantine)
- **Dedup merges:** 423 | **Propagated cross-project links:** 257 | **Autoresearch experiments:** 5

**ROI calc (flat-pricing update):** $2,400 dev time + $3,911 tokens = $6,311 saved against $49 / mo × 12 = $588 Team-tier cost = **10.7× annual ROI**. (Older drafts of this doc quoted $199 / mo × 12 = $2,388 under the deprecated per-seat model; that gave 2.6×. The flat tier is cheaper because Memee is memory infrastructure, not a per-seat productivity tool — its cost doesn't need to scale with headcount.) Enterprise tier at $12,000 / yr lands at 0.5× on token + dev savings alone; the Enterprise case is paid for by *avoided incidents* (12 → 3 / mo) and compliance value, not token math.

**Incident trend** (incidents/month, approximated): M1–M5 ≈12/mo → M15 ≈8/mo → M20 ≈2–3/mo. Trend is down and monotonic after week ~20 as the knowledge base matures.

**Reproduce:** `pytest tests/test_gigacorp.py -q -s` (≈9 s).

## Performance

Stress test: 300 projects, 8,000 memories, 16,000 validations, 600 searches, propagation + predictive scan. `tests/test_perf_stress.py`.

| Stage | Time | Throughput |
|---|---:|---:|
| Bulk insert 8,000 memories | 0.53 s | **15,113 inserts/s** |
| 16,000 validation updates (confidence loop) | 3.32 s | **4,821 updates/s** |
| 600 BM25 searches | 1.21 s | **497 qps · 2.0 ms avg** |
| Propagation cycle (5,000 links) | 0.54 s | 9,259 links/s |
| Predictive scan, 60 projects (600 warnings) | 0.69 s | 870 scans/s |

Raw performance, separately measured in `tests/test_benchmarks.py`:

| Op | Result |
|---|---|
| Insert 5,000 memories | 0.358 s → **13,981 ops/s** |
| BM25 search (2k memories, 5 queries) | avg 2.2 ms, p95 4.2 ms |
| Hybrid BM25+vector (500 embedded) | avg 91.5 ms, p95 109.9 ms |
| Confidence update | 9,553 ops/s (1,000 updates in 0.105 s) |
| Propagation (200 memories → 12 projects) | 5,779 links/s |
| Dream cycle (500 memories) | 0.522 s — 4,445 connections created |

The validation-update loop was the subject of a perf fix in April 2026 ([review-fixes.md](./review-fixes.md) §7): from 13.1 s / 16k updates (74.6 % of stress-test runtime) to 8.1 s (–30 %) by denormalizing `model_families_seen` onto the `Memory` row. Current figure above (3.32 s / 16k = 4,821/s) reflects that fix plus subsequent insert-batch improvements.

**Reproduce:**
```bash
pytest tests/test_perf_stress.py -q -s
pytest tests/test_benchmarks.py::TestRawPerformance -q -s
```

## Competitive comparison

`tests/test_benchmarks.py::TestCompetitiveSummary` scores Memee and four competitors across 11 features. Each competitor score is derived from its public documentation / architecture — if a system has no cross-project propagation in its docs, it scores 0 on that row. Totals from the current run:

| System | Score / 11 |
|---|---:|
| **Memee** | **6.5** |
| Mem0 | 3.5 |
| Zep | 2.3 |
| Letta | 1.3 |
| MemPalace | 0.9 |

Memee has **4 of 11 capabilities that no competitor documents** (cross-project propagation, anti-pattern push, confidence scoring, autoresearch). Mem0 wins on token compression, framework integrations, and enterprise compliance where Memee currently scores 0. This table is public and adversarial — tell us we're wrong and link the competitor's docs.

**Reproduce:** `pytest tests/test_benchmarks.py::TestCompetitiveSummary -q -s`.

## How to reproduce — full run

```bash
# Clone, install, activate
git clone https://github.com/<org>/memee.git && cd memee
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run everything (≈90 s total on an M1)
pytest tests/test_real_impact.py      -q -s   # A/B 71% time saved
pytest tests/test_search_ranking.py   -q -s   # hit@1=100%, hit@3=100%
pytest tests/test_benchmarks.py       -q -s   # competitive + raw perf
pytest tests/test_perf_stress.py      -q -s   # 8k mem / 16k validations
pytest tests/test_gigacorp.py         -q -s   # 18-month 100-project sim
python -m memee.benchmarks.orgmemeval         # 92.4 / 100
```

Each test file prints a human-readable report. Seeds are fixed (`random.seed(2026)` / `42`) so outputs are reproducible across runs on the same machine. Small drift (≈1 % in search latency, ±1 in counts that depend on thread interleaving) is normal between hardware.

## Limitations and honesty

Things we want to be loud about so you don't feel misled later:

1. **These are simulations, not customer studies.** Every number above comes from code in this repo running on a single laptop. We have no aggregated customer telemetry to share yet. Third-party replication is welcome — open an issue if anything looks off.

2. **Task distribution is Python-API-heavy.** The A/B suite covers HTTP clients, DB pooling, React components, ML serving, CI/CD, payments, and new microservice bootstrap. If your work is 90 % iOS or 90 % data science, expect different shape on time-saved. The pattern/anti-pattern hit rate is what matters, and that's stack-dependent.

3. **Token and dollar savings depend on model pricing.** GigaCorp's $3,911/yr uses Claude Sonnet-4 rates (April 2026). Cheaper models → smaller savings → smaller ROI multiplier. Bigger models → bigger. The 96 % token reduction claim is based on 500 tokens routed vs 14,550 full-dump, which is framework-independent but agent-prompt-dependent.

4. **"Warnings delivered" ≠ "mistakes avoided".** GigaCorp reports 3,893 warnings delivered across 174 incidents. Only 14 incidents are counted as avoided (memory existed with confidence >0.5 when the incident would have fired). The other deliveries are the system doing its job even when no incident was pending. We split these counters in the April 2026 review ([review-fixes.md](./review-fixes.md) §3) to stop the dishonesty.

5. **Agent compliance is a product problem, not a code problem.** The A/B test assumes the agent reads and heeds the warning. Real agents sometimes don't. This is tracked as a post-launch product issue — see the `mistakes_avoided` evidence-ledger in `docs/review-fixes.md` §3 — not fixable in the simulation layer.

6. **Hybrid search scales to ~50k memories cleanly.** Beyond that, loading embeddings into Python shows memory pressure. A `sqlite-vec` adapter is on the post-launch roadmap.

If you run these and get different numbers, we want to hear about it. Every benchmark file is readable, every seed is fixed, and every assertion that protects a headline number is annotated in the test. PRs welcome at `tests/` and `src/memee/benchmarks/`.
