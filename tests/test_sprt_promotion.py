"""Tests for v2.4.2 SPRT-gated canon promotion + deprecation (Tier 1.3).

Wald's Sequential Probability Ratio Test replaces the pre-v2.4.2
``confidence_score >= 0.85 AND validation_count >= 10`` gate with
calibratable Type-I/II error rates. Math anchor: Wald 1945,
Wolfowitz 1948 (optimality), Schönbrodt et al. BRM 2017 (sequential
Bayes factor — same family under flat priors).

Hypotheses + boundaries:
  H0: p ≤ 0.5 (chance). H1: p ≥ 0.85 (canon quality).
  α = β = 0.05. Upper boundary log(19) ≈ +2.944.
  Lower boundary -log(19) ≈ -2.944.

LLR = n_val · log(0.85/0.5) + n_inv · log(0.15/0.5)
    = n_val · 0.5306         + n_inv · (-1.2040)

Tests pin:
  * Boundary math + signal classification (promote/continue/deprecate)
  * Independence from diversity gates (multi-project, LLM quarantine)
  * Backward-compat path via ``MEMEE_SPRT_PROMOTION=0``
  * Auto-deprecation under SPRT lower-boundary cross
"""

from __future__ import annotations


from memee.engine.confidence import (
    _SPRT_LOWER,
    _SPRT_UPPER,
    _sprt_enabled,
    _sprt_promotion_signal,
    evaluate_maturity,
    sprt_log_likelihood_ratio,
)
from memee.storage.models import MaturityLevel, Memory, MemoryType


def _mk(alpha: float, beta: float, **kw) -> Memory:
    """Build a Memory with controlled α/β and sensible defaults."""
    val_count = kw.pop("validation_count", int(max(0, alpha - 1)))
    inv_count = kw.pop("invalidation_count", int(max(0, beta - 1)))
    return Memory(
        type=MemoryType.PATTERN.value,
        title=kw.pop("title", "t"),
        content="c",
        tags=[],
        maturity=kw.pop("maturity", MaturityLevel.VALIDATED.value),
        confidence_score=alpha / (alpha + beta),
        alpha=alpha,
        beta=beta,
        source_type=kw.pop("source_type", "human"),
        validation_count=val_count,
        invalidation_count=inv_count,
        project_count=kw.pop("project_count", 5),
        model_count=kw.pop("model_count", 2),
        application_count=kw.pop("application_count", 0),
        **kw,
    )


# ── LLR math ──


def test_fresh_memory_has_zero_llr():
    """Beta(1, 1) has zero evidence — LLR = 0, continue."""
    m = _mk(alpha=1.0, beta=1.0)
    assert sprt_log_likelihood_ratio(m) == 0.0
    assert _sprt_promotion_signal(m) == "continue"


def test_strong_evidence_crosses_upper_boundary():
    """Ten validations with no invalidations → LLR ≈ 5.31 > 2.944."""
    m = _mk(alpha=11.0, beta=1.0)
    llr = sprt_log_likelihood_ratio(m)
    assert llr > _SPRT_UPPER
    assert _sprt_promotion_signal(m) == "promote"


def test_negative_evidence_crosses_lower_boundary():
    """Five invalidations with no validations → LLR ≈ -6.02 < -2.944."""
    m = _mk(alpha=1.0, beta=6.0)
    llr = sprt_log_likelihood_ratio(m)
    assert llr < _SPRT_LOWER
    assert _sprt_promotion_signal(m) == "deprecate"


def test_mixed_evidence_stays_in_continue_zone():
    """Six validations + one invalidation balances out below upper boundary."""
    m = _mk(alpha=7.0, beta=2.0)
    llr = sprt_log_likelihood_ratio(m)
    assert _SPRT_LOWER < llr < _SPRT_UPPER
    assert _sprt_promotion_signal(m) == "continue"


def test_boundaries_symmetric_at_05_alpha():
    """With α = β = 0.05, the upper and lower SPRT thresholds are
    symmetric about zero (both log(19) in magnitude). Pins the
    convention so a future config change is loud."""
    import math
    assert _SPRT_UPPER == -_SPRT_LOWER
    assert _SPRT_UPPER == math.log(19)


# ── Promotion integration with evaluate_maturity ──


