# R14 — Maturity-gated query expansion

## Summary

A second gate on the router's query expansion (`router._build_search_query`).
R10 cycle 1 already gates expansion on `_db_has_any_embeddings` so BM25-only
deployments don't pay a -0.0265 nDCG@10 hit from dilution. R14 adds a
maturity gate: when the **raw** task already lights up a
CANON/VALIDATED `pattern` strongly via BM25, expansion will only dilute
the win — skip it.

The gate is shipped behind an opt-in env flag
(`MEMEE_MATURITY_GATED_EXPANSION=1`) because the 207-query A/B harness
showed ΔnDCG@10 = 0.0000 at p=1.0000 on the bench corpus. The plumbing
and probe are in place so operators with different traffic mixes can
flip the switch.

## Why a second gate

Expansion broadens recall by appending related terms (`"deploy"` →
`"deploy docker kubernetes health check"`). On vector-aware DBs that
trade is a net win on the paraphrastic / lexical-gap clusters where
BM25 alone misses the gold. But when the raw query is already a clear
canon hit — e.g. `"pgbouncer transaction pool prepared statements"` →
the pgbouncer canon memory dominates BM25 — the extra terms bring in
neighbour topics that can outrank the canon under RRF.

The gate doesn't decide what the *retriever* does. It decides whether
the **router-issued query** is the raw task or the expanded form.
Search runs unchanged either way.

## Implementation

`src/memee/engine/router.py`:

* `_strong_canon_match(session, raw_query, threshold=None)` — runs one
  FTS5 `MATCH` joined to `memories` with the type+maturity filter
  pushed into SQL, returns the top-3 BM25 ranks. Filters: `type =
  'pattern'`, `maturity IN ('canon', 'validated')`. Cost: 1-2 ms warm
  on the bench corpus, ≤5 ms on a 5 k-memory production tier.
* Dominance normalisation: `top / (top + second)`, bounded in `(0, 1]`.
  At 1.0 the second hit is a non-match and the top is unambiguous; at
  0.5 top and second are tied and the canon answer isn't clearly the
  right one. Threshold `0.7` corresponds to "the top is at least
  ~2.3× the second hit" — a strong-canon signal.
* Single-row result is treated as full dominance (a top with no rival
  is the strongest possible signal).
* Any FTS5 / DB error is swallowed and the gate returns False, so the
  caller falls through to the existing expansion path. The gate is
  purely additive: when it works it skips expansion, when it doesn't
  work the existing R10 behaviour is preserved exactly.
* `_build_search_query(task, stack_tags, session=None)` — gains the
  optional `session` parameter; when present and the gate is enabled,
  runs the probe before `_expand_query`.
* `smart_briefing` passes `session` through.

`MEMEE_MATURITY_GATED_EXPANSION` accepts `1`/`true`/`on`/`yes` to
enable; everything else (or unset) leaves the gate disabled.
`MEMEE_MATURITY_GATE_THRESHOLD` accepts a float in `[0.0, 1.0]`,
default `0.7`.

The R10 `_db_has_any_embeddings` gate is **untouched**. R14 is the
inner gate; on BM25-only DBs `_build_search_query` is never called
(the BM25-only branch in `smart_briefing` skips expansion already), so
R14 only matters on vector-aware deployments — exactly where
expansion runs.

## A/B measurement

`tests/r14_maturity_gated_expansion_eval.py` runs three configs over
the 207-query / 255-memory bench (the same corpus
`tests/retrieval_eval.py` uses, with the seven difficulty clusters):

| Config              | Gate | Threshold |
|---------------------|------|-----------|
| `baseline`          | off  | —         |
| `candidate_A_thr_0.7`  | on   | 0.7       |
| `candidate_B_thr_0.85` | on   | 0.85      |

Per-cluster nDCG@10 + paired permutation test (10 k iterations) vs
baseline. Macro nDCG@10 + macro permutation test.

### Findings

```
=== candidate_A_thr_0.7 vs baseline ===
metric               baseline       cand      delta
----------------------------------------------------
macro_ndcg10           0.7241     0.7241 +   0.0000
macro_recall5          0.5688     0.5688 +   0.0000
macro_recall10         0.6337     0.6337 +   0.0000
macro_mrr              0.8227     0.8227 +   0.0000
macro_mat_b5           0.8882     0.8878   -0.0004

Macro permutation_test p=1.0000
Gate fired: 180 / 207 (87.0%)

cluster                   n     base     cand     delta        p
----------------------------------------------------------------
code_specific            42   0.8349   0.8349   +0.0000   1.0000
paraphrastic             43   0.6626   0.6626   +0.0000   1.0000
anti_pattern_intent      32   0.8264   0.8264   +0.0000   1.0000
onboarding_to_stack      25   0.6569   0.6569   +0.0000   1.0000
diff_review              30   0.5618   0.5618   +0.0000   1.0000
multilingual_lite        20   0.7724   0.7724   +0.0000   1.0000
lexical_gap_hard         15   0.7446   0.7446   +0.0000   1.0000
```

Candidate B (threshold 0.85) is byte-identical to A on this corpus.

The "gate fired 180/207 (87 %)" headline is misleading at first read.
Under the hood:

* `_expand_query` actually fires for **28** of the 207 queries — most
  bench queries have no expansion-keyword match.
* The maturity gate fires for **28** queries overall (mostly
  `code_specific` rare-identifier queries like `"pgbouncer transaction
  pool prepared statements"` — the canon answer dominates BM25 by a
  wide margin, so dominance > 0.7).
* Of those 28 gate-firing queries, only **1** also triggers expansion
  (`"npm ci npm audit signatures"`). On the other 179 queries
  expansion was already a no-op, so the gate firing changes nothing
  observable.

That single intersection moved one ID in the top-10 of one query and
shifted `mat_b5` by -0.0004 — well below any significance threshold.

### Ship rule and decision

Ship rule:

* Macro nDCG@10 ≥ +0.003 at p < 0.10  **OR**
* `code_specific` cluster ≥ +0.005 at p < 0.10

Neither bar is crossed → **ship behind opt-in flag**
(`MEMEE_MATURITY_GATED_EXPANSION=1`).

The probe and gate are committed; the env knob is documented; and the
A/B harness lives in tree so a future bench (different traffic mix,
larger corpus, real production search log) can re-run the decision
with one command.

## Why ship the dead code

The gate is correct on the queries it fires on — when the raw query
already nails a CANON pattern, expansion is wasted work. Production
traffic looks different: the 60+ expansion keys cover engineering,
marketing, product, design, ops; real agent tasks ("deploy a new
service safely without downtime") trigger expansion far more often
than this bench does. Cost is a single FTS5 query (~1-2 ms warm) —
a rounding error on a search call that's already 100+ ms.

## Re-evaluating

Flipping the default is a one-character PR — change `"0"` to `"1"` in
`_maturity_gate_enabled` and re-run the A/B. The harness preserves
its per-query log so a single config re-runs in ~10 s.

## Files touched

* `src/memee/engine/router.py` — `_strong_canon_match`,
  `_maturity_gate_enabled`, `MATURITY_GATE_THRESHOLD`, plus a
  `session=` argument on `_build_search_query`.
* `tests/r14_maturity_gated_expansion_eval.py` — three-config A/B
  harness with per-cluster permutation tests and a ship-rule
  evaluator.
* `docs/r14-maturity-gated-expansion.md` — this file.
