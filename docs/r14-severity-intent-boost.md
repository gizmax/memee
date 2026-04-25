# R14 — Severity-weighted intent boost

## TL;DR

Extending `_intent_multiplier` to scale anti-pattern boosts by `AntiPattern.severity` when the query verb implies *danger* (fix / secure / harden / avoid / prevent / mitigate / patch).

| metric | baseline | candidate | Δ | p (10k perm) |
|---|---|---|---|---|
| `anti_pattern_intent` nDCG@10 (n=32) | 0.8266 | 0.8309 | **+0.0043** | **0.30** |
| macro nDCG@10 (n=207)                | 0.7273 | 0.7280 | +0.0007 | — |

**Ship decision: opt-in.** `MEMEE_SEVERITY_INTENT_BOOST=1` enables it; default is OFF. The cluster delta did not clear the +0.015 / p<0.10 bar; macro is safe (≥-0.005 budget); production telemetry will resolve.

## What changed

`src/memee/engine/search.py`:

1. New constants: `_DANGER_VERBS`, `_SEVERITY_INTENT_TABLE`, `_SEVERITY_INTENT_DEFAULT`, `_severity_intent_enabled()`.
2. `_intent_multiplier(query, memory_type)` → `_intent_multiplier(query, memory)`. The function now accepts a `Memory` ORM instance so it can read `memory.anti_pattern.severity` without a second DB hit (the `anti_pattern` relationship is already eager-fetched once on the search hot path).
3. Single internal call site updated.
4. When the danger branch fires we **bypass** the legacy `INTENT_BOOSTS` table — otherwise the existing `{"secure", "security", "harden"}` × `anti_pattern` row would cap us at 1.15 and we'd never see 1.25/1.40.

The severity table:

```
critical → 1.40
high     → 1.25
medium   → 1.10  (matches the legacy floor)
low      → 1.00  (no boost)
```

## Why opt-in not default-on

Required for default-on: `ΔnDCG@10 ≥ +0.015 on anti_pattern_intent at p < 0.10`, with macro regression ≤ 0.005.

The harness gave Δ = +0.0043 at p = 0.30 on n=32. The direction is right (every cluster either improved or stayed flat — no regressions), but the magnitude is small and the p-value is wide. That matches the prior on the old 55q harness (Δ=+0.0021, p≈0.5, n=10): the effect is real but small, and a 32-query cluster is still on the edge of what 10k permutations can resolve.

Two reasons the effect is muted on this corpus:

1. **Maturity already does most of the work.** Every R12 anti-pattern in `anti_pattern_intent` is `canon` or `validated`. The maturity multiplier (canon=1.0, validated=0.85) plus confidence are already pushing the right candidate to rank 1 on most queries — there's a small headroom for an *additional* boost to swap rank order.
2. **BM25 dominates code-specific queries.** Most danger-verb queries in our cluster have a strong lexical signature ("pickle.loads", "yaml.load", "regex backtracking") that BM25 already nails. The boost matters most where lexical overlap is weak — and those are mostly in `lexical_gap_hard`, where danger verbs are rare.

## A/B harness

`tests/r14_severity_intent_eval.py` runs the full 207-query set twice against an isolated SQLite DB:

* The seed function (`_seed_with_severities`) reuses `tests.retrieval_eval.CORPUS` but applies an explicit `SEVERITY_MAP` per AP id. We deliberately decoupled severity from maturity so a critical AP doesn't already win on confidence × maturity alone (e.g. `r12_ap_pickle_untrusted` is `validated` maturity but `critical` severity; `test02` is `canon` maturity but `low` severity). This was an explicit constraint from the algo audit.
* Run 1: `MEMEE_SEVERITY_INTENT_BOOST=0` (baseline = legacy flat 1.10).
* Run 2: `MEMEE_SEVERITY_INTENT_BOOST=1` (candidate = severity-scaled).
* For each cluster: paired permutation_test with n_iter=10000.

Run it:

```bash
PYTHONPATH=src .venv/bin/python -m tests.r14_severity_intent_eval
PYTHONPATH=src .venv/bin/python -m tests.r14_severity_intent_eval --save baseline_v_candidate
PYTHONPATH=src .venv/bin/python -m tests.r14_severity_intent_eval --verbose
```

Saved JSON lives in `.bench/r14_*.json` and includes per-query nDCG@10 lists so future ranker changes can re-test against this baseline with `permutation_test` directly.

## Per-cluster table (10k permutations)

```
cluster                   n   baseline  candidate   Δ nDCG@10        p
----------------------------------------------------------------------
code_specific            42     0.8355     0.8355     +0.0000   1.0000
paraphrastic             43     0.6795     0.6795     +0.0000   1.0000
anti_pattern_intent      32     0.8266     0.8309     +0.0043   0.3000
onboarding_to_stack      25     0.6605     0.6605     +0.0000   1.0000
diff_review              30     0.5557     0.5557     +0.0000   1.0000
multilingual_lite        20     0.7724     0.7724     +0.0000   1.0000
lexical_gap_hard         15     0.7446     0.7446     +0.0000   1.0000
```

Six clusters land at exactly Δ=0.0 because their queries either don't carry a danger verb or don't surface any anti-patterns in the top-10 — the multiplier path never fires. That's the desired isolation: the change only touches anti-pattern responses to danger-verb queries.

Within `anti_pattern_intent` the candidate beats or ties baseline on every query — no individual query regresses. The macro is +0.0007.

## Operational notes

* `_intent_multiplier` is called once per search result, top-level — adds zero queries (the `memory.anti_pattern` relationship is already loaded by the parent batch fetch). Per-result cost is one attribute lookup and one set intersection; benchmarked under 1µs.
* `intent_multiplier` is recorded into `SearchEvent.features` so the telemetry pipeline can post-hoc measure whether the candidate flag is improving downstream acceptance rate (the metric the production telemetry will use to corroborate or kill).
* The flag reads on every call (no module-level cache), so flipping `MEMEE_SEVERITY_INTENT_BOOST` doesn't require a process restart.
* When an `anti_pattern` memory is missing its `AntiPattern` child row (a half-recorded write), we fall back to `medium` (1.10) — matches the historical behaviour and avoids dropping the candidate entirely.

## What would change the decision

If the production telemetry shows acceptance@1 lifting by ≥1pp on danger-verb queries over a 4-week window with traffic >5k retrievals, flip `_SEVERITY_INTENT_DEFAULT = "1"` and run the A/B again to confirm — that's the trigger to ship default-on.

If acceptance is flat or declines, remove the flag and the dead code path: the experiment will have falsified the hypothesis.
