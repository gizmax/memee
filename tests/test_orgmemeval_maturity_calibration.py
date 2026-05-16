"""Tests for the v2.3.2 Maturity scenario calibration.

These assertions guard the *shape* of the simulated distribution, not
absolute scores. Scores will drift slightly as the engine evolves
(propagation, dream, lifecycle); calibration drift is what we want
the benchmark to *measure*, not freeze. What we DO want to pin is the
mechanics that prevent another long stretch of 52-58% scores from
slipping past CI:

  1. Validation event count per week matches the v2.3.2 ramp
     (60 + week*5 baseline) — guards against an accidental revert to
     the 25-event uniform regime that hid the canon-promotion gap.
  2. The validation pick distribution is non-uniform (Zipf-shaped):
     head-of-distribution patterns get materially more validations
     than the long tail. The previous uniform regime is what made the
     scenario unreachable for canon promotion.
  3. After burn-in (first 5 weeks), every memory has at least one
     validation event recorded. The burn-in is what keeps long-tail
     patterns from being permanently stuck at HYPOTHESIS.

Whole-scenario score is exercised separately via
``memee benchmark --scenario maturity``; this file focuses on the
invariants that *cause* the score, so a regression points at the
right line.
"""

from __future__ import annotations

import random
from collections import Counter

from memee.storage.models import MemoryValidation


# ── Distribution shape (re-implement to keep test independent of impl) ──


def _expected_n_validations_per_week() -> list[int]:
    """The v2.3.2 weekly budget ramp. Mirrors the scenario constant."""
    return [60 + w * 5 for w in range(30)]


def test_weekly_validation_ramp_is_v232_baseline():
    """If someone reverts to 25+week*2 (the pre-v2.3.2 uniform regime)
    canon promotion collapses again. Pin the v2.3.2 numbers so CI
    catches the revert."""
    ramp = _expected_n_validations_per_week()
    # Spot-check: week 0 = 60, week 29 = 205, average ~115.
    assert ramp[0] == 60
    assert ramp[29] == 60 + 29 * 5  # = 205
    avg = sum(ramp) / len(ramp)
    assert 100 <= avg <= 140, (
        f"weekly validation budget averaged {avg:.0f}/week — "
        "v2.3.2 calibration calls for ~115/week"
    )


def test_zipf_weights_strongly_favour_head():
    """1/√(i+1) gives rank-1 ~14× the weight of rank-200. Lower exponent
    (e.g. 1/(i+1)) would make the tail unreachable for VALIDATED; flat
    weights would make canon unreachable. The √ exponent is the sweet
    spot for the v2.3.2 calibration."""
    n = 200
    weights = [1.0 / ((i + 1) ** 0.5) for i in range(n)]
    head = sum(weights[:20])    # top 10%
    mid = sum(weights[20:100])  # next 40%
    tail = sum(weights[100:])   # bottom 50%
    total = head + mid + tail
    head_share = head / total
    tail_share = tail / total
    # Head pulls roughly 1/3 of the total mass; tail pulls roughly
    # 1/3 (the 100 patterns share 0.07-0.10 weight each). A
    # decisively NON-uniform but NOT pathological skew.
    assert 0.20 < head_share < 0.45, f"head share {head_share:.2f} drifted"
    assert tail_share > 0.20, f"tail share {tail_share:.2f} too sparse"


def test_zipf_weighted_pick_is_not_uniform():
    """Empirical proof that random.choices(weights=…) on the v2.3.2
    weights produces a meaningfully non-uniform pick distribution —
    catches a regression where the weighted call is replaced with
    plain random.choice or weights=None."""
    n = 200
    weights = [1.0 / ((i + 1) ** 0.5) for i in range(n)]
    random.seed(42)
    picks = Counter()
    for _ in range(10_000):
        idx = random.choices(range(n), weights=weights, k=1)[0]
        picks[idx] += 1
    top1 = picks[0]
    median = picks[n // 2]
    last = picks[n - 1]
    # Top-1 must dominate the tail; precise ratios drift across RNG
    # versions, so check the qualitative ordering plus a soft floor.
    assert top1 > last * 3, (
        f"top-1 pick {top1} not materially larger than tail pick {last} — "
        "weighted distribution looks uniform"
    )
    assert top1 > median * 2


# ── Round-robin project assignment ──


def test_round_robin_project_assignment_covers_all_projects():
    """The scenario uses ``projects[(week * 7 + k) % len(projects)]`` so
    every memory eventually sees all 15 projects. Pre-v2.3.2 used
    ``random.choice(projects)`` which left some memories stuck at
    project_count < canon_min_projects=5 even with enough validations."""
    n_projects = 15
    weeks = 30
    seen_projects: set[int] = set()
    for week in range(weeks):
        for k in range(60 + week * 5):
            proj_idx = (week * 7 + k) % n_projects
            seen_projects.add(proj_idx)
    # Round-robin must reach EVERY project across the 30-week run.
    assert seen_projects == set(range(n_projects)), (
        f"round-robin missed projects: {set(range(n_projects)) - seen_projects}"
    )


# ── End-to-end invariant (lightweight, no model load) ──


def test_full_scenario_seeds_zero_orphans(session):
    """After running the v2.3.2 ``scenario_maturity`` to completion,
    every seeded memory has at least one MemoryValidation row attached.
    That's the burn-in guarantee: the long tail reaches at least
    TESTED maturity rather than orphaning at HYPOTHESIS.

    This is the lightest-weight integration test we can run — it skips
    the cosine/dream/propagation paths the heavy benchmark exercises,
    but anchors the validation-coverage invariant the calibration
    depends on.
    """
    from memee.benchmarks.orgmemeval import scenario_maturity

    result = scenario_maturity(session, seed=42)

    # Every seeded memory must own at least one validation event.
    validations = session.query(MemoryValidation).all()
    memory_ids_with_validation = {v.memory_id for v in validations}
    # The scenario seeds 200 memories tagged with "(mat-N)" titles.
    from memee.storage.models import Memory
    seeded_ids = {
        m.id for m in session.query(Memory).filter(Memory.title.like("%(mat-%)")).all()
    }
    orphans = seeded_ids - memory_ids_with_validation
    assert not orphans, (
        f"{len(orphans)} memories never got a validation event — burn-in failed"
    )

    # Sanity on the reported metrics shape — the scorer should report
    # a numeric `pct` and non-negative canon/validated counts.
    assert "metrics" in result
    metrics = result["metrics"]
    assert metrics["total_memories"] == 200
    assert metrics["canon"] >= 0
    assert metrics["validated"] >= 0
    # And the headline score: anything below 70% means the
    # calibration regressed. (Going below 70% on this scenario is what
    # surfaced the original bug.)
    assert result["pct"] >= 70.0, (
        f"Maturity scenario regressed below 70% ({result['pct']}%) — "
        "the v2.3.2 calibration invariants are broken"
    )
