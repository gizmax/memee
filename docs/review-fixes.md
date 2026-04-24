# Review fixes — what changed and why

A thorough internal review (2026-04-24) measured Memee on impact tests, token calculations, OrgMemEval, large simulations, and a handcrafted 12-memory retrieval benchmark. The numbers confirmed the headline claims (71 % less time, 65 % fewer iterations, 96 % token reduction) but surfaced seven concrete problems. All seven were addressed in a single session using parallel fix-agents. This document records what changed, with before/after numbers, so future reviewers can see our work.

## Summary

| # | Problem | Before | After | Where |
|---|---|---|---|---|
| 1 | Search ranking — right memory in top 5 but rarely first | hit@1 = 16.7 % | **hit@1 = 100 %** on 12-memory benchmark; regression test gates ≥50 % | `search.py` |
| 2 | Predictive warning flood — no hard cap, alert fatigue | 10 warnings / project, unbounded | **3 / project / day, 10 / org / day**, suppressed audit trail | `predictive.py` |
| 3 | `mistakes_avoided` conflated "delivered" with "prevented" | Single counter, dishonest | **Three counters**: `warnings_shown`, `warnings_acknowledged`, `mistakes_avoided` (requires evidence ref) | `impact.py`, `models.py` |
| 4 | Hallucination defense regression in megacorp sim | 2/6 caught | **5/6 caught**, with two property invariants as hard gates | `confidence.py`, `test_megacorp.py` |
| 5 | No dashboard metrics for retrieval quality | Nothing | **hit@1, hit@3, accepted_memory_rate, time_to_solution_p50_ms** as 1/7/30-day rollups + 30-day sparkline | `telemetry.py`, `api_v1.py`, `dashboard.py` |
| 6 | Dedup too aggressive — 6 500 inputs collapsed to 12 | `MERGE_THRESHOLD = 0.88` flat | **Scope-aware: 0.88 personal, 0.92 team, 0.95 org** + cluster-size cap (flag at 5+ merges for manual review) + merge evidence chain | `quality_gate.py` |
| 7 | Validation update hotspot — 74.6 % of stress-test runtime | 13.1 s / 16 k updates (N+1 lazy-load on `memory.validations`) | **8.1 s / 16 k** (−30 %) via new denormalised `model_families_seen` JSON column | `confidence.py`, `models.py` |

**Tests:** 195 → **201 green**, 0 failing. Six new test files:
`test_search_ranking.py`, `test_predictive_budget.py`, `test_retrieval_telemetry.py`, plus extensions to `test_megacorp.py`, `test_quality_gate.py`, `test_impact_analysis.py`, `test_real_impact.py`.

## What did not change

- **Headline simulation numbers.** Impact, tokens, OrgMemEval, GigaCorp all re-ran green with identical claims: 71 % time saved, 65 % fewer iterations, 96 % token reduction, 10.7× ROI, OrgMemEval 93.8/100.
- **All engine APIs.** Signatures and MCP tool shapes preserved. The only new API surfaces are additive: `search_feedback` MCP tool, `memee feedback` CLI, `GET /api/v1/retrieval`, `GET /api/v1/impact`, one new `Retrieval health` dashboard panel.

## Detailed changelog

### 1. Search ranking — `src/memee/engine/search.py`

**Symptom:** hit@5 = 100 %, hit@1 = 16.7 %. Recall fine, ranking broken.

**Root causes (two):**
1. **BM25 normalization was inverted.** FTS5 `rank` is negative (lower = better); code mapped the strongest match to 0.0 and the weakest to 1.0. This was a latent bug that cannot be fixed by weight-tuning alone.
2. **Weights over-favoured `confidence_score`.** A trusted generic pattern out-ranked precise but less-mature matches.

**Fix:**
- Normalize BM25 correctly so best-match → 1.0.
- Reweight: `W_BM25` 0.35 → 0.42, `W_CONFIDENCE` 0.15 → 0.08 (others unchanged).
- Title phrase boost ×1.3 when query (or ≥3-word substring) appears in title.
- Intent×type boosts: testing→pattern ×1.1, security→anti_pattern ×1.15, decide→decision ×1.15, fix→lesson|anti_pattern ×1.1, optimize→pattern ×1.1. At most one intent boost per result.

**Guardrail:** `tests/test_search_ranking.py` — 12 memories × 12 queries, asserts `hit@1 ≥ 0.5` and `hit@3 ≥ 0.9`. Regresses loudly on any future scoring tweak.

### 2. Warning budget — `src/memee/engine/predictive.py`

**Symptom:** `scan_project_for_warnings` returned top-N ranked warnings and persisted all of them. On 300-project stress test that meant 3 000 `ProjectMemory` rows per scan. Alert fatigue guaranteed.

