"""Memory lifecycle management: aging, expiry, promotion, deprecation."""

from datetime import timedelta, timezone

from sqlalchemy.orm import Session

from memee.config import settings
from memee.engine.confidence import evaluate_maturity
from memee.storage.models import MaturityLevel, Memory, utcnow


def run_aging_cycle(session: Session) -> dict:
    """Run a full aging cycle across all memories.

    1. Expire old hypotheses that were never validated
    2. Auto-deprecate low-confidence memories with sufficient applications
    3. Re-evaluate maturity for all active memories

    Returns stats dict: {expired, deprecated, promoted, unchanged}
    """
    now = utcnow()
    stats = {"expired": 0, "deprecated": 0, "promoted": 0, "unchanged": 0}

    active_memories = (
        session.query(Memory)
        .filter(Memory.maturity != MaturityLevel.DEPRECATED.value)
        .all()
    )

    for memory in active_memories:
        old_maturity = memory.maturity

        # 1. Expire stale hypotheses
        if memory.maturity == MaturityLevel.HYPOTHESIS.value:
            ttl = timedelta(days=settings.hypothesis_ttl_days)
            created = memory.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created and (now - created) > ttl:
                if memory.validation_count == 0:
                    memory.maturity = MaturityLevel.DEPRECATED.value
                    memory.deprecated_at = now
                    memory.deprecated_reason = (
                        f"Auto-expired: hypothesis not validated within "
                        f"{settings.hypothesis_ttl_days} days"
                    )
                    stats["expired"] += 1
                    continue

        # 1b. Auto-archive: never-used memories older than 60 days
        if memory.application_count == 0 and memory.validation_count == 0:
            created = memory.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created and (now - created) > timedelta(days=60):
                memory.maturity = MaturityLevel.DEPRECATED.value
                memory.deprecated_at = now
                memory.deprecated_reason = "Auto-archived: never used or validated in 60 days"
                stats["expired"] += 1
                continue

        # 1c. Stale detection: high invalidation ratio = dying knowledge
        if memory.invalidation_count and memory.validation_count:
            inv_ratio = memory.invalidation_count / (memory.validation_count + memory.invalidation_count)
            if inv_ratio > 0.6 and memory.application_count >= 3:
                memory.maturity = MaturityLevel.DEPRECATED.value
                memory.deprecated_at = now
                memory.deprecated_reason = (
                    f"Auto-deprecated: {inv_ratio:.0%} invalidation rate "
                    f"({memory.invalidation_count} of "
                    f"{memory.validation_count + memory.invalidation_count} validations)"
                )
                stats["deprecated"] += 1
                continue

        # 2. Re-evaluate maturity
        new_maturity = evaluate_maturity(memory)
        if new_maturity != old_maturity:
            memory.maturity = new_maturity
            if new_maturity == MaturityLevel.DEPRECATED.value:
                memory.deprecated_at = now
                memory.deprecated_reason = (
                    f"Auto-deprecated: confidence {memory.confidence_score:.2f} "
                    f"below threshold after {memory.application_count} applications"
                )
                stats["deprecated"] += 1
            else:
                stats["promoted"] += 1
        else:
            stats["unchanged"] += 1

    session.commit()
    return stats


def deprecate_memory(
    session: Session, memory: Memory, reason: str
) -> Memory:
    """Manually deprecate a memory with a reason."""
    memory.maturity = MaturityLevel.DEPRECATED.value
    memory.deprecated_at = utcnow()
    memory.deprecated_reason = reason
    session.commit()
    return memory


def get_expiring_memories(session: Session, within_days: int = 7) -> list[Memory]:
    """Get hypothesis memories that will expire within N days.

    A hypothesis expires at ``created_at + hypothesis_ttl_days``. We return
    the ones whose expiry is within ``within_days`` from now — i.e. those
    already at least ``(ttl - within_days)`` old. Without this age filter
    the function returned every unvalidated hypothesis regardless of age.
    """
    now = utcnow()
    ttl = timedelta(days=settings.hypothesis_ttl_days)
    warn_threshold = now - (ttl - timedelta(days=within_days))

    return (
        session.query(Memory)
        .filter(
            Memory.maturity == MaturityLevel.HYPOTHESIS.value,
            Memory.validation_count == 0,
            Memory.created_at < warn_threshold,
        )
        .all()
    )
