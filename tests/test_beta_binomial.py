"""Tests for the v2.4.0 Beta-Binomial confidence posterior.

The refactor replaces the pre-v2.4.0 ``conf + w·(1-conf)`` update rule
with a proper Beta(α, β) posterior. Each Memory carries two Float
columns ``alpha`` and ``beta`` that count weighted evidence; the
``confidence_score`` column stays populated as the derived posterior
mean ``α/(α+β)`` so the 30+ callsites that read it keep working.

These tests pin:

  1. **Schema**: ``alpha`` and ``beta`` columns exist with the Beta(1, 1)
     default (mean = 0.5, matching the legacy ``confidence_score=0.5``).
  2. **Update mechanics**: validation increments α by an evidence weight
     (cross-project × cross-model × diminishing-returns); invalidation
     increments β by the same weight (symmetric).
  3. **HDI shape**: ``confidence_hdi`` returns a credible interval that
     *widens* with conflicting evidence and *narrows* with consistent
     evidence — the fundamental property the legacy ``±1/√(n+1)`` rule
     could not express.
  4. **Backfill**: legacy rows with non-trivial validation history but
     default ``(α, β) = (1, 1)`` get back-filled to preserve the
     historical confidence_score AND the evidence weight.
  5. **Order invariance**: identical sets of evidence in different
     orders produce identical posteriors (the pre-v2.4.0 proportional
     decay was order-dependent).
"""

from __future__ import annotations

import pytest

from memee.engine.confidence import (
    confidence_hdi,
    get_confidence_interval,
    get_uncertainty,
    update_confidence,
)
from memee.storage.models import MaturityLevel, Memory, MemoryType


# ── Schema ────────────────────────────────────────────────────────


def test_fresh_memory_starts_at_beta_1_1(session):
    """Default prior is Beta(1, 1) — uniform, mean 0.5."""
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="t", content="c", tags=["x"],
        maturity=MaturityLevel.HYPOTHESIS.value, confidence_score=0.5,
        source_type="human",
    )
    session.add(m)
    session.commit()
    assert m.alpha == pytest.approx(1.0)
    assert m.beta == pytest.approx(1.0)
    assert m.confidence_score == pytest.approx(0.5)


# ── Update mechanics ─────────────────────────────────────────────


def test_validation_increments_alpha_with_base_weight(session):
    """Same-project, same-model validation: α += 1.0 (no bonuses)."""
    m = Memory(
        type=MemoryType.PATTERN.value, title="t", content="c", tags=[],
        maturity=MaturityLevel.HYPOTHESIS.value, confidence_score=0.5,
        source_type="human", source_model="claude-opus-4",
    )
    session.add(m)
    session.commit()

    update_confidence(m, True, project_id="p1", model_name="claude-sonnet-4")
    # Same family (anthropic), no project history → no cross bonus,
    # BUT this is the first time p1 validates so cross-project fires (w=1.5).
    assert m.alpha == pytest.approx(2.5)  # 1 + 1.0*1.5
    assert m.beta == pytest.approx(1.0)
    assert m.confidence_score == pytest.approx(2.5 / 3.5, abs=0.005)


def test_cross_project_and_cross_model_stacks(session):
    """Cross-project (×1.5) AND cross-model (×1.3) stack as a single
    evidence weight 1.95, not as separate score nudges."""
    m = Memory(
        type=MemoryType.PATTERN.value, title="t", content="c", tags=[],
        maturity=MaturityLevel.HYPOTHESIS.value, confidence_score=0.5,
        source_type="human", source_model="claude-opus-4",
    )
    session.add(m)
    session.commit()

    update_confidence(m, True, project_id="p1", model_name="gpt-4o")
    # 1.0 × 1.5 (new project) × 1.3 (new family) = 1.95
    assert m.alpha == pytest.approx(2.95)
    assert m.beta == pytest.approx(1.0)


def test_invalidation_increments_beta_symmetrically(session):
    """Invalidation evidence is weighted the same way as validation —
    the pre-v2.4.0 ``-0.12·conf`` decay had no statistical justification
    for the asymmetry."""
    m = Memory(
        type=MemoryType.PATTERN.value, title="t", content="c", tags=[],
        maturity=MaturityLevel.HYPOTHESIS.value, confidence_score=0.5,
        source_type="human", source_model="claude-opus-4",
    )
    session.add(m)
    session.commit()

    update_confidence(m, False, project_id="p1", model_name="gpt-4o")
    # β += 1.0 × 1.5 (new project) × 1.3 (new family) = 2.95
    assert m.alpha == pytest.approx(1.0)
    assert m.beta == pytest.approx(2.95)
    assert m.confidence_score == pytest.approx(1.0 / 3.95, abs=0.005)