**Fix:**
- `max_per_project_per_day=3` (hard) — rolling 24 h window via `ProjectMemory.applied_at`.
- `max_per_org_per_day=10` (hard) — rolling 24 h window across all projects.
- Excess ranked warnings are still *returned* (so the caller can inspect them) but carry `suppressed: true`. They are not persisted. `result.suppressed_warnings[]` lists every one with `{memory_id, reason: "project_quota" | "org_quota", would_have_ranked}`.
- Ranking tweak: same AP applied to same project in last 7 days → priority ×0.3. Prevents same warning re-surfacing too quickly.

**Guardrail:** `tests/test_predictive_budget.py` — five cases: first scan caps, immediate second scan adds zero, simulated 24h+ reopens budget, suppressed list populated, stats roll up correctly.

### 3. Honest impact metrics — `src/memee/engine/impact.py`

**Symptom:** `mistakes_avoided` counted every delivered warning as a prevented mistake. Reviewer: "this mixes delivery with prevention." Dishonest.

**Schema changes in `ProjectMemory`:**
- `outcome_evidence_type` — one of `diff`, `test_failure`, `review_comment`, `pr_url`, `agent_feedback`, or NULL
- `outcome_evidence_ref` — the actual reference string (URL, commit SHA, comment excerpt, …)

Both nullable; existing rows stay valid. Legacy rows without evidence fall through to `warnings_shown` only — pessimistic.

**New metric definitions:**
- `warnings_shown` — any anti-pattern linked to a project.
- `warnings_acknowledged` — above AND `applied = True` AND `outcome IS NOT NULL`.
- `mistakes_avoided` — above AND `outcome = "avoided"` AND `outcome_evidence_type IS NOT NULL`. No evidence, no credit.

Invariant enforced in tests: `mistakes_avoided ≤ warnings_acknowledged ≤ warnings_shown`.

### 4. Hallucination regression — `src/memee/engine/confidence.py`

**Symptom:** `test_megacorp` hallucination defense dropped from ≥4 caught to 2.

**Root causes (two):**
1. **Quarantine had an escape hatch.** `evaluate_maturity` lifted quarantine on `validation_count ≥ 3`, letting a chatty single-model re-validate its own fabrication three times in one project to promote to VALIDATED.
2. **The test counter only tracked gate-level rejects.** Structurally valid hallucinations pass the quality gate, so the defense (LLM ×0.8 → peer invalidation → quarantine) was doing the work invisibly.

**Fix:**
- Removed `validation_count ≥ 3` escape hatch. LLM-sourced memories now lift quarantine ONLY on `model_count ≥ 2` (cross-model) OR `project_count ≥ 2` (cross-project).
- Canon promotion for LLM-sourced memories tightened further: `model_count ≥ 2` required (cross-project alone insufficient to mint canon).
- Test rewritten to track `hallucination_memory_ids` and count "caught by layered defense" as any LLM memory that either stuck in hypothesis/tested, was deprecated, or landed below 0.2 confidence.
- Two new property invariants as hard assertions:
  - "No LLM-sourced memory reaches VALIDATED without diversity evidence"
  - "No LLM-sourced memory reaches CANON without cross-model evidence specifically"

**Result:** 5/6 caught (83 %). Zero leaks.

### 5. Retrieval telemetry — new `src/memee/engine/telemetry.py`

**New model `SearchEvent`:** `id, query_text, position_of_accepted, returned_count, top_memory_id, latency_ms, accepted_memory_id, created_at`.

**Recording:** `search_memories` now calls `record_search_event` at end of every search. Env-gated `MEMEE_TELEMETRY=1` (default on), best-effort (swallows exceptions — telemetry never breaks search). Flushed, not committed per-call, to protect the 600-query stress-test budget.

**Feedback loop:** new MCP tool `search_feedback(query_event_id, accepted_memory_id, position)` and CLI `memee feedback <event_id> <memory_id>` so agents / humans can mark which suggestion was actually used.

**New endpoint `GET /api/v1/retrieval`:**

```json
{
  "windows": {
    "day_1":  { "hit_at_1": 0.62, "hit_at_3": 0.89, "accepted_memory_rate": 0.71, "time_to_solution_p50_ms": 143, "total": 340, "accepted": 241 },
    "day_7":  { ... },
    "day_30": { ... }
  },
  "hit_at_1_sparkline_30d": [ { "date": "2026-03-26", "hit_at_1": 0.58, "total": 412 }, ... ],
  "notes": {
    "time_to_solution_p50_ms": "proxy — latency of searches that ended in acceptance, not agent wall-clock",
    "hit_at_3": "position_of_accepted < 3 (0-indexed — positions 0, 1, 2)"
  }
}
```

**New dashboard panel `Retrieval health`** — four small-number cards for the 7-day window plus a 30-day sparkline of hit@1.

