"""Tests for v2.0.5 correctness fixes.

Three areas:

1. ``init_db`` creates ``impact_events`` (the table the engine needs).
2. ``post_task_review`` no longer credits MISTAKE_AVOIDED for warnings the
   agent ignored.
3. ``impact_summary`` returns one stable shape regardless of whether
   there are events.
"""

from __future__ import annotations

from sqlalchemy import inspect


# ── 1. init_db creates impact_events ──────────────────────────────────────


def test_init_db_creates_impact_events(tmp_path):
    """A fresh DB created via ``init_db`` must contain the impact_events
    table — without it, the API and the feedback loop crash on the first
    ``record_impact`` call."""
    from memee.storage.database import get_engine, init_db

    engine = init_db(get_engine(tmp_path / "fresh.db"))
    tables = set(inspect(engine).get_table_names())
    assert "impact_events" in tables
    # Sanity: a few core tables we always expect.
    assert "memories" in tables
    assert "anti_patterns" in tables


def test_record_impact_works_on_fresh_db(tmp_path):
    """End-to-end: write to impact_events on a fresh DB through
    ``record_impact`` — proves the table is queryable, not just declared."""
    from memee.engine.impact import ImpactType, record_impact
    from memee.storage.database import get_engine, get_session, init_db
    from memee.storage.models import Memory, MemoryType, Organization

    engine = init_db(get_engine(tmp_path / "fresh.db"))
    session = get_session(engine)
    org = Organization(name="org")
    session.add(org)
    session.flush()
    mem = Memory(
        title="Memory",
        content="Test memory content for impact recording",
        type=MemoryType.PATTERN.value,
        organization_id=org.id,
    )
    session.add(mem)
    session.commit()

    event = record_impact(
        session, mem.id,
        ImpactType.KNOWLEDGE_REUSED.value,
        agent="claude", model="claude-sonnet",
    )
    assert event.id
    session.commit()


# ── 2. feedback no longer lies about MISTAKE_AVOIDED ──────────────────────


def _seed_anti_pattern(session):
    """Plant one anti-pattern + memory in the DB so post_task_review can
    detect a "violation" in our crafted diff."""
    from memee.storage.models import (
        AntiPattern, Memory, MemoryType, Organization, Severity,
    )

    org = session.query(Organization).first()
    if org is None:
        org = Organization(name="test-org")
        session.add(org)
        session.flush()

    mem = Memory(
        title="Never use eval() on untrusted input",
        content="Trigger: parsing user-supplied expressions. Use ast.literal_eval instead.",
        type=MemoryType.ANTI_PATTERN.value,
        organization_id=org.id,
    )
    session.add(mem)
    session.flush()

    ap = AntiPattern(
        memory_id=mem.id,
        trigger="eval()",
        consequence="Remote code execution",
        alternative="ast.literal_eval",
        severity=Severity.HIGH.value,
    )
    session.add(ap)
    session.commit()
    return mem


def test_violation_with_success_does_not_credit_mistake_avoided(session):
    """The bug: v2.0.4 and earlier counted ``warnings_violated`` with
    ``outcome="success"`` as MISTAKE_AVOIDED — a number that read like a
    win for a behaviour the agent had actually ignored. v2.0.5 maps that
    case to WARNING_INEFFECTIVE instead. Either way: never AVOIDED."""
    from memee.engine.feedback import post_task_review
    from memee.engine.impact import ImpactEvent, ImpactType

    _seed_anti_pattern(session)

    diff = """
diff --git a/parse.py b/parse.py
+ result = eval(user_input)
"""
    post_task_review(
        session, diff_text=diff, agent="claude", outcome="success"
    )

    events = session.query(ImpactEvent).all()
    types = {e.impact_type for e in events}

    assert ImpactType.MISTAKE_AVOIDED.value not in types, (
        "v2.0.4 regression: a violated warning + success counts as "
        "MISTAKE_AVOIDED. Should be WARNING_INEFFECTIVE."
    )
    # Be specific about what we DO expect: ineffective on success.
    assert ImpactType.WARNING_INEFFECTIVE.value in types


def test_violation_with_failure_still_records_mistake_made(session):
    """Failure path is unchanged: violation + failure → MISTAKE_MADE."""
    from memee.engine.feedback import post_task_review
    from memee.engine.impact import ImpactEvent, ImpactType

    _seed_anti_pattern(session)

    diff = """
diff --git a/parse.py b/parse.py
+ result = eval(user_input)
"""
    post_task_review(
        session, diff_text=diff, agent="claude", outcome="failure"
    )

    events = session.query(ImpactEvent).all()
    types = {e.impact_type for e in events}

    assert ImpactType.MISTAKE_MADE.value in types
    assert ImpactType.MISTAKE_AVOIDED.value not in types


# ── 3. impact_summary returns one stable shape ────────────────────────────


_REQUIRED_KEYS = frozenset({
    "total_events",
    "total_time_saved_minutes",
    "total_time_saved_hours",
    "total_iterations_saved",
    "by_type",
    "severities_avoided",
    "warnings_shown",
    "warnings_shown_unique",
    "warnings_acknowledged",
    "warnings_acknowledged_unique",
    "mistakes_avoided",
    "mistakes_avoided_unique",
    "mistakes_made",
    "impactful_memories",
    "agents_helped",
    "avg_confidence_at_use",
    "roi_multiplier",
    "investment_minutes",
})


def test_impact_summary_shape_on_empty_db(session):
    """Empty DB must return the same key set as a populated one — no
    consumer should have to defensive-branch on missing keys."""
    from memee.engine.impact import get_impact_summary

    out = get_impact_summary(session)
    missing = _REQUIRED_KEYS - set(out)
    assert not missing, f"empty summary missing keys: {sorted(missing)}"
    # And nothing extra.
    extra = set(out) - _REQUIRED_KEYS
    assert not extra, f"empty summary has unexpected keys: {sorted(extra)}"
    assert out["total_events"] == 0
    assert out["total_time_saved_minutes"] == 0.0
    assert out["by_type"] == {}


def test_impact_summary_shape_matches_after_event(session):
    """After recording one event, the key set is unchanged."""
    from memee.engine.impact import ImpactType, get_impact_summary, record_impact
    from memee.storage.models import Memory, MemoryType, Organization

    org = session.query(Organization).first()
    mem = Memory(
        title="Pattern",
        content="Some pattern content for impact testing",
        type=MemoryType.PATTERN.value,
        organization_id=org.id,
    )
    session.add(mem)
    session.commit()
    record_impact(
        session, mem.id, ImpactType.KNOWLEDGE_REUSED.value,
        agent="claude", time_saved_minutes=10,
    )
    session.commit()

    out = get_impact_summary(session)
    assert set(out) == _REQUIRED_KEYS
    assert out["total_events"] == 1
    assert out["total_time_saved_minutes"] == 10.0
