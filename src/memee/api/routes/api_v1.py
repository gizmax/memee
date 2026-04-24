"""REST API v1 — JSON endpoints for dashboard and integrations."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from memee.storage.database import get_session, init_db
from memee.storage.models import (
    AntiPattern,
    LearningSnapshot,
    Memory,
    MemoryConnection,
    MemoryValidation,
    Project,
    ProjectMemory,
)

router = APIRouter(tags=["api"])


def get_db() -> Session:
    engine = init_db()
    return get_session(engine)


@router.get("/stats")
def get_stats(session: Session = Depends(get_db)):
    """Overall org stats."""
    total = session.query(func.count(Memory.id)).scalar() or 0
    if total == 0:
        return {"empty": True}

    maturity = dict(
        session.query(Memory.maturity, func.count(Memory.id))
        .group_by(Memory.maturity).all()
    )
    types = dict(
        session.query(Memory.type, func.count(Memory.id))
        .group_by(Memory.type).all()
    )
    avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0
    projects = session.query(func.count(Project.id)).scalar() or 0
    connections = session.query(func.count(MemoryConnection.source_id)).scalar() or 0

    canon_r = maturity.get("canon", 0) / total
    valid_r = maturity.get("validated", 0) / total
    org_iq = canon_r * 30 + valid_r * 25 + avg_conf * 20 + 0.5 * 15 + 0.97 * 10

    return {
        "total_memories": total,
        "projects": projects,
        "connections": connections,
        "avg_confidence": round(avg_conf, 3),
        "maturity": maturity,
        "types": types,
        "org_iq": round(org_iq, 1),
    }


@router.get("/memories")
def list_memories(
    type: str = "",
    maturity: str = "",
    limit: int = 50,
    session: Session = Depends(get_db),
):
    """List memories with optional filters."""
    q = session.query(Memory).order_by(Memory.confidence_score.desc())
    if type:
        q = q.filter(Memory.type == type)
    if maturity:
        q = q.filter(Memory.maturity == maturity)

    memories = q.limit(limit).all()
    return [
        {
            "id": m.id[:8],
            "type": m.type,
            "maturity": m.maturity,
            "title": m.title,
            "confidence": round(m.confidence_score, 3),
            "validations": m.validation_count,
            "projects": m.project_count,
            "tags": m.tags or [],
            "agent": m.source_agent,
        }
        for m in memories
    ]


@router.get("/anti-patterns")
def list_anti_patterns(session: Session = Depends(get_db)):
    """List all anti-patterns with details."""
    results = (
        session.query(Memory, AntiPattern)
        .join(AntiPattern, AntiPattern.memory_id == Memory.id)
        .order_by(Memory.confidence_score.desc())
        .all()
    )
    return [
        {
            "id": m.id[:8],
            "title": m.title,
            "severity": ap.severity,
            "trigger": ap.trigger,
            "consequence": ap.consequence,
            "alternative": ap.alternative,
            "confidence": round(m.confidence_score, 3),
            "occurrences": ap.occurrences,
            "tags": m.tags or [],
        }
        for m, ap in results
    ]


@router.get("/timeline")
def get_timeline(session: Session = Depends(get_db)):
    """Validation timeline — all validation events over time."""
    validations = (
        session.query(MemoryValidation)
        .order_by(MemoryValidation.created_at)
        .all()
    )
    return [
        {
            "memory_id": v.memory_id[:8],
            "project_id": v.project_id[:8] if v.project_id else None,
            "validated": v.validated,
            "evidence": v.evidence,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in validations
    ]


@router.get("/projects")
def list_projects(session: Session = Depends(get_db)):
    """List projects with memory counts."""
    projects = session.query(Project).all()
    result = []
    for p in projects:
        mem_count = (
            session.query(func.count(ProjectMemory.memory_id))
            .filter(ProjectMemory.project_id == p.id)
            .scalar()
        )
        result.append({
            "id": p.id[:8],
            "name": p.name,
            "stack": p.stack or [],
            "tags": p.tags or [],
            "memories": mem_count,
        })
    return sorted(result, key=lambda x: -x["memories"])


@router.get("/agents")
def list_agents(session: Session = Depends(get_db)):
    """Agent effectiveness stats."""
    agents = (
        session.query(Memory.source_agent, func.count(Memory.id))
        .filter(Memory.source_agent.isnot(None))
        .group_by(Memory.source_agent)
        .all()
    )

    result = []
    for agent_name, count in agents:
        # Avg confidence of memories created by this agent
        avg_conf = (
            session.query(func.avg(Memory.confidence_score))
            .filter(Memory.source_agent == agent_name)
            .scalar() or 0
        )
        # Count by type
        type_counts = dict(
            session.query(Memory.type, func.count(Memory.id))
            .filter(Memory.source_agent == agent_name)
            .group_by(Memory.type)
            .all()
        )
        result.append({
            "name": agent_name,
            "memories": count,
            "avg_confidence": round(avg_conf, 3),
            "patterns": type_counts.get("pattern", 0),
            "anti_patterns": type_counts.get("anti_pattern", 0),
            "decisions": type_counts.get("decision", 0),
            "lessons": type_counts.get("lesson", 0),
        })

    return sorted(result, key=lambda x: -x["avg_confidence"])


@router.get("/confidence-distribution")
def confidence_distribution(session: Session = Depends(get_db)):
    """Confidence score distribution in buckets."""
    memories = session.query(Memory.confidence_score).all()
    buckets = defaultdict(int)
    for (conf,) in memories:
        bucket = round(conf * 10) / 10
        buckets[f"{bucket:.1f}"] += 1
    return dict(sorted(buckets.items()))


@router.get("/experiments")
def list_experiments_api(session: Session = Depends(get_db)):
    """List all autoresearch experiments."""
    from memee.engine.research import list_experiments

    return list_experiments(session)


@router.get("/experiments/{experiment_id}")
def get_experiment_api(experiment_id: str, session: Session = Depends(get_db)):
    """Get experiment details with trajectory."""
    from memee.engine.research import get_experiment_status
    from memee.storage.models import ResearchExperiment

    # Try full or partial ID
    exp = session.get(ResearchExperiment, experiment_id)
    if not exp:
        exps = session.query(ResearchExperiment).filter(
            ResearchExperiment.id.like(f"{experiment_id}%")
        ).all()
        if len(exps) == 1:
            experiment_id = exps[0].id

    return get_experiment_status(session, experiment_id)


@router.get("/meta-learning")
def get_meta_learning_api(session: Session = Depends(get_db)):
    """Meta-learning insights across all experiments."""
    from memee.engine.research import get_meta_learning

    return get_meta_learning(session)


@router.get("/retrieval")
def get_retrieval_metrics(session: Session = Depends(get_db)):
    """Retrieval health: hit@1, hit@3, acceptance rate, p50 latency.

    Rolled up over 1 / 7 / 30 day trailing windows. Also returns a 30-day
    daily sparkline of hit@1 so the dashboard can render a small trend line
    without a separate request.

    Notes on honesty:
      * ``hit_at_1``: share of events where ``accepted_memory_id == top_memory_id``.
      * ``hit_at_3``: events where the caller recorded ``position_of_accepted < 3``.
        Acceptances without a known position do NOT count toward hit@3.
      * ``time_to_solution_p50_ms`` is a **proxy** — we use search latency for
        events that ended in an acceptance. Until Memee emits a real "solved"
        event from the agent, this understates true time-to-solution.
    """
    from memee.engine.telemetry import compute_retrieval_metrics, hit_at_1_sparkline

    windows = {
        "day_1": compute_retrieval_metrics(session, window_days=1),
        "day_7": compute_retrieval_metrics(session, window_days=7),
        "day_30": compute_retrieval_metrics(session, window_days=30),
    }
    return {
        "windows": windows,
        "hit_at_1_sparkline_30d": hit_at_1_sparkline(session, days=30),
        "notes": {
            "time_to_solution_p50_ms": (
                "Proxy: search latency for events that ended in acceptance. "
                "No separate 'solved' event is emitted yet."
            ),
            "hit_at_3": (
                "Counts acceptances with a known 0-based position < 3. "
                "Acceptances without position are excluded."
            ),
        },
    }


@router.get("/impact")
def get_impact(session: Session = Depends(get_db)):
    """Honest impact counters: warnings shown / acknowledged / avoided.

    Mirrors ``memee.engine.impact.get_impact_summary``. The ``mistakes_avoided``
    figure only counts project_memories rows with ``outcome = 'avoided'`` AND
    ``outcome_evidence_type`` set (diff, test_failure, review_comment, pr_url,
    or agent_feedback) — no evidence, no avoidance.
    """
    from memee.engine.impact import get_impact_summary

    return get_impact_summary(session)


@router.get("/snapshots")
def get_snapshots(session: Session = Depends(get_db)):
    """Learning snapshots over time."""
    snapshots = (
        session.query(LearningSnapshot)
        .order_by(LearningSnapshot.snapshot_date)
        .all()
    )
    return [
        {
            "date": s.snapshot_date.isoformat() if s.snapshot_date else None,
            "total": s.total_memories,
            "canon": s.canon_memories,
            "hypothesis": s.hypothesis_memories,
            "deprecated": s.deprecated_memories,
            "avg_confidence": round(s.avg_confidence, 3) if s.avg_confidence else 0,
            "learning_rate": round(s.learning_rate, 3) if s.learning_rate else 0,
        }
        for s in snapshots
    ]
