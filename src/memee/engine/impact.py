"""Impact measurement: prove that organizational learning has REAL effects.

Not just "confidence went up" but:
- Agent changed behavior after receiving a warning
- Fewer iterations needed because pattern was known
- Decision was better because history was available
- Code was different (provably) because of memory

Every impact event is tracked with evidence chain:
  trigger → memory shown → agent action → outcome measured
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Session, relationship

from memee.storage.models import Base, Memory, new_id, utcnow


class ImpactType(str, Enum):
    MISTAKE_AVOIDED = "mistake_avoided"       # Agent got warning, changed approach
    TIME_SAVED = "time_saved"                 # Pattern reuse saved iteration time
    DECISION_INFORMED = "decision_informed"   # Decision made with historical context
    CODE_CHANGED = "code_changed"             # Code diff proves behavior change
    KNOWLEDGE_REUSED = "knowledge_reused"     # Agent explicitly used a memory


class ImpactEvent(Base):
    """A single measurable impact of organizational memory."""
    __tablename__ = "impact_events"

    id = Column(String(36), primary_key=True, default=new_id)
    memory_id = Column(String(36), ForeignKey("memories.id"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id"))
    agent = Column(String(255))
    model = Column(String(100))
    impact_type = Column(String(30), nullable=False)

    # Evidence chain
    trigger = Column(Text)           # What triggered the memory lookup
    memory_shown = Column(Text)      # What was shown to the agent
    agent_action = Column(Text)      # What the agent did differently
    outcome = Column(Text)           # Measured result

    # Quantified impact
    time_saved_minutes = Column(Float, default=0)
    iterations_saved = Column(Integer, default=0)
    severity_avoided = Column(String(20))   # If mistake avoided: severity level
    confidence_at_use = Column(Float)       # Memory confidence when used

    created_at = Column(DateTime, default=utcnow)

    memory = relationship("Memory")


def record_impact(
    session: Session,
    memory_id: str,
    impact_type: str,
    agent: str = "",
    model: str = "",
    project_id: str | None = None,
    trigger: str = "",
    memory_shown: str = "",
    agent_action: str = "",
    outcome: str = "",
    time_saved_minutes: float = 0,
    iterations_saved: int = 0,
    severity_avoided: str = "",
) -> ImpactEvent:
    """Record a measurable impact event."""
    memory = session.get(Memory, memory_id)
    conf = memory.confidence_score if memory else 0

    event = ImpactEvent(
        memory_id=memory_id,
        project_id=project_id,
        agent=agent,
        model=model,
        impact_type=impact_type,
        trigger=trigger,
        memory_shown=memory_shown,
        agent_action=agent_action,
        outcome=outcome,
        time_saved_minutes=time_saved_minutes,
        iterations_saved=iterations_saved,
        severity_avoided=severity_avoided,
        confidence_at_use=conf,
    )
    session.add(event)
    session.commit()
    return event


def get_impact_summary(session: Session) -> dict:
    """Aggregate impact metrics across all events.

    Honest-accounting policy for anti-pattern outcomes
    --------------------------------------------------
    Historically this function reported a single ``mistakes_avoided`` number
    that was effectively "anti-pattern warnings that were delivered to a
    project". A reviewer flagged that as dishonest: a delivered warning is
    not proof that a real mistake was prevented. We now split the counter
    into three, going from loosest to strictest:

    * ``warnings_shown`` — every time a critical/high anti-pattern memory is
      linked to a project via ``project_memories`` (i.e. ``predict_warnings``
      or a similar push surfaced the AP). Counted regardless of outcome.
    * ``warnings_acknowledged`` — links where ``applied = True`` AND
      ``outcome`` is NOT NULL. The agent actually took an action and recorded
      a result.
    * ``mistakes_avoided`` — links where ``applied = True`` AND
      ``outcome = "avoided"`` AND ``outcome_evidence_type IS NOT NULL``.
      Without a concrete evidence reference (diff, test_failure,
      review_comment, pr_url, or agent_feedback) we do not credit an
      avoidance. This is intentionally pessimistic.

    Backfill note (read this before running on an old DB): existing
    ``project_memories`` rows created before the ``outcome_evidence_type``
    column existed will have evidence_type = NULL. Under the new definition
    those rows are classified as ``warnings_shown`` only. They do NOT count
    as ``mistakes_avoided`` even if their old ``outcome`` field said
    "avoided" — we would rather under-report than lie. There is no Alembic
    migration here; the column is nullable and SQLite simply returns NULL
    for older rows.
    """
    from sqlalchemy import func

    from memee.storage.models import AntiPattern, ProjectMemory

    events = session.query(ImpactEvent).all()

    total_time_saved = sum(e.time_saved_minutes or 0 for e in events)
    total_iterations_saved = sum(e.iterations_saved or 0 for e in events)

    by_type = {}
    for t in ImpactType:
        type_events = [e for e in events if e.impact_type == t.value]
        if type_events:
            by_type[t.value] = {
                "count": len(type_events),
                "time_saved": sum(e.time_saved_minutes or 0 for e in type_events),
                "iterations_saved": sum(e.iterations_saved or 0 for e in type_events),
            }

    severity_counts = {}
    for e in events:
        if e.severity_avoided:
            severity_counts[e.severity_avoided] = severity_counts.get(e.severity_avoided, 0) + 1

    # ── Honest AP-outcome counters, read from project_memories ──
    # warnings_shown: every AP link delivered to a project.
    warnings_shown = (
        session.query(func.count(ProjectMemory.memory_id))
        .join(AntiPattern, AntiPattern.memory_id == ProjectMemory.memory_id)
        .scalar()
        or 0
    )

    # warnings_acknowledged: agent touched it AND recorded an outcome.
    warnings_acknowledged = (
        session.query(func.count(ProjectMemory.memory_id))
        .join(AntiPattern, AntiPattern.memory_id == ProjectMemory.memory_id)
        .filter(ProjectMemory.applied.is_(True))
        .filter(ProjectMemory.outcome.isnot(None))
        .scalar()
        or 0
    )

    # mistakes_avoided: strict — needs evidence_type set.
    mistakes_avoided = (
        session.query(func.count(ProjectMemory.memory_id))
        .join(AntiPattern, AntiPattern.memory_id == ProjectMemory.memory_id)
        .filter(ProjectMemory.applied.is_(True))
        .filter(ProjectMemory.outcome == "avoided")
        .filter(ProjectMemory.outcome_evidence_type.isnot(None))
        .scalar()
        or 0
    )

    # Unique memories that had impact
    impactful_memories = len(set(e.memory_id for e in events))

    # Unique agents that benefited
    agents_helped = len(set(e.agent for e in events if e.agent))

    # Average confidence of memories when used
    confs = [e.confidence_at_use for e in events if e.confidence_at_use]
    avg_conf_at_use = sum(confs) / len(confs) if confs else 0

    # ROI estimate: time saved vs time to build knowledge
    # Assume each memory took ~5 min to record
    total_memories = session.query(func.count(Memory.id)).scalar() or 1
    investment_minutes = total_memories * 5
    roi = total_time_saved / investment_minutes if investment_minutes > 0 else 0

    if not events and warnings_shown == 0:
        return {
            "total_events": 0,
            "warnings_shown": 0,
            "warnings_acknowledged": 0,
            "mistakes_avoided": 0,
        }

    return {
        "total_events": len(events),
        "total_time_saved_minutes": round(total_time_saved, 1),
        "total_time_saved_hours": round(total_time_saved / 60, 1),
        "total_iterations_saved": total_iterations_saved,
        "by_type": by_type,
        "severities_avoided": severity_counts,
        "warnings_shown": warnings_shown,
        "warnings_acknowledged": warnings_acknowledged,
        "mistakes_avoided": mistakes_avoided,
        "impactful_memories": impactful_memories,
        "agents_helped": agents_helped,
        "avg_confidence_at_use": round(avg_conf_at_use, 3),
        "roi_multiplier": round(roi, 1),
        "investment_minutes": investment_minutes,
    }
