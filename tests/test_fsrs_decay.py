"""Tests for v2.4.5 FSRS-light per-memory decay (Tier 1.5).

Each Memory carries its own ``half_life`` (days) and
``last_retrieved`` timestamp. Predicted retrievability is
``R(t) = 2^(-Δt/h)``. Successful validation stretches ``h``
proportional to ``(1 - R)`` — effortful recall is what consolidates,
per Wozniak SM-2 1990 and FSRS / Ye et al. KDD 2022. Invalidation
shrinks ``h`` to 0.6× — a wrong call is evidence the trace was
weaker than predicted.

Tests pin the math + the integration with ``evaluate_maturity`` /
Layer 0.7.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memee.engine.confidence import (
    FSRS_DEFAULT_HL,
    FSRS_GROWTH,
    FSRS_MAX_HL,
    FSRS_MIN_HL,
    FSRS_SHRINK,
    _fsrs_update_on_validation,
    predicted_retrievability,
    update_confidence,
)
from memee.storage.models import MaturityLevel, Memory, MemoryType


def _mk(half_life: float | None = None, **kw) -> Memory:
    now = datetime.now(timezone.utc)
    return Memory(
        type=MemoryType.PATTERN.value,
        title="t", content="c", tags=[],
        maturity=MaturityLevel.HYPOTHESIS.value,
        confidence_score=0.5,
        alpha=1.0, beta=1.0,
        source_type="human",
        half_life=half_life if half_life is not None else FSRS_DEFAULT_HL,
        last_retrieved=kw.pop("last_retrieved", None),
        last_applied_at=kw.pop("last_applied_at", None),
        last_validated_at=kw.pop("last_validated_at", None),
        created_at=kw.pop("created_at", now),
        **kw,
    )


# ── predicted_retrievability formula ──


def test_freshly_retrieved_memory_has_R_close_to_1():
    """``Δt → 0`` ⇒ ``R → 1``."""
    now = datetime.now(timezone.utc)
    m = _mk(half_life=14.0, last_retrieved=now)
    assert predicted_retrievability(m, now=now) == pytest.approx(1.0, abs=1e-6)


def test_one_half_life_old_memory_has_R_half():
    """``Δt = h`` ⇒ ``R = 0.5`` (definition of half-life)."""
    now = datetime.now(timezone.utc)
    m = _mk(half_life=14.0, last_retrieved=now - timedelta(days=14))
    assert predicted_retrievability(m, now=now) == pytest.approx(0.5, abs=0.001)


def test_two_half_lives_old_memory_has_R_quarter():
    now = datetime.now(timezone.utc)
    m = _mk(half_life=14.0, last_retrieved=now - timedelta(days=28))
    assert predicted_retrievability(m, now=now) == pytest.approx(0.25, abs=0.001)


def test_fallback_chain_uses_last_applied_when_no_last_retrieved():
    """Legacy memories (pre-v2.4.5) have ``last_retrieved=None``.
    Fallback chain reads ``last_applied_at`` next so the decay signal
    still works."""
    now = datetime.now(timezone.utc)
    m = _mk(half_life=10.0, last_applied_at=now - timedelta(days=10))
    assert predicted_retrievability(m, now=now) == pytest.approx(0.5, abs=0.001)


def test_fallback_chain_uses_created_when_nothing_else():
    """A memory that was inserted but never validated or retrieved
    falls all the way to ``created_at``."""
    now = datetime.now(timezone.utc)
    m = _mk(half_life=7.0, created_at=now - timedelta(days=7))
    assert predicted_retrievability(m, now=now) == pytest.approx(0.5, abs=0.001)


def test_future_timestamp_returns_R_1():
    """Clock skew or test seeding can make Δt negative. Clamp to 1.0,
    don't return >1 (which would break downstream math)."""
    now = datetime.now(timezone.utc)
    m = _mk(half_life=14.0, last_retrieved=now + timedelta(days=5))
    assert predicted_retrievability(m, now=now) == 1.0


def test_zero_half_life_returns_R_1():
    """Defensive: a corrupt row with ``h=0`` shouldn't divide by zero;
    treat as "no decay applies"."""
    now = datetime.now(timezone.utc)
    m = _mk(half_life=0.0, last_retrieved=now - timedelta(days=100))
    assert predicted_retrievability(m, now=now) == 1.0


# ── _fsrs_update_on_validation ──


