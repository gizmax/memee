"""v2.4.6 regression test for Layer 0 / Layer 0.7 deduplication.

A critical anti-pattern that ALSO qualifies for Layer 0.7 (wide HDI +
stale ``last_retrieved``) was being shown TWICE in the same briefing:
once under Layer 0's ``•`` glyph and again under Layer 0.7's ``?``
glyph. A real install demonstrated the bug in the session-reminder —
the user saw three ``• Never use eval()`` lines immediately followed
by two ``? Never use eval()`` lines for the same memories.

Fix in ``engine/router.py``: ``select_verify_candidates`` results now
filter against the IDs of memories Layer 0 already surfaced, not just
against Layer 0.5's ``pinned_ids``.

Test pinned here in a separate file because the v2.3.1 test_repetition.py
acquired a ``com.apple.provenance`` macOS sandbox attribute mid-session
and became immutable until either a `chmod` with elevated privileges
or a file recreation. Splitting the regression into its own file is
the path of least resistance and keeps the v2.4.6 history audit-clean.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memee.engine.router import smart_briefing
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryType,
)


def test_critical_ap_does_not_double_render_in_layer07(session, monkeypatch):
    """The exact failure mode the real install demonstrated:
    a CANON-tier critical anti-pattern with wide HDI + stale
    ``last_retrieved`` qualifies for *both* Layer 0 (critical APs)
    AND Layer 0.7 (re-checking canon). Before v2.4.6 it rendered
    twice; now it renders once, under Layer 0."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_VERIFY", raising=False)
    monkeypatch.delenv("MEMEE_NO_LAYER0", raising=False)

    now = datetime.now(timezone.utc)
    title = "eval()/exec() on user input -> code execution risk"
    m = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title=title,
        content="critical security anti-pattern",
        tags=["security", "python"],
        maturity=MaturityLevel.CANON.value,
        confidence_score=0.85,
        alpha=5.0,  # wide-HDI signal → Layer 0.7 candidate
        beta=1.0,
        source_type="human",
        last_applied_at=now - timedelta(days=60),
        half_life=14.0,
        last_retrieved=now - timedelta(days=60),
    )
    session.add(m)
    session.flush()
    session.add(
        AntiPattern(
            memory_id=m.id,
            severity="critical",
            trigger="eval",
            consequence="arbitrary code execution",
        )
    )
    session.commit()

    result = smart_briefing(session, task="security audit", token_budget=500)
    # Title must appear EXACTLY ONCE across the whole briefing — under
    # Layer 0's "• " bullet, not also under Layer 0.7's "? ".
    occurrences = result.count(title)
    assert occurrences == 1, (
        f"Layer 0 / Layer 0.7 duplication: title appeared {occurrences} "
        f"times.\n---\n{result}\n---"
    )
    # And specifically: the ``? <title>`` form does NOT appear.
    assert f"? {title}" not in result, (
        f"Layer 0.7 surfaced a critical AP under '?' glyph.\n"
        f"---\n{result}\n---"
    )


def test_layer07_still_surfaces_non_critical_canon(session, monkeypatch):
    """The dedup must not over-fire: a regular CANON pattern (not
    flagged as critical anti-pattern) should still be eligible for
    Layer 0.7 when its retrievability is low."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_VERIFY", raising=False)

    now = datetime.now(timezone.utc)
    title = "Use connection pooling for SQLAlchemy"
    session.add(
        Memory(
            type=MemoryType.PATTERN.value,
            title=title,
            content="connection pool defaults",
            tags=["python", "sqlalchemy"],
            maturity=MaturityLevel.CANON.value,
            confidence_score=0.85,
            alpha=5.0,
            beta=1.0,
            source_type="human",
            last_applied_at=now - timedelta(days=60),
            half_life=14.0,
            last_retrieved=now - timedelta(days=60),
        )
    )
    session.commit()

    result = smart_briefing(session, task="database refactor", token_budget=500)
    assert "Re-checking canon:" in result
    assert title in result
