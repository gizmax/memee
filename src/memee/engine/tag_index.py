"""Tag index sync: keep MemoryTag / ProjectTag in sync with JSON fields.

Propagation and predictive scan use these indexed tables instead of
iterating JSON columns in Python.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from memee.storage.models import Memory, MemoryTag, Project, ProjectTag


# Stack → inferred domain tags (mirrored from propagation.py for consistency)
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


def _expand_project_tags(project: Project) -> set[str]:
    """Expanded tag set for a project (stack → inferred + explicit tags)."""
    tags = set()
    for s in (project.stack or []):
        s_lower = s.lower()
        tags.add(s_lower)
        for inferred in _STACK_TAG_MAP.get(s_lower, []):
            tags.add(inferred)
    for t in (project.tags or []):
        tags.add(t.lower())
    return tags


def sync_memory_tags(session: Session, memory: Memory) -> None:
    """Write memory.tags JSON to the MemoryTag index table.

    The delete+insert pair is wrapped in a SAVEPOINT so a concurrent reader
    in another connection doesn't observe the empty window between the two.
    """
    with session.begin_nested():
        session.query(MemoryTag).filter(MemoryTag.memory_id == memory.id).delete()
        if memory.tags:
            for t in memory.tags:
                t_lower = (t or "").strip().lower()
                if t_lower:
                    session.add(MemoryTag(memory_id=memory.id, tag=t_lower))


def sync_project_tags(session: Session, project: Project) -> None:
    """Write expanded project tags to ProjectTag index."""
    with session.begin_nested():
        session.query(ProjectTag).filter(
            ProjectTag.project_id == project.id
        ).delete()
        for t in _expand_project_tags(project):
            session.add(ProjectTag(project_id=project.id, tag=t))


def rebuild_all_tag_indexes(session: Session) -> dict:
    """Rebuild tag indexes from scratch (for migrations / repair).

    Wrapped in a single SAVEPOINT so concurrent readers never observe the
    empty intermediate state between DELETE and INSERT. MemoryTag rebuilds
    are still slow at scale — this is a maintenance op, not a hot path.
    Do not call this from a request handler.
    """
    mem_count = 0
    proj_count = 0
    with session.begin_nested():
        session.query(MemoryTag).delete()
        session.query(ProjectTag).delete()
        session.flush()

        for m in session.query(Memory).all():
            if m.tags:
                for t in m.tags:
                    t_lower = (t or "").strip().lower()
                    if t_lower:
                        session.add(MemoryTag(memory_id=m.id, tag=t_lower))
                        mem_count += 1

        for p in session.query(Project).all():
            for t in _expand_project_tags(p):
                session.add(ProjectTag(project_id=p.id, tag=t))
                proj_count += 1

    session.commit()
    return {"memory_tags": mem_count, "project_tags": proj_count}


def find_memories_by_tags(
    session: Session,
    tags: set[str],
    min_overlap: int = 1,
    limit: int = 100,
) -> list[str]:
    """SQL-level tag lookup. Returns memory_ids with ≥min_overlap matching tags."""
    if not tags:
        return []
    tag_list = list(tags)
    # Use GROUP BY + COUNT to find memories with enough tag matches
    from sqlalchemy import func
    rows = (
        session.query(MemoryTag.memory_id, func.count(MemoryTag.tag).label("hits"))
        .filter(MemoryTag.tag.in_(tag_list))
        .group_by(MemoryTag.memory_id)
        .having(func.count(MemoryTag.tag) >= min_overlap)
        .order_by(func.count(MemoryTag.tag).desc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]