def test_repeat_same_project_validations_diminish(session):
    """Each subsequent validation from the same project carries less
    evidence weight (``×0.95^same_count``) so a chatty agent can't
    promote its own opinion by spamming the same memory."""
    m = Memory(
        type=MemoryType.PATTERN.value, title="t", content="c", tags=[],
        maturity=MaturityLevel.HYPOTHESIS.value, confidence_score=0.5,
        source_type="human", source_model="claude-opus-4",
    )
    session.add(m)
    session.commit()

    # First validation from p1: weight 1.5 (new project)
    update_confidence(m, True, project_id="p1", model_name="claude-sonnet-4")
    a1, b1 = m.alpha, m.beta
    # Second validation from same p1, same model: weight 1.0 * 0.95^0
    update_confidence(m, True, project_id="p1", model_name="claude-sonnet-4")
    a2, b2 = m.alpha, m.beta
    # Third: weight 1.0 * 0.95^1 = 0.95
    update_confidence(m, True, project_id="p1", model_name="claude-sonnet-4")
    a3, b3 = m.alpha, m.beta

    # Each increment is smaller than the last.
    inc_1 = a2 - a1
    inc_2 = a3 - a2
    assert inc_2 < inc_1, f"diminishing returns broken: {inc_1=}, {inc_2=}"
    assert inc_2 == pytest.approx(0.95, abs=0.01)


# ── HDI / uncertainty ────────────────────────────────────────────


def test_hdi_widens_under_conflict(session):
    """A memory with 5 validations and 5 invalidations should have a
    *wider* credible interval than a memory with 10 validations and 0,
    even at the same posterior mean. The pre-v2.4.0 ``±1/√(n+1)``
    formula treated them identically because it only counted ``n``."""
    m_consistent = Memory(
        type=MemoryType.PATTERN.value, title="consistent", content="c",
        tags=[], maturity=MaturityLevel.HYPOTHESIS.value,
        confidence_score=0.5, source_type="human",
    )
    m_conflicted = Memory(
        type=MemoryType.PATTERN.value, title="conflicted", content="c",
        tags=[], maturity=MaturityLevel.HYPOTHESIS.value,
        confidence_score=0.5, source_type="human",
    )
    session.add_all([m_consistent, m_conflicted])
    session.commit()

    # Manually set α/β to isolate the HDI shape (skip update_confidence's
    # weighting machinery — we're testing the interval, not the update).
    m_consistent.alpha = 11.0  # +10 validations
    m_consistent.beta = 1.0
    m_conflicted.alpha = 6.0   # +5 validations
    m_conflicted.beta = 6.0    # +5 invalidations
    session.commit()

    lo_c, mean_c, hi_c = confidence_hdi(m_consistent)
    lo_x, mean_x, hi_x = confidence_hdi(m_conflicted)

    width_c = hi_c - lo_c
    width_x = hi_x - lo_x
    # Conflicted memory has more evidence (12 vs 11) but also more
    # variance — its HDI must be wider.
    assert width_x > width_c, (
        f"HDI did not widen under conflict: consistent={width_c:.3f} "
        f"conflicted={width_x:.3f}"
    )
    # Conflicted memory's mean is right at 0.5; consistent is near 0.9.
    assert mean_x == pytest.approx(0.5, abs=0.01)
    assert mean_c == pytest.approx(11.0 / 12.0, abs=0.005)


def test_uncertainty_shrinks_with_evidence(session):
    """``get_uncertainty`` returns the Beta posterior SD, which shrinks
    toward zero as α+β grows. Fundamental property of conjugate updates."""
    m = Memory(
        type=MemoryType.PATTERN.value, title="t", content="c", tags=[],
        maturity=MaturityLevel.HYPOTHESIS.value, confidence_score=0.5,
        source_type="human", source_model="claude-opus-4",
    )
    session.add(m)
    session.commit()

    fresh_sd = get_uncertainty(m)
    for i in range(20):
        update_confidence(m, True, project_id=f"p{i}", model_name="claude-sonnet-4")
    matured_sd = get_uncertainty(m)
    assert matured_sd < fresh_sd / 4, (
        f"posterior SD did not shrink with evidence: "
        f"fresh={fresh_sd:.3f} matured={matured_sd:.3f}"
    )


