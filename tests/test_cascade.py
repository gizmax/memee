"""Regression tests for FK cascade on Memory deletion.

Before the cascade fix, deleting a Memory either raised IntegrityError
(with PRAGMA foreign_keys=ON) or left dangling rows in Decision, AntiPattern,
ProjectMemory, MemoryValidation, MemoryConnection, and MemoryTag.
"""

from __future__ import annotations

from memee.storage.models import (
    AntiPattern,
    Decision,
    Memory,
    MemoryConnection,
    MemoryTag,
    MemoryType,
    MemoryValidation,
    Project,
    ProjectMemory,
    Severity,
)


def _make_memory(session, *, title: str) -> Memory:
    m = Memory(
        type=MemoryType.PATTERN.value,
        title=title,
        content=f"content for {title}",
        tags=["cascade", "test"],
        confidence_score=0.5,
    )
    session.add(m)
    session.flush()
    return m


def test_memory_delete_cascades_all_children(session, org):
    # Parent memory + a second memory for connection targets
    m = _make_memory(session, title="parent memory for cascade")
    other = _make_memory(session, title="other memory for cascade")

    # Project for project_memories / validations
    proj = Project(
        organization_id=org.id,
        name="cascade-proj",
        path="/tmp/cascade-test",
    )
    session.add(proj)
    session.flush()

    # Children
    session.add(
        Decision(memory_id=m.id, chosen="foo", alternatives=["bar"], criteria=[])
    )
    session.add(
        AntiPattern(
            memory_id=m.id,
            severity=Severity.MEDIUM.value,
            trigger="trigger text",
            consequence="bad consequence",
            alternative="do this instead",
        )
    )
    session.add(ProjectMemory(project_id=proj.id, memory_id=m.id))
    session.add(
        MemoryValidation(
            memory_id=m.id, project_id=proj.id, validated=True, evidence="ok"
        )
    )
    session.add(
        MemoryConnection(
            source_id=m.id, target_id=other.id, relationship_type="supports"
        )
    )
    session.add(
        MemoryConnection(
            source_id=other.id, target_id=m.id, relationship_type="supports"
        )
    )
    session.add(MemoryTag(memory_id=m.id, tag="cascade"))
    session.add(MemoryTag(memory_id=m.id, tag="test"))
    session.add(MemoryTag(memory_id=m.id, tag="extra"))
    session.commit()

    # Sanity: children exist before delete
    assert session.query(Decision).filter_by(memory_id=m.id).count() == 1
    assert session.query(AntiPattern).filter_by(memory_id=m.id).count() == 1
    assert session.query(ProjectMemory).filter_by(memory_id=m.id).count() == 1
    assert session.query(MemoryValidation).filter_by(memory_id=m.id).count() == 1
    assert (
        session.query(MemoryConnection)
        .filter(
            (MemoryConnection.source_id == m.id) | (MemoryConnection.target_id == m.id)
        )
        .count()
        == 2
    )
    assert session.query(MemoryTag).filter_by(memory_id=m.id).count() == 3

    # Delete the parent — no IntegrityError should be raised.
    session.delete(m)
    session.commit()

    # All children of m are gone
    assert session.query(Decision).filter_by(memory_id=m.id).count() == 0
    assert session.query(AntiPattern).filter_by(memory_id=m.id).count() == 0
    assert session.query(ProjectMemory).filter_by(memory_id=m.id).count() == 0
    assert session.query(MemoryValidation).filter_by(memory_id=m.id).count() == 0
    assert (
        session.query(MemoryConnection)
        .filter(
            (MemoryConnection.source_id == m.id) | (MemoryConnection.target_id == m.id)
        )
        .count()
        == 0
    )
    assert session.query(MemoryTag).filter_by(memory_id=m.id).count() == 0

    # Sibling memory + project are untouched
    assert session.query(Memory).filter_by(id=other.id).first() is not None
    assert session.query(Project).filter_by(id=proj.id).first() is not None
