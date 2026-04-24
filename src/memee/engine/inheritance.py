"""Memory Inheritance: new projects inherit from similar-stack projects.

When a project is registered, find projects with overlapping stacks
and inherit their validated/canon patterns. Don't start from zero.
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


def compute_stack_similarity(project_a: Project, project_b: Project) -> float:
    """Similarity between projects based on stack + tags.

    Uses weighted Jaccard: stack overlap (0.6) + tag overlap (0.4).
    """
    stack_a = set(s.lower() for s in (project_a.stack or []))
    stack_b = set(s.lower() for s in (project_b.stack or []))
    tags_a = set(t.lower() for t in (project_a.tags or []))
    tags_b = set(t.lower() for t in (project_b.tags or []))

    stack_union = stack_a | stack_b
    stack_sim = len(stack_a & stack_b) / len(stack_union) if stack_union else 0.0

    tag_union = tags_a | tags_b
    tag_sim = len(tags_a & tags_b) / len(tag_union) if tag_union else 0.0

    return 0.6 * stack_sim + 0.4 * tag_sim


def find_similar_projects(
    session: Session,
    project: Project,
    min_similarity: float = 0.2,
    limit: int = 5,
) -> list[tuple[Project, float]]:
    """Find projects with similar stacks, sorted by similarity."""
    all_projects = session.query(Project).filter(Project.id != project.id).all()

    scored = []
    for other in all_projects:
        sim = compute_stack_similarity(project, other)
        if sim >= min_similarity:
            scored.append((other, sim))

    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


def inherit_memories(
    session: Session,
    target_project: Project,
    min_similarity: float = 0.2,
    min_memory_confidence: float = 0.7,
    max_inherit: int = 200,
) -> dict:
    """Inherit validated patterns from similar projects.

    Inheritance ≠ validation:
      We create project-to-memory links so onboarding agents SEE the knowledge,
      but we do NOT bump confidence or applications_count. A memory that was
      applied once in Project A does not become 2x validated because Project B
      is similar; linking is only a delivery event.

      Previously this function pulled in TESTED memories (maturity after a
      single application), which dragged hypothesis-adjacent, 0.5-0.65
      confidence content into fresh projects and polluted onboarding. We now
      restrict to VALIDATED + CANON and raise the default confidence floor to
      0.7 — inheritance should carry forward knowledge the org already trusts.

    Returns stats: {similar_projects, memories_inherited, by_type, avg_confidence}
    """
    similar = find_similar_projects(session, target_project, min_similarity)

    stats = {
        "similar_projects": [],
        "memories_inherited": 0,
        "by_type": {},
        "inherited_memories": [],
    }

    existing_memory_ids = {
        pm.memory_id
        for pm in session.query(ProjectMemory)
        .filter(ProjectMemory.project_id == target_project.id)
        .all()
    }

    inherited_count = 0

    for source_project, similarity in similar:
        stats["similar_projects"].append({
            "name": source_project.name,
            "similarity": round(similarity, 3),
        })

        # Get validated/canon memories from source project only. TESTED is
        # explicitly excluded: "one application" is not strong enough evidence
        # to deliver a memory to a cross-project onboarding channel.
        source_links = (
            session.query(ProjectMemory)
            .join(Memory, Memory.id == ProjectMemory.memory_id)
            .filter(
                ProjectMemory.project_id == source_project.id,
                Memory.confidence_score >= min_memory_confidence,
                Memory.maturity.in_([
                    MaturityLevel.VALIDATED.value,
                    MaturityLevel.CANON.value,
                ]),
                Memory.type.in_([
                    MemoryType.PATTERN.value,
                    MemoryType.ANTI_PATTERN.value,
                    MemoryType.LESSON.value,
                ]),
            )
            .all()
        )

        for source_link in source_links:
            if inherited_count >= max_inherit:
                break
            if source_link.memory_id in existing_memory_ids:
                continue

            # Inherit: link to target project with relevance = stack similarity
            pm = ProjectMemory(
                project_id=target_project.id,
                memory_id=source_link.memory_id,
                relevance_score=similarity,
            )
            session.add(pm)
            existing_memory_ids.add(source_link.memory_id)

            # Link only — inheriting ≠ validating
            memory = session.get(Memory, source_link.memory_id)
            if memory:
                mem_type = memory.type
                stats["by_type"][mem_type] = stats["by_type"].get(mem_type, 0) + 1
                stats["inherited_memories"].append({
                    "title": memory.title,
                    "type": mem_type,
                    "confidence": memory.confidence_score,
                    "from_project": source_project.name,
                })

            inherited_count += 1

        if inherited_count >= max_inherit:
            break

    session.commit()
    stats["memories_inherited"] = inherited_count
    return stats
