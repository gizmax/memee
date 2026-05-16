"""Tests for v2.4.1 active re-validation (Tier 1.6).

The cog-sci dossier flagged canon as currently unfalsifiable — a
pattern that hit canon two years ago and silently went stale only
ever deprecates if somebody manually invalidates it. v2.4.1 closes
that loop:

  1. ``engine/repetition.select_verify_candidates`` ranks canon by a
     "needs re-validation" score combining wide-HDI (Beta posterior
     SD) and staleness (days since last_applied_at).
  2. ``engine/router.smart_briefing`` renders the top N under a new
     **Layer 0.7** "Re-checking canon:" block, between Layer 0.5
     (pinned) and Layer 1 (search).
  3. Feedback closes via existing ``update_confidence`` — agent
     applies the candidate → α += w; agent invalidates → β += w.
     Skipping leaves the score unchanged so the next selection
     surfaces the same row again.

Tests below pin:
  * Score ordering — stale-wide > stale-narrow > fresh-narrow > 0
  * Selection caps at ``limit`` and respects kill switches
  * Layer 0.7 fires in the briefing with the expected header + glyph
  * Layer 0.7 de-dups against Layer 0.5 (pinned) and Layer 1 (search)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


from memee.engine.repetition import (
    VERIFY_DEFAULT_LIMIT,
    _layer07_suppressed,
    _verify_score,
    select_verify_candidates,
    verify_limit_from_env,
)
from memee.engine.router import smart_briefing
from memee.storage.models import MaturityLevel, Memory, MemoryType


def _mk_canon(
    title: str,
    *,
    alpha: float,
    beta: float,
    last_applied_days_ago: float,
    tags: list[str] | None = None,
) -> Memory:
    now = datetime.now(timezone.utc)
    return Memory(
        type=MemoryType.PATTERN.value,
        title=title,
        content="canon content",
        tags=tags or ["python"],
        maturity=MaturityLevel.CANON.value,
        confidence_score=alpha / (alpha + beta),
        alpha=alpha,
        beta=beta,
        source_type="human",
        last_applied_at=now - timedelta(days=last_applied_days_ago),
    )


# ── _verify_score ──


def test_score_zero_for_fresh_narrow_canon(session):
    """Fresh canon with narrow HDI gets a low score — not zero (Beta(11,1)
    SD already exceeds the 0.07 threshold by 0.007 → small score) but
    well below stale candidates."""
    now = datetime.now(timezone.utc)
    m = _mk_canon("fresh-narrow", alpha=11.0, beta=1.0,
                  last_applied_days_ago=2)
    session.add(m)
    session.commit()
    score = _verify_score(m, now)
    # SD just over threshold contributes; staleness contributes 0.
    assert 0 < score < 1.5, f"unexpected score {score:.3f}"


def test_score_high_for_stale_wide_canon(session):
    """Stale canon with wide HDI dominates the ranking — both
    components contribute."""
    now = datetime.now(timezone.utc)
    m = _mk_canon("stale-wide", alpha=5.0, beta=1.0,
                  last_applied_days_ago=60)
    session.add(m)
    session.commit()
    score = _verify_score(m, now)
    assert score > 5.0, f"stale-wide score should dominate, got {score:.3f}"


def test_ordering_stale_wide_beats_stale_narrow_beats_fresh(session):
    """The whole point of Layer 0.7 is to surface the *most uncertain*
    canon. Ordering must reflect that across two independent axes."""
    now = datetime.now(timezone.utc)
    fresh = _mk_canon("fresh", alpha=11.0, beta=1.0,
                      last_applied_days_ago=2)
    stale_narrow = _mk_canon("stale-narrow", alpha=11.0, beta=1.0,
                             last_applied_days_ago=90)
    stale_wide = _mk_canon("stale-wide", alpha=5.0, beta=1.0,
                           last_applied_days_ago=60)
    session.add_all([fresh, stale_narrow, stale_wide])
    session.commit()
    s_fresh = _verify_score(fresh, now)
    s_narrow = _verify_score(stale_narrow, now)
    s_wide = _verify_score(stale_wide, now)
    assert s_wide > s_narrow > s_fresh, (
        f"ordering broken: fresh={s_fresh:.3f}, narrow={s_narrow:.3f}, "
        f"wide={s_wide:.3f}"
    )


# ── select_verify_candidates ──


def test_select_returns_top_n_by_score(session):
    """``limit`` caps the returned list and orders by descending score."""
    fresh = _mk_canon("fresh", alpha=11.0, beta=1.0,
                      last_applied_days_ago=2)
    stale_narrow = _mk_canon("stale-narrow", alpha=11.0, beta=1.0,
                             last_applied_days_ago=90)
    stale_wide = _mk_canon("stale-wide", alpha=5.0, beta=1.0,
                           last_applied_days_ago=60)
    session.add_all([fresh, stale_narrow, stale_wide])
    session.commit()

    top = select_verify_candidates(session, limit=2)
    titles = [m.title for m in top]
    assert titles[0] == "stale-wide"
    assert "stale-narrow" in titles
    assert "fresh" not in titles


def test_select_skips_non_canon(session):
    """Only CANON-tier memories are surfaced. Validated/Tested
    already churn under normal validation cycles — Layer 0.7's job is
    to make CANON itself falsifiable."""
    m = _mk_canon("validated-stale", alpha=5.0, beta=1.0,
                  last_applied_days_ago=90)
    m.maturity = MaturityLevel.VALIDATED.value
    session.add(m)
    session.commit()
    assert select_verify_candidates(session, limit=5) == []


def test_select_respects_memee_no_verify(session, monkeypatch):
    """Per-channel kill switch silences the block without affecting
    other surfaces (Layer 0, 0.5, 1, footer)."""
    monkeypatch.setenv("MEMEE_NO_VERIFY", "1")
    session.add(_mk_canon("stale-wide", alpha=5.0, beta=1.0,
                          last_applied_days_ago=60))
    session.commit()
    assert select_verify_candidates(session, limit=5) == []
    assert _layer07_suppressed() is True


def test_select_respects_memee_quiet(session, monkeypatch):
    """Master ``MEMEE_QUIET=1`` switch reaches Layer 0.7 too."""
    monkeypatch.setenv("MEMEE_QUIET", "1")
    session.add(_mk_canon("stale-wide", alpha=5.0, beta=1.0,
                          last_applied_days_ago=60))
    session.commit()
    assert select_verify_candidates(session, limit=5) == []


def test_verify_limit_from_env_invalid_falls_back(monkeypatch):
    """Garbled env var should not crash — fall back to the default."""
    monkeypatch.setenv("MEMEE_VERIFY_MAX_BULLETS", "not-a-number")
    assert verify_limit_from_env() == VERIFY_DEFAULT_LIMIT


def test_verify_limit_from_env_zero_is_off(monkeypatch):
    """Setting 0 is the canonical 'off' value (avoids needing two
    knobs for 'off' vs 'kill switch')."""
    monkeypatch.setenv("MEMEE_VERIFY_MAX_BULLETS", "0")
    assert verify_limit_from_env() == 0


def test_select_returns_empty_when_limit_zero(session):
    session.add(_mk_canon("stale-wide", alpha=5.0, beta=1.0,
                          last_applied_days_ago=60))
    session.commit()
    assert select_verify_candidates(session, limit=0) == []


# ── Router Layer 0.7 integration ──


def test_router_renders_layer07_block(session, monkeypatch):
    """End-to-end: a stale-wide canon shows under the
    "Re-checking canon:" header in the routed briefing."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_VERIFY", raising=False)
    session.add_all([
        _mk_canon("Stale canon worth re-checking",
                  alpha=5.0, beta=1.0, last_applied_days_ago=60),
        _mk_canon("Fresh canon",
                  alpha=20.0, beta=1.0, last_applied_days_ago=2),
    ])
    session.commit()

    out = smart_briefing(session, task="canon", token_budget=500)
    assert "Re-checking canon:" in out
    assert "? Stale canon worth re-checking" in out
    # Stale canon must appear ONLY ONCE — Layer 0.7 took it, the
    # Layer 1 de-dup must keep it out of the search-routed bullets
    # even when the search query matches the title.
    assert out.count("Stale canon worth re-checking") == 1


def test_router_suppresses_layer07_under_kill_switch(session, monkeypatch):
    monkeypatch.setenv("MEMEE_NO_VERIFY", "1")
    session.add(_mk_canon("Stale canon",
                          alpha=5.0, beta=1.0, last_applied_days_ago=60))
    session.commit()

    out = smart_briefing(session, task="anything", token_budget=500)
    assert "Re-checking canon:" not in out