def test_get_confidence_interval_backcompat(session):
    """Legacy callers reading ``(lower, upper)`` should see a clamped
    [0, 1] interval matching the 95 % credible interval."""
    m = Memory(
        type=MemoryType.PATTERN.value, title="t", content="c", tags=[],
        maturity=MaturityLevel.HYPOTHESIS.value, confidence_score=0.5,
        source_type="human",
    )
    session.add(m)
    session.commit()

    lo, hi = get_confidence_interval(m)
    assert 0.0 <= lo <= hi <= 1.0
    # Beta(1, 1) HDI95 ≈ [0.025, 0.975]
    assert lo == pytest.approx(0.025, abs=0.05)
    assert hi == pytest.approx(0.975, abs=0.05)


# ── Backfill ─────────────────────────────────────────────────────


def test_legacy_row_backfills_alpha_beta_on_update(session):
    """A memory with non-trivial confidence + counts but the schema
    default (α, β) = (1, 1) should get back-filled on first
    ``update_confidence`` call. The mapping ``α = conf·n + 1,
    β = (1-conf)·n + 1`` preserves the posterior mean exactly while
    encoding the right amount of evidence."""
    m = Memory(
        type=MemoryType.PATTERN.value, title="legacy", content="c", tags=[],
        maturity=MaturityLevel.VALIDATED.value,
        confidence_score=0.80,
        validation_count=8,
        invalidation_count=2,
        source_type="human",
    )
    session.add(m)
    session.commit()

    # Fresh schema defaults until first update.
    assert m.alpha == pytest.approx(1.0)
    assert m.beta == pytest.approx(1.0)

    # Backfill on first touch.
    update_confidence(m, True, project_id="p_new", model_name="claude")

    # Pre-update backfill: α = 0.80*10 + 1 = 9, β = 0.20*10 + 1 = 3.
    # Then the validation adds the evidence weight. The legacy memory
    # has no source_model AND empty model_families_seen, so the
    # validator "claude" (anthropic) counts as a new family → weight
    # 1.5 (new project) × 1.3 (new family) = 1.95.
    assert m.alpha == pytest.approx(9.0 + 1.95, abs=0.01)
    assert m.beta == pytest.approx(3.0, abs=0.01)


# ── Order invariance ─────────────────────────────────────────────


def test_same_evidence_different_order_same_posterior(session):
    """Two memories receiving the same evidence stream in different
    orders should have identical posteriors. The pre-v2.4.0
    proportional-decay rule was *order-dependent* (early validations
    moved conf more because (1-conf) was larger) — a known math
    weakness flagged by the dossier."""
    m_a = Memory(
        type=MemoryType.PATTERN.value, title="A", content="c", tags=[],
        maturity=MaturityLevel.HYPOTHESIS.value, confidence_score=0.5,
        source_type="human", source_model="claude-opus-4",
    )
    m_b = Memory(
        type=MemoryType.PATTERN.value, title="B", content="c", tags=[],
        maturity=MaturityLevel.HYPOTHESIS.value, confidence_score=0.5,
        source_type="human", source_model="claude-opus-4",
    )
    session.add_all([m_a, m_b])
    session.commit()

    # m_a: V, V, V, V, I (all distinct projects, same model family)
    sequence_a = [
        (True, "p1"), (True, "p2"), (True, "p3"), (True, "p4"),
        (False, "p5"),
    ]
    # m_b: I, V, V, V, V (same events, reversed)
    sequence_b = [
        (False, "p5"), (True, "p4"), (True, "p3"), (True, "p2"),
        (True, "p1"),
    ]
    for validated, proj in sequence_a:
        update_confidence(m_a, validated, project_id=proj, model_name="claude-sonnet")
    for validated, proj in sequence_b:
        update_confidence(m_b, validated, project_id=proj, model_name="claude-sonnet")

    assert m_a.alpha == pytest.approx(m_b.alpha, abs=0.001)
    assert m_a.beta == pytest.approx(m_b.beta, abs=0.001)
    assert m_a.confidence_score == pytest.approx(m_b.confidence_score, abs=0.001)
