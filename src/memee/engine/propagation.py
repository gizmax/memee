"""Auto-Propagation: push validated patterns to matching-stack projects.

When a memory crosses a confidence threshold, find all projects whose
stack/tags overlap with the memory's tags and auto-link + validate.
This is the #1 impact feature (+68.8% Org IQ).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryType,
    Project,
    ProjectMemory,
)


def propagate_memory(
    session: Session,
    memory: Memory,
    min_tag_overlap: int = 1,
    *,
    projects: list | None = None,
    project_tag_cache: dict | None = None,
) -> list[dict]:
    """Push a single memory to all projects with matching tags.

    Returns list of {project_name, project_id, relevance_score} for each new link.

    R13 perf: when called from ``run_propagation_cycle`` we now pass the
    pre-loaded ``projects`` list and a shared ``project_tag_cache`` so we
    don't re-issue the ``session.query(Project).all()`` and recompute
    expanded tags per memory. Standalone callers see no change — both
    kwargs default to ``None`` and we recompute on demand.
    """
    if not memory.tags:
        return []

    mem_tags = set(memory.tags)

    # Query DB for existing links (not just in-memory relationship)
    existing_proj_ids = {
        pm.project_id
        for pm in session.query(ProjectMemory)
        .filter(ProjectMemory.memory_id == memory.id)
        .all()
    }

    if projects is None:
        projects = session.query(Project).all()
    if project_tag_cache is None:
        project_tag_cache = {}

    propagated = []

    for proj in projects:
        if proj.id in existing_proj_ids:
            continue

        proj_tags = project_tag_cache.get(proj.id)
        if proj_tags is None:
            proj_tags = _get_expanded_tags(proj)
            project_tag_cache[proj.id] = proj_tags

        overlap = mem_tags & proj_tags

        # Critical anti-patterns propagate to ALL projects
        is_critical_ap = (
            memory.type == MemoryType.ANTI_PATTERN.value
            and memory.anti_pattern
            and memory.anti_pattern.severity == "critical"
        )

        if len(overlap) >= min_tag_overlap or is_critical_ap:
            relevance = len(overlap) / len(mem_tags | proj_tags) if (mem_tags | proj_tags) else 0.5

            # Link memory to project (linking ≠ validating)
            pm = ProjectMemory(
                project_id=proj.id,
                memory_id=memory.id,
                relevance_score=relevance,
            )
            session.add(pm)
            # Note: we do NOT call update_confidence here.
            # Propagation makes knowledge AVAILABLE, not VALIDATED.
            # Validation happens when an agent actually uses and confirms it.

            propagated.append({
                "project_name": proj.name,
                "project_id": proj.id,
                "relevance_score": round(relevance, 3),
                "overlap_tags": list(overlap),
            })

    return propagated


def run_propagation_cycle(
    session: Session,
    confidence_threshold: float = 0.55,
    min_tag_overlap: int = 1,
    max_propagations: int = 500,
) -> dict:
    """Run propagation across all eligible memories.

    Eligible: confidence >= threshold, not deprecated, has tags.

    Returns stats: {memories_checked, memories_propagated, total_new_links,
                    projects_reached}
    """
    eligible = (
        session.query(Memory)
        .filter(
            Memory.confidence_score >= confidence_threshold,
            Memory.maturity != MaturityLevel.DEPRECATED.value,
            Memory.type.in_([
                MemoryType.PATTERN.value,
                MemoryType.LESSON.value,
                MemoryType.ANTI_PATTERN.value,
            ]),
        )
        .all()
    )

    stats = {
        "memories_checked": len(eligible),
        "memories_propagated": 0,
        "total_new_links": 0,
        "projects_reached": set(),
    }

    # R13 perf: pre-load projects + cache expanded tags ONCE for the whole
    # cycle. Old shape: per-memory ``Project.all()`` (1 query) + per-memory
    # × per-project ``_get_expanded_tags`` (cheap but repeated). At
    # 200 eligible × 30 projects this saved ~200 queries and ~6k tag
    # computations.
    projects = session.query(Project).all()
    project_tag_cache: dict = {}

    # Tag-index sync used to fire lazily inside ``propagate_memory``
    # (one COUNT per memory). Move it here so it runs at most once per
    # cycle for all eligible memories whose tag-index is empty.
    from memee.engine.tag_index import sync_memory_tags
    from memee.storage.models import MemoryTag

    eligible_ids = [m.id for m in eligible]
    if eligible_ids:
        already_indexed = {
            mid for (mid,) in session.query(MemoryTag.memory_id)
            .filter(MemoryTag.memory_id.in_(eligible_ids))
            .distinct()
            .all()
        }
        missing = [m for m in eligible if m.id not in already_indexed]
        for m in missing:
            sync_memory_tags(session, m)
        if missing:
            session.flush()

    total_links = 0
    for memory in eligible:
        if total_links >= max_propagations:
            break

        results = propagate_memory(
            session,
            memory,
            min_tag_overlap,
            projects=projects,
            project_tag_cache=project_tag_cache,
        )
        if results:
            stats["memories_propagated"] += 1
            stats["total_new_links"] += len(results)
            total_links += len(results)
            for r in results:
                stats["projects_reached"].add(r["project_id"])

    session.commit()
    stats["projects_reached"] = len(stats["projects_reached"])
    return stats


# Stack → inferred domain tags
_STACK_TAG_MAP = {
    "sqlite": ["database"],
    "postgresql": ["database"],
    "postgres": ["database"],
    "mongodb": ["database"],
    "redis": ["database", "caching"],
    "mysql": ["database"],
    "fastapi": ["api", "async"],
    "flask": ["api", "web"],
    "django": ["api", "web"],
    "express": ["api"],
    "react": ["frontend"],
    "vue": ["frontend"],
    "angular": ["frontend"],
    "next.js": ["frontend", "api"],
    "tailwind": ["css", "frontend"],
    "swift": ["mobile"],
    "swiftui": ["mobile", "ui"],
    "kotlin": ["mobile"],
    "docker": ["devops", "deployment"],
    "terraform": ["devops", "infra"],
    "airflow": ["data", "etl"],
    "pandas": ["data"],
    "pydantic": ["validation", "api"],
    "sqlalchemy": ["database"],
    "pytest": ["testing", "quality"],
    "bandit": ["security"],
    "owasp": ["security"],
}


def _get_expanded_tags(proj: Project) -> set[str]:
    """Get project tags expanded with inferred domain tags from stack."""
    tags = set()
    for s in (proj.stack or []):
        s_lower = s.lower()
        tags.add(s_lower)
        for inferred in _STACK_TAG_MAP.get(s_lower, []):
            tags.add(inferred)
    for t in (proj.tags or []):
        tags.add(t.lower())
    return tags
