"""Predictive Anti-Pattern Push: proactive failure prevention.

When a project is registered or scanned, match its stack against all known
anti-patterns and push relevant warnings. 100% unique feature.

Instead of agents manually checking (pull), Memee pushes warnings (push).
Avoidance rate: 36% (pull) → 72% (push).

Budgeting (alert-fatigue defense):
    Hard per-project daily cap and hard org-wide daily cap enforce that
    *linked* ProjectMemory rows stay bounded. Warnings beyond the quota
    are still returned (sorted, ranked) but flagged ``suppressed=True``
    and never persisted. An audit list ``suppressed_warnings`` is attached
    to the returned list via an attribute and surfaced in
    ``scan_all_projects`` stats.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    Project,
    ProjectMemory,
)


_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


class _WarningList(list):
    """List subclass that can carry a ``suppressed_warnings`` audit trail.

    Keeps full backward compatibility with existing callers that expect a
    plain ``list[dict]`` of warnings, while newer callers can pull the
    suppression audit via ``.suppressed_warnings`` or ``.stats``.
    """

    suppressed_warnings: list
    stats: dict


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def scan_project_for_warnings(
    session: Session,
    project: Project,
    top_n: int = 10,
    min_severity: str = "high",
    min_confidence: float = 0.4,
    preview: bool = False,
    max_per_project_per_day: int = 3,
    max_per_org_per_day: int = 10,
) -> list[dict]:
    """Scan project stack against anti-patterns. Budgeted + severity-gated.

    Uses MemoryTag + ProjectTag indexes (SQL JOIN) instead of Python iteration.

    Parameters
    ----------
    top_n:
        Soft cap: how many top-ranked warnings appear in the returned list.
    max_per_project_per_day:
        Hard cap on new ProjectMemory links for this project in a rolling
        24h window (measured via ``ProjectMemory.applied_at``; rows with a
        null ``applied_at`` are stamped on creation below).
    max_per_org_per_day:
        Hard cap on new ProjectMemory links across *all* projects in the
        same organization in a rolling 24h window.

    Returns
    -------
    list[dict]
        Top-ranked warnings, each with a ``suppressed`` bool. Suppressed
        entries are not persisted. The returned list also exposes
        ``.suppressed_warnings`` (audit trail) and ``.stats`` (counters).
    """
    from memee.engine.propagation import _get_expanded_tags
    from memee.engine.tag_index import sync_project_tags
    from memee.storage.models import MemoryTag, ProjectTag

    proj_tags = _get_expanded_tags(project)
    out = _WarningList()
    out.suppressed_warnings = []
    out.stats = {
        "linked": 0,
        "suppressed": 0,
        "project_quota_remaining": 0,
        "org_quota_remaining": 0,
    }
    if not proj_tags:
        return out

    # Ensure project tags are indexed (lazy migration)
    if session.query(ProjectTag).filter(ProjectTag.project_id == project.id).count() == 0:
        sync_project_tags(session, project)
        session.flush()

    min_rank = _SEVERITY_RANK.get(min_severity, 3)
    tag_list = list(proj_tags)

    existing_ids = {
        row[0] for row in session.query(ProjectMemory.memory_id)
        .filter(ProjectMemory.project_id == project.id).all()
    }

    # Find candidate memory IDs via tag index — SQL does the heavy lifting.
    # Lazy rebuild: if MemoryTag is empty, sync from Memory.tags JSON.
    if session.query(MemoryTag).count() == 0:
        from memee.engine.tag_index import rebuild_all_tag_indexes
        rebuild_all_tag_indexes(session)

    tagged_ids = {
        row[0] for row in session.query(MemoryTag.memory_id)
        .filter(MemoryTag.tag.in_(tag_list)).distinct().all()
    }

    # Load only candidate anti-patterns (plus CRITICAL which apply to all)
    candidates_q = (
        session.query(Memory, AntiPattern)
        .join(AntiPattern, AntiPattern.memory_id == Memory.id)
        .filter(
            Memory.maturity != MaturityLevel.DEPRECATED.value,
            Memory.confidence_score >= min_confidence,
        )
    )
    if tagged_ids:
        # Also include critical regardless of tags
        candidates_q = candidates_q.filter(
            (Memory.id.in_(list(tagged_ids))) | (AntiPattern.severity == "critical")
        )
    else:
        # No tag matches — critical only
        candidates_q = candidates_q.filter(AntiPattern.severity == "critical")

    candidates = candidates_q.all()

    # ── Recency-decay lookup: which anti-patterns were *applied* to this
    # project in the last 7 days? Multiplicative penalty on their priority
    # so they don't re-surface too fast.
    now = _utcnow()
    seven_days_ago = now - timedelta(days=7)
    recent_applied_ids = {
        row[0] for row in session.query(ProjectMemory.memory_id)
        .filter(
            ProjectMemory.project_id == project.id,
            ProjectMemory.applied_at.isnot(None),
            ProjectMemory.applied_at >= seven_days_ago,
        ).all()
    }

    # Rank by relevance × severity × confidence × recency_decay
    scored = []
    for memory, ap in candidates:
        if memory.id in existing_ids:
            continue
        sev_rank = _SEVERITY_RANK.get(ap.severity or "medium", 2)
        is_critical = sev_rank == 4
        if sev_rank < min_rank and not is_critical:
            continue

        mem_tags = set(t.lower() for t in (memory.tags or []))
        overlap = mem_tags & proj_tags
        if not overlap and not is_critical:
            continue

        relevance = (
            len(overlap) / len(mem_tags | proj_tags)
            if (mem_tags | proj_tags) else 0.5
        )
        priority = sev_rank * memory.confidence_score * (relevance + 0.3)
        if memory.id in recent_applied_ids:
            priority *= 0.3  # recency decay — don't re-surface within 7d
        scored.append((priority, memory, ap, overlap, relevance))

    # Top N only
    scored.sort(key=lambda x: -x[0])
    scored = scored[:top_n]

    # ── Budget computation: rolling 24h window ──
    one_day_ago = now - timedelta(hours=24)

    project_linked_24h = (
        session.query(ProjectMemory)
        .filter(
            ProjectMemory.project_id == project.id,
            ProjectMemory.applied_at.isnot(None),
            ProjectMemory.applied_at >= one_day_ago,
        )
        .count()
    )
    project_quota_remaining = max(0, max_per_project_per_day - project_linked_24h)

    # Org-wide: join across projects in the same organization.
    org_linked_24h = (
        session.query(ProjectMemory)
        .join(Project, Project.id == ProjectMemory.project_id)
        .filter(
            Project.organization_id == project.organization_id,
            ProjectMemory.applied_at.isnot(None),
            ProjectMemory.applied_at >= one_day_ago,
        )
        .count()
    )
    org_quota_remaining = max(0, max_per_org_per_day - org_linked_24h)

    out.stats["project_quota_remaining"] = project_quota_remaining
    out.stats["org_quota_remaining"] = org_quota_remaining

    warnings = out
    new_links = []
    budget_left = min(project_quota_remaining, org_quota_remaining)

    for priority, memory, ap, overlap, relevance in scored:
        suppressed = False
        reason = None
        if preview:
            # Preview mode: rank but never link, not counted as suppressed
            # (no write would happen regardless).
            pass
        elif budget_left <= 0:
            suppressed = True
            # Attribute to whichever quota is currently exhausted.
            proj_used = len(new_links)
            if proj_used >= project_quota_remaining:
                reason = "project_quota"
            else:
                reason = "org_quota"

        entry = {
            "memory_id": memory.id,
            "title": memory.title,
            "severity": ap.severity if ap else "medium",
            "trigger": ap.trigger if ap else "",
            "consequence": ap.consequence if ap else "",
            "alternative": ap.alternative if ap else "",
            "confidence": memory.confidence_score,
            "relevance": round(relevance, 3),
            "matching_tags": list(overlap),
            "priority_score": round(priority, 4),
            "suppressed": suppressed,
        }
        warnings.append(entry)

        if suppressed:
            out.suppressed_warnings.append({
                "memory_id": memory.id,
                "reason": reason,
                "would_have_ranked": round(priority, 4),
            })
            out.stats["suppressed"] += 1
            continue

        if not preview:
            new_links.append(ProjectMemory(
                project_id=project.id,
                memory_id=memory.id,
                relevance_score=relevance,
                applied_at=now,  # stamp for rolling 24h budget tracking
            ))
            budget_left -= 1
            out.stats["linked"] += 1

    if new_links:
        session.bulk_save_objects(new_links)
        session.commit()

    return warnings


def scan_all_projects(session: Session) -> dict:
    """Scan all registered projects for anti-pattern matches.

    Returns stats: {projects_scanned, total_warnings, warnings_by_severity,
    suppressed_warnings, total_linked, total_suppressed}
    """
    projects = session.query(Project).all()
    stats = {
        "projects_scanned": 0,
        "total_warnings": 0,
        "total_linked": 0,
        "total_suppressed": 0,
        "warnings_by_severity": {"low": 0, "medium": 0, "high": 0, "critical": 0},
        "project_warnings": {},
        "suppressed_warnings": [],
    }

    for project in projects:
        warnings = scan_project_for_warnings(session, project)
        stats["projects_scanned"] += 1
        stats["total_warnings"] += len(warnings)
        stats["project_warnings"][project.name] = len(warnings)

        for w in warnings:
            sev = w.get("severity", "medium")
            if sev in stats["warnings_by_severity"]:
                stats["warnings_by_severity"][sev] += 1

        # Collect suppression audit from the enriched return value.
        sup = getattr(warnings, "suppressed_warnings", None) or []
        wstats = getattr(warnings, "stats", None) or {}
        stats["total_linked"] += wstats.get("linked", 0)
        stats["total_suppressed"] += wstats.get("suppressed", 0)
        if sup:
            stats["suppressed_warnings"].extend(
                {**item, "project_name": project.name} for item in sup
            )

    return stats
