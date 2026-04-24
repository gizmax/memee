"""Tests for memory lifecycle: aging, expiry, promotion."""

from datetime import datetime, timedelta, timezone

from memee.config import settings
from memee.engine.lifecycle import (
    deprecate_memory,
    get_expiring_memories,
    run_aging_cycle,
)
from memee.storage.models import MaturityLevel, Memory, MemoryType


def test_expire_old_hypothesis(session):
    """Unvalidated hypotheses expire after TTL."""
    old_date = datetime.now(timezone.utc) - timedelta(days=100)
    memory = Memory(
        type=MemoryType.OBSERVATION.value,
        title="Old untested observation",
        content="Something I noticed once.",
        created_at=old_date,
        validation_count=0,
    )
    session.add(memory)
    session.commit()

    stats = run_aging_cycle(session)
    assert stats["expired"] >= 1
    assert memory.maturity == MaturityLevel.DEPRECATED.value
    assert "Auto-expired" in (memory.deprecated_reason or "")


def test_keep_validated_hypothesis(session):
    """Hypotheses with validations don't expire even if old."""
    old_date = datetime.now(timezone.utc) - timedelta(days=100)
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Old but validated pattern",
        content="Tested and confirmed.",
        created_at=old_date,
        validation_count=2,
        application_count=2,
    )
    session.add(memory)
    session.commit()

    stats = run_aging_cycle(session)
    assert memory.maturity != MaturityLevel.DEPRECATED.value


def test_manual_deprecation(session):
    """Manual deprecation with reason."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Deprecated pattern",
        content="No longer applies.",
    )
    session.add(memory)
    session.commit()

    deprecate_memory(session, memory, "Technology changed, no longer relevant")
    assert memory.maturity == MaturityLevel.DEPRECATED.value
    assert memory.deprecated_reason == "Technology changed, no longer relevant"


def test_auto_deprecate_low_confidence(session):
    """Low confidence after many applications triggers deprecation."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Consistently wrong pattern",
        content="Keeps failing.",
        confidence_score=0.15,
        application_count=5,
    )
    session.add(memory)
    session.commit()

    stats = run_aging_cycle(session)
    assert memory.maturity == MaturityLevel.DEPRECATED.value


def test_promotion_during_aging(session):
    """Aging cycle promotes memories when thresholds are met."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Good pattern",
        content="Works well.",
        confidence_score=0.75,
        application_count=5,
        project_count=3,
        validation_count=5,
        maturity=MaturityLevel.TESTED.value,
    )
    session.add(memory)
    session.commit()

    stats = run_aging_cycle(session)
    assert memory.maturity == MaturityLevel.VALIDATED.value
    assert stats["promoted"] >= 1


def test_get_expiring_memories_filters_by_age(session):
    """`within_days` must actually scope the result set by age.

    An expiring-window of 30 days should return the hypothesis about to
    expire (already older than ttl-30) and skip the brand-new one, whose
    expiry is far outside the window.
    """
    ttl = settings.hypothesis_ttl_days
    now = datetime.now(timezone.utc)

    # Old hypothesis: created 5 days before TTL runs out → within the
    # 30-day warning window.
    old = Memory(
        type=MemoryType.PATTERN.value,
        title="Old hypothesis nearing expiry",
        content="Content for aged hypothesis.",
        created_at=now - timedelta(days=ttl - 5),
        validation_count=0,
        maturity=MaturityLevel.HYPOTHESIS.value,
    )
    # Fresh hypothesis: just created. Expiry is ttl days away, well
    # outside a 30-day warning window.
    fresh = Memory(
        type=MemoryType.PATTERN.value,
        title="Fresh hypothesis",
        content="Brand new content.",
        created_at=now,
        validation_count=0,
        maturity=MaturityLevel.HYPOTHESIS.value,
    )
    session.add_all([old, fresh])
    session.commit()

    expiring = get_expiring_memories(session, within_days=30)
    expiring_ids = {m.id for m in expiring}

    assert old.id in expiring_ids
    assert fresh.id not in expiring_ids