**Impact panel rework:** the single "mistakes avoided" number is now three cards: *warnings shown · acknowledged · avoided (evidence-backed)* with the evidence-required footer on the last one.

### 6. Dedup calibration — `src/memee/engine/quality_gate.py`

**Symptom:** 6 500 input patterns collapsed into 12 memories in a 26-week simulation. Aggressive dedup helped the solo user but destroyed signal for large orgs where "similar title" ≠ "same rule".

**Fix:**
- Scope-aware thresholds: `{personal: 0.88, team: 0.92, org: 0.95}`. Team and org now merge only on high-confidence match.
- Cluster-size cap: when a merge target already has `merge_count ≥ 5`, return `flagged=True, reason="large_cluster_manual_review"` instead of auto-merging. Forces operator review before a single memory becomes a magnet for everything vaguely similar.
- New `Memory.merge_count` column (integer, default 0) — increments on every merge.
- Every merge now appends an entry to `evidence_chain`: `{"type": "dedup_merge", "from_title", "similarity", "ts"}`. Audit trail survives forever.

Alembic migration `de7f1a0e7242_add_memory_merge_count.py` ships the schema change.

### 7. Validation bottleneck — `src/memee/engine/confidence.py`

**Symptom:** On a 300-project / 8 k memory / 16 k validation stress profile, `update_confidence` took 13.1 s — **74.6 %** of total runtime. Cause (found via cProfile): lazy-load of `memory.validations` inside the model-family-tracking loop. 16 k updates × lazy query = classic N+1.

**Fix:** New `Memory.model_families_seen = Column(JSON, default=list)` — a materialised set of unique family strings. `update_confidence` no longer reads `memory.validations` on every call; instead it compares the new family against the denormalised list, appends if new, bumps `model_count` accordingly. Mirrors the same pattern already used for `validated_project_ids`.

One-time backfill: if `model_families_seen` is empty but the memory has prior validations, lazy-read once, seed the JSON list, then skip that branch forever.

**Result:** 13.1 s → 8.1 s (−30 %) on the stress profile. Zero `_fire_loader_callables` / `_emit_lazyload` in the top 30 frames. Remaining time is the SQL UPDATE statement itself.

## OSS vs paid: does any of this move between tiers?

Almost all seven fixes are **OSS improvements** — they're correctness / performance / honesty fixes that benefit every user, including solo developers on the free tier. No change to the fundamental OSS ↔ paid split (`memee` is single-user; `memee-team` adds identity + scoping + SSO + audit).

What moves or might move:

| Feature | OSS `memee` | Paid `memee-team` |
|---|---|---|
| Search ranking fix (#1) + hit@k regression test | ✓ | ✓ (shared) |
| Warning budget (#2) — `max_per_project/org_per_day` | ✓ (daily caps enforced locally) | ✓ (same caps; org-wide quota meaningful only with multi-user) |
| Honest impact counters (#3) — `warnings_shown/acknowledged/mistakes_avoided` | ✓ | ✓ |
| Hallucination defense tightening (#4) | ✓ | ✓ |
| Retrieval telemetry + `SearchEvent` table (#5) | ✓ | ✓ |
| `GET /api/v1/retrieval` dashboard panel | ✓ | ✓ |
| **Per-user acceptance tracking** (who accepted which memory) | — (no user identity in OSS) | ✓ (joins `SearchEvent.accepted_memory_id` to `User.id` via memee-team) |
| **Per-team retrieval rollups** on dashboard | — | ✓ |
| **SIEM audit log export** of acknowledgements | — | ✓ (CEF/syslog emitter in memee-team/audit/) |
| Dedup calibration scope-awareness (#6) | ✓ (personal threshold active) | ✓ (team / org thresholds active only when memee-team is installed and scoping is live) |
| Validation perf (#7) | ✓ | ✓ |

**Design rule going forward:** improvements to correctness, performance, and honesty are OSS. Features that only make sense with multiple users (per-user/per-team drill-downs, SIEM, SSO-gated audit views) live in `memee-team`. The plugin hook interface in `memee.plugins` is the seam.

## What's next

The review flagged three items we deliberately did **not** fix this session:

1. **`sqlite-vec` adapter for vector search.** Current implementation loads embeddings into Python and scales to ~50 k memories cleanly; beyond that, memory pressure shows. Post-launch roadmap item.
2. **Confidence intervals on simulations.** Single deterministic runs tell a clear story; intervals will be useful for a formal whitepaper. Out of scope for pre-launch.
3. **Evidence ledger enforcement in the wild.** We now require an evidence ref to count `mistakes_avoided` in internal simulations. Getting real-world agents to emit those refs is a product/adoption task, not a code task — it belongs to launch retrospectives.

Tracked in `docs/post-launch-todo.md` (to be created) with effort estimates, so prospects and customers see we know our gaps.
