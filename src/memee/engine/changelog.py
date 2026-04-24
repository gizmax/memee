"""Org Memory Diff: what changed in organizational knowledge this week.

Like git diff, but for knowledge:
  - What was LEARNED (new patterns)
  - What was PROVEN (promoted to validated/canon)
  - What AGED (deprecated, invalidated)
  - What was CHALLENGED (contradictions found)
  - What SPREAD (cross-project propagation)

Usage: memee changelog --days 7
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryConnection,
    MemoryType,
    MemoryValidation,
)


def generate_changelog(
    session: Session,
    days: int = 7,
) -> dict:
    """Generate knowledge changelog for the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # New memories
    new_memories = (
        session.query(Memory)
        .filter(Memory.created_at >= cutoff)
        .order_by(Memory.created_at.desc())
        .all()
    )

    new_patterns = [m for m in new_memories if m.type == MemoryType.PATTERN.value]
    new_anti_patterns = [m for m in new_memories if m.type == MemoryType.ANTI_PATTERN.value]
    new_decisions = [m for m in new_memories if m.type == MemoryType.DECISION.value]
    new_lessons = [m for m in new_memories if m.type == MemoryType.LESSON.value]

    # Promoted (maturity changed upward recently — check via high confidence + recent validation)
    recent_validations = (
        session.query(MemoryValidation)
        .filter(MemoryValidation.created_at >= cutoff, MemoryValidation.validated == True)  # noqa: E712
        .all()
    )
    validated_ids = {v.memory_id for v in recent_validations}
    promoted = []
    for mid in validated_ids:
        m = session.get(Memory, mid)
        if m and m.maturity in (MaturityLevel.VALIDATED.value, MaturityLevel.CANON.value):
            promoted.append(m)

    # Deprecated
    deprecated = (
        session.query(Memory)
        .filter(
            Memory.maturity == MaturityLevel.DEPRECATED.value,
            Memory.deprecated_at >= cutoff,
        )
        .all()
    )

    # Contradictions (new connections of type "contradicts")
    contradictions = (
        session.query(MemoryConnection)
        .filter(
            MemoryConnection.relationship_type == "contradicts",
            MemoryConnection.created_at >= cutoff,
        )
        .all()
    )

    # Stats
    total = session.query(func.count(Memory.id)).scalar() or 0
    canon = session.query(func.count(Memory.id)).filter(
        Memory.maturity == MaturityLevel.CANON.value
    ).scalar() or 0
    avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0

    return {
        "period_days": days,
        "new_patterns": len(new_patterns),
        "new_anti_patterns": len(new_anti_patterns),
        "new_decisions": len(new_decisions),
        "new_lessons": len(new_lessons),
        "total_new": len(new_memories),
        "promoted": len(promoted),
        "deprecated": len(deprecated),
        "contradictions": len(contradictions),
        "total_memories": total,
        "canon_count": canon,
        "avg_confidence": round(avg_conf, 3),
        "details": {
            "learned": [
                {"title": m.title, "type": m.type, "confidence": m.confidence_score,
                 "agent": m.source_agent}
                for m in new_memories[:20]
            ],
            "proven": [
                {"title": m.title, "maturity": m.maturity,
                 "confidence": round(m.confidence_score, 3)}
                for m in promoted[:10]
            ],
            "aged": [
                {"title": m.title, "reason": m.deprecated_reason}
                for m in deprecated[:10]
            ],
            "challenged": [
                {
                    "a": session.get(Memory, c.source_id).title if session.get(Memory, c.source_id) else "?",
                    "b": session.get(Memory, c.target_id).title if session.get(Memory, c.target_id) else "?",
                }
                for c in contradictions[:10]
            ],
        },
    }


def format_changelog(data: dict) -> str:
    """Format changelog as readable text."""
    lines = []
    lines.append(f"=== KNOWLEDGE CHANGELOG (last {data['period_days']} days) ===")
    lines.append("")

    # Summary
    lines.append(f"  New: {data['total_new']} memories "
                 f"({data['new_patterns']} patterns, "
                 f"{data['new_anti_patterns']} anti-patterns, "
                 f"{data['new_decisions']} decisions)")
    lines.append(f"  Promoted: {data['promoted']} (reached validated/canon)")
    lines.append(f"  Deprecated: {data['deprecated']} (aged or invalidated)")
    lines.append(f"  Contradictions: {data['contradictions']} found")
    lines.append(f"  Total: {data['total_memories']} memories, "
                 f"{data['canon_count']} canon, "
                 f"avg conf {data['avg_confidence']}")
    lines.append("")

    # Learned
    learned = data["details"].get("learned", [])
    if learned:
        lines.append("  LEARNED:")
        for m in learned[:10]:
            agent = f" by {m['agent']}" if m.get("agent") else ""
            lines.append(f"    + [{m['type']}] {m['title']}{agent}")
        lines.append("")

    # Proven
    proven = data["details"].get("proven", [])
    if proven:
        lines.append("  PROVEN:")
        for m in proven[:5]:
            lines.append(f"    ↑ {m['title']} → {m['maturity']} ({m['confidence']})")
        lines.append("")

    # Aged
    aged = data["details"].get("aged", [])
    if aged:
        lines.append("  AGED:")
        for m in aged[:5]:
            reason = m.get("reason", "")[:60] if m.get("reason") else ""
            lines.append(f"    ↓ {m['title']} — {reason}")
        lines.append("")

    # Challenged
    challenged = data["details"].get("challenged", [])
    if challenged:
        lines.append("  CHALLENGED:")
        for c in challenged[:5]:
            lines.append(f"    ⚡ \"{c['a'][:40]}\" vs \"{c['b'][:40]}\"")
        lines.append("")

    return "\n".join(lines)