def test_sprt_promotes_canon_at_6_strong_validations(session):
    """Beta(7, 1) crosses upper boundary (LLR ≈ 3.18 > 2.944). Combined
    with sufficient project_count, the memory reaches CANON via the
    statistical gate even though ``validation_count = 6`` is below
    the legacy ``canon_min_validations = 10`` threshold."""
    m = _mk(
        alpha=7.0, beta=1.0,
        project_count=5, model_count=2,
        source_type="human",
        application_count=6,
    )
    session.add(m)
    session.commit()
    assert evaluate_maturity(m) == MaturityLevel.CANON.value


def test_sprt_blocked_when_project_count_insufficient(session):
    """Diversity gate is INDEPENDENT of SPRT. Strong statistical evidence
    on a single-project memory does NOT reach canon — multi-project
    breadth is still required. Prevents one project from promoting its
    own opinion past Wald boundaries."""
    m = _mk(
        alpha=21.0, beta=1.0,
        project_count=1, model_count=2,
        source_type="human",
        application_count=20,
    )
    session.add(m)
    session.commit()
    assert evaluate_maturity(m) != MaturityLevel.CANON.value


def test_sprt_deprecation_fires_at_lower_boundary(session):
    """Wald lower boundary trips auto-deprecation even when the
    historical confidence_score is mid-range and touch_events haven't
    reached the legacy deprecation threshold. Catches "canon that
    started failing under new conditions" the ratio rule misses."""
    m = _mk(
        alpha=1.0, beta=6.0,
        project_count=5, model_count=2,
        source_type="human",
        application_count=0,
        # invalidation_count comes from _mk's β-1 = 5
    )
    session.add(m)
    session.commit()
    assert evaluate_maturity(m) == MaturityLevel.DEPRECATED.value


def test_llm_quarantine_still_required_under_sprt(session):
    """LLM-sourced memory with strong SPRT evidence + 1 model family
    still does NOT reach canon. Cross-model evidence is independent
    of the SPRT gate (defends against the self-reinforcement loop)."""
    m = _mk(
        alpha=21.0, beta=1.0,
        project_count=5, model_count=1,    # single family
        source_type="llm",                  # quarantined
        application_count=20,
    )
    session.add(m)
    session.commit()
    assert evaluate_maturity(m) != MaturityLevel.CANON.value


def test_llm_quarantine_lifted_with_cross_model_under_sprt(session):
    """LLM source + strong SPRT + ≥2 model families → CANON."""
    m = _mk(
        alpha=21.0, beta=1.0,
        project_count=5, model_count=2,
        source_type="llm",
        application_count=20,
    )
    session.add(m)
    session.commit()
    assert evaluate_maturity(m) == MaturityLevel.CANON.value


# ── Feature flag fallback ──


def test_feature_flag_default_is_on(monkeypatch):
    monkeypatch.delenv("MEMEE_SPRT_PROMOTION", raising=False)
    assert _sprt_enabled() is True


def test_feature_flag_off_with_zero(monkeypatch):
    monkeypatch.setenv("MEMEE_SPRT_PROMOTION", "0")
    assert _sprt_enabled() is False


def test_feature_flag_off_with_alternate_falsy_values(monkeypatch):
    """Common ops conventions for "off"."""
    for val in ("false", "no", "off", "FALSE", "NO", "OFF"):
        monkeypatch.setenv("MEMEE_SPRT_PROMOTION", val)
        assert _sprt_enabled() is False, f"value {val!r} should disable SPRT"


def test_legacy_gate_used_when_sprt_disabled(session, monkeypatch):
    """``MEMEE_SPRT_PROMOTION=0`` falls back to the pre-v2.4.2 thresholds:
    ``c >= 0.85 AND validation_count >= 10``. A memory that would have
    been promoted by SPRT (6 validations) but not by the legacy gate
    stays at VALIDATED."""
    monkeypatch.setenv("MEMEE_SPRT_PROMOTION", "0")
    m = _mk(
        alpha=7.0, beta=1.0,
        project_count=5, model_count=2,
        source_type="human",
        application_count=6,
        validation_count=6,  # below legacy 10
    )
    session.add(m)
    session.commit()
    assert evaluate_maturity(m) != MaturityLevel.CANON.value


def test_legacy_gate_still_promotes_when_validation_count_high(session, monkeypatch):
    """With SPRT off, the legacy gate still works at validation_count >= 10
    + confidence >= 0.85."""
    monkeypatch.setenv("MEMEE_SPRT_PROMOTION", "0")
    m = _mk(
        alpha=21.0, beta=1.0,
        project_count=5, model_count=2,
        source_type="human",
        application_count=10,
        validation_count=20,
    )
    session.add(m)
    session.commit()
    assert evaluate_maturity(m) == MaturityLevel.CANON.value