def test_successful_validation_stretches_half_life():
    """Hot memory (R≈1) barely grows; cool memory (R≈0.4) grows ~1.3×."""
    now = datetime.now(timezone.utc)
    # Cool memory: ~1.5 half-lives old → R ≈ 0.354
    m = _mk(half_life=10.0, last_retrieved=now - timedelta(days=15))
    R_before = predicted_retrievability(m, now=now)
    _fsrs_update_on_validation(m, validated=True)
    expected_h = 10.0 * (1.0 + FSRS_GROWTH * (1.0 - R_before))
    assert m.half_life == pytest.approx(expected_h, abs=0.01)
    # last_retrieved is stamped to "now" (or close)
    assert m.last_retrieved is not None


def test_fresh_validation_barely_grows():
    """Memory just retrieved has R≈1, so the growth factor is tiny."""
    now = datetime.now(timezone.utc)
    m = _mk(half_life=14.0, last_retrieved=now)
    _fsrs_update_on_validation(m, validated=True)
    # 14 × (1 + 0.5 × 0) = 14
    assert m.half_life == pytest.approx(14.0, abs=0.01)


def test_invalidation_shrinks_half_life_by_06():
    """β-event multiplies half-life by FSRS_SHRINK regardless of R."""
    now = datetime.now(timezone.utc)
    m = _mk(half_life=20.0, last_retrieved=now - timedelta(days=2))
    _fsrs_update_on_validation(m, validated=False)
    assert m.half_life == pytest.approx(20.0 * FSRS_SHRINK, abs=0.01)


def test_half_life_clamped_at_minimum():
    """Repeated invalidations should never push below ``FSRS_MIN_HL``;
    deprecation belongs to SPRT, not exponential collapse."""
    m = _mk(half_life=FSRS_MIN_HL)
    for _ in range(10):
        _fsrs_update_on_validation(m, validated=False)
    assert m.half_life == FSRS_MIN_HL


def test_half_life_clamped_at_maximum():
    """A relentlessly-recalled cool memory should still cap at
    ``FSRS_MAX_HL``. Institutional memory isn't permastore."""
    m = _mk(half_life=FSRS_MAX_HL - 1.0)
    # Push artificially: pretend the recall was effortful (R≈0).
    for _ in range(50):
        # Force "cool" state by resetting last_retrieved into the past.
        m.last_retrieved = (
            (m.last_retrieved or datetime.now(timezone.utc))
            - timedelta(days=int(m.half_life * 3))
        )
        _fsrs_update_on_validation(m, validated=True)
    assert m.half_life == FSRS_MAX_HL


# ── Integration with update_confidence ──


def test_update_confidence_advances_half_life(session):
    """The full ``update_confidence`` path stamps ``last_retrieved``
    and updates ``half_life`` end to end."""
    m = _mk()
    session.add(m)
    session.commit()
    h_before = m.half_life
    last_before = m.last_retrieved

    update_confidence(m, validated=True, project_id="p1", model_name="gpt-5")

    # last_retrieved moved forward.
    assert m.last_retrieved is not None
    if last_before is not None:
        assert m.last_retrieved > last_before
    # half_life either grew or stayed the same (R≈1 on fresh memory).
    assert m.half_life >= h_before


# ── Layer 0.7 integration: low-R memories rank higher ──


def test_layer_07_uses_retrievability_to_rank_canon(session):
    """A canon memory with low predicted recall outranks a canon
    memory with high predicted recall, even if their HDI is identical.
    Pre-v2.4.5 this was implicit via the 30d cliff; v2.4.5 makes it
    per-memory."""
    now = datetime.now(timezone.utc)
    cool = Memory(
        type=MemoryType.PATTERN.value,
        title="cool-canon", content="c", tags=["x"],
        maturity=MaturityLevel.CANON.value,
        confidence_score=0.92, alpha=11.0, beta=1.0,
        source_type="human",
        half_life=14.0,
        last_retrieved=now - timedelta(days=30),  # > 2 half-lives
    )
    hot = Memory(
        type=MemoryType.PATTERN.value,
        title="hot-canon", content="c", tags=["x"],
        maturity=MaturityLevel.CANON.value,
        confidence_score=0.92, alpha=11.0, beta=1.0,
        source_type="human",
        half_life=14.0,
        last_retrieved=now - timedelta(days=1),
    )
    session.add_all([cool, hot])
    session.commit()

    from memee.engine.repetition import select_verify_candidates

    top = select_verify_candidates(session, limit=2)
    titles = [m.title for m in top]
    # Cool one must outrank hot one. Hot one might not even appear
    # (R > 0.85 + narrow HDI → score 0).
    assert "cool-canon" in titles
    if "hot-canon" in titles:
        assert titles.index("cool-canon") < titles.index("hot-canon")
