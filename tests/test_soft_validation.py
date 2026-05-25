"""v2.4.11 — `memee cite --confirm` is a soft validation.

An explicit confirm is weak-but-real positive evidence: the agent applied
the memory and it helped. It bumps the Beta posterior's α by a fractional
weight so confidence rises gently with use — but never touches the
validation_count / project_count gates that promote to validated/canon, so
a single-user install cannot inflate maturity past "tested".
"""

from __future__ import annotations

import pytest

from memee import config
from memee.engine.citations import confirm_citation
from memee.engine.confidence import soft_validate
from memee.storage.models import MaturityLevel, Memory, MemoryType


def _mk(session, org, **kw):
    m = Memory(
        organization_id=org.id,
        type=kw.get("type", MemoryType.PATTERN.value),
        title=kw.get("title", "Use connection pooling in production"),
        content=kw.get("content", "Pool connections to avoid exhaustion"),
        tags=kw.get("tags", ["database"]),
        maturity=kw.get("maturity", MaturityLevel.HYPOTHESIS.value),
    )
    # Start from the uniform prior.
    m.alpha = kw.get("alpha", 1.0)
    m.beta = kw.get("beta", 1.0)
    m.confidence_score = kw.get("confidence_score", 0.5)
    session.add(m)
    session.flush()
    return m


# ── soft_validate unit ──


def test_soft_validate_bumps_alpha_by_weight(session, org):
    m = _mk(session, org)
    a0 = m.alpha
    soft_validate(m, weight=0.5)
    assert m.alpha == pytest.approx(a0 + 0.5)


def test_soft_validate_raises_confidence(session, org):
    m = _mk(session, org)
    c0 = m.confidence_score
    c1 = soft_validate(m)
    assert c1 > c0
    assert m.confidence_score == pytest.approx(c1)


def test_soft_validate_default_weight_from_settings(session, org):
    m = _mk(session, org)
    a0 = m.alpha
    soft_validate(m)
    assert m.alpha == pytest.approx(a0 + config.settings.soft_validation_weight)


def test_soft_validate_does_not_touch_hard_gates(session, org):
    """The validated/canon gates must stay reserved for cross-project."""
    m = _mk(session, org)
    vc0 = m.validation_count or 0
    pc0 = m.project_count or 0
    soft_validate(m)
    assert (m.validation_count or 0) == vc0
    assert (m.project_count or 0) == pc0


def test_soft_validate_cannot_reach_validated_or_canon_single_project(session, org):
    """Even after many confirms, a 1-project memory caps below validated."""
    m = _mk(session, org)
    for _ in range(50):
        soft_validate(m)
    # Confidence may be high, but project_count is 0 → never validated/canon.
    assert m.maturity not in (
        MaturityLevel.VALIDATED.value,
        MaturityLevel.CANON.value,
    )


def test_soft_validate_negative_weight_clamped(session, org):
    m = _mk(session, org)
    a0 = m.alpha
    soft_validate(m, weight=-5.0)
    assert m.alpha == pytest.approx(a0)  # clamped to 0


# ── confirm_citation integration ──


def test_confirm_citation_raises_confidence_and_returns_delta(session, org):
    m = _mk(session, org)
    c0 = m.confidence_score
    result = confirm_citation(session, m, note="applied in feature/x")
    assert result["confidence_after"] > result["confidence_before"]
    assert result["confidence_before"] == pytest.approx(round(c0, 4))
    assert result["application_count"] == 1
    assert "maturity" in result
    # Evidence chain still records the citation.
    assert any(e.get("kind") == "citation" for e in (m.evidence_chain or []))


def test_confirm_citation_promotes_hypothesis_to_tested(session, org):
    """First confirm sets application_count≥1 → 'tested' on re-evaluation."""
    m = _mk(session, org, maturity=MaturityLevel.HYPOTHESIS.value)
    confirm_citation(session, m)
    assert m.maturity == MaturityLevel.TESTED.value


def test_repeated_confirms_compound_confidence(session, org):
    m = _mk(session, org)
    c0 = m.confidence_score
    confirm_citation(session, m)
    c1 = m.confidence_score
    confirm_citation(session, m)
    c2 = m.confidence_score
    assert c1 > c0
    assert c2 > c1
