"""Memory lifecycle management: aging, expiry, promotion, deprecation."""

from datetime import timedelta, timezone

from sqlalchemy.orm import Session

from memee.config import settings
from memee.engine.confidence import evaluate_maturity
from memee.storage.models import MaturityLevel, Memory, MemoryConnection, utcnow


def _build_canon_dependent_index(session: Session) -> set[str]:
    """Return memory ids that any CANON memory currently depends on.

    R9: deprecation should NOT auto-fire on a memory that holds up a CANON
    pattern. Removing the prerequisite while CANON still references it
    breaks the chain. Returns the set of target_ids guarded by this rule.
    """
    rows = (
        session.query(MemoryConnection.target_id)
        .join(Memory, Memory.id == MemoryConnection.source_id)
        .filter(
            MemoryConnection.relationship_type == "depends_on",
            Memory.maturity == MaturityLevel.CANON.value,
        )
        .all()
    )
    return {tid for (tid,) in rows}


def _supersedes_proposals(session: Session) -> list[dict]:
    """R9: collect (winner, loser) pairs for memories targeted by a
    ``supersedes`` edge. We do NOT auto-deprecate the loser — supersedes
    inference can have false positives, and auto-flipping kills the loser
    before a human verifies. Instead, return them as digest entries the
    operator can act on (or `memee deprecate <id>` manually).
    """
    rows = (
        session.query(
            MemoryConnection.source_id,
            MemoryConnection.target_id,
        )
        .filter(MemoryConnection.relationship_type == "supersedes")
        .all()
    )
    if not rows:
        return []
    ids = {tid for _src, tid in rows} | {sid for sid, _tgt in rows}
    mems = session.query(Memory).filter(Memory.id.in_(ids)).all()
    by_id = {m.id: m for m in mems}
    proposals: list[dict] = []
    for src, tgt in rows:
        winner = by_id.get(src)
        loser = by_id.get(tgt)
        if not (winner and loser):
            continue
        if loser.maturity == MaturityLevel.DEPRECATED.value:
            continue  # already handled
        proposals.append(
            {
                "winner_id": winner.id,
                "winner_title": winner.title,
                "loser_id": loser.id,
                "loser_title": loser.title,
            }
        )
    return proposals


def run_aging_cycle(session: Session) -> dict:
    """Run a full aging cycle across all memories.

    1. Expire old hypotheses that were never validated
    2. Auto-deprecate low-confidence memories with sufficient applications
    3. Re-evaluate maturity for all active memories

    R9 changes:
    - Memories depended on by CANON entries are protected from auto-deprecation
      (other patterns reference them; killing them breaks the chain).
    - Supersession edges produce proposals in the digest, not auto-deprecation.
      Manual review keeps thrashing in check on small corpora where inference
      is loose.

    Returns stats dict: {expired, deprecated, promoted, unchanged,
    skipped_canon_dependent, supersession_proposals}
    """
    now = utcnow()
    stats: dict = {
        "expired": 0,
        "deprecated": 0,
        "promoted": 0,
        "unchanged": 0,
        "skipped_canon_dependent": 0,
        "supersession_proposals": [],
    }

    canon_dependents = _build_canon_dependent_index(session)
    stats["supersession_proposals"] = _supersedes_proposals(session)

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
                if memory.id in canon_dependents:
                    stats["skipped_canon_dependent"] += 1
                else:
                    memory.maturity = MaturityLevel.DEPRECATED.value
                    memory.deprecated_at = now
                    memory.deprecated_reason = "Auto-archived: never used or validated in 60 days"
                    stats["expired"] += 1
                continue

        # 1c. Stale detection: high invalidation ratio = dying knowledge.
        # R12 P1: when ``MEMEE_CALIBRATED_CONFIDENCE`` is on, gate via the
        # Beta-Binomial posterior instead of the raw ratio. The raw ratio
        # is sample-size-blind — 1 invalidation against 2 validations
        # (ratio=0.33, posterior with Beta(2,2)=0.40) shouldn't fire the
        # same way 6 invalidations against 4 (ratio=0.6, posterior=0.43)
        # would. Default off; production stays on the raw rule until we
        # trust the curve.
        if memory.invalidation_count and memory.validation_count:
            from memee.engine.calibration import (
                beta_binomial_posterior,
                is_enabled as _calib_enabled,
            )

            inv_ratio = memory.invalidation_count / (
                memory.validation_count + memory.invalidation_count
            )
            if _calib_enabled():
                # Posterior > 0.4 fires deprecation. Equivalent in spirit
                # to the raw 0.6 ratio gate but smoother on small samples.
                posterior_invalid = 1.0 - beta_binomial_posterior(memory)
                fires = posterior_invalid > 0.4 and memory.application_count >= 3
                reason_suffix = (
                    f"Beta-Binomial posterior P(invalid|data)={posterior_invalid:.2f}"
                )
            else:
                fires = inv_ratio > 0.6 and memory.application_count >= 3
                reason_suffix = (
                    f"{inv_ratio:.0%} invalidation rate "
                    f"({memory.invalidation_count} of "
                    f"{memory.validation_count + memory.invalidation_count} validations)"
                )
            if fires:
                if memory.id in canon_dependents:
                    stats["skipped_canon_dependent"] += 1
                else:
                    memory.maturity = MaturityLevel.DEPRECATED.value
                    memory.deprecated_at = now
                    memory.deprecated_reason = f"Auto-deprecated: {reason_suffix}"
                    stats["deprecated"] += 1
                continue

        # 2. Re-evaluate maturity
        new_maturity = evaluate_maturity(memory)
        if new_maturity != old_maturity:
            if (
                new_maturity == MaturityLevel.DEPRECATED.value
                and memory.id in canon_dependents
            ):
                # CANON depends on this; refuse auto-deprecation. Keep at the
                # same maturity until the canon dependent moves first.
                stats["skipped_canon_dependent"] += 1
                continue
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
