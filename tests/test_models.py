"""Tests for SQLAlchemy models and database initialization."""

from memee.storage.models import (
    AntiPattern,
    Decision,
    MaturityLevel,
    Memory,
    MemoryType,
    MemoryValidation,
    Project,
    ProjectMemory,
    Severity,
)


def test_create_memory(session):
    """Test basic memory creation."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Always use timeout on API calls",
        content="Use requests.get(url, timeout=10) to prevent hanging.",
        tags=["python", "api", "reliability"],
    )
    session.add(memory)
    session.commit()

    loaded = session.get(Memory, memory.id)
    assert loaded is not None
    assert loaded.title == "Always use timeout on API calls"
    assert loaded.type == "pattern"
    assert loaded.maturity == MaturityLevel.HYPOTHESIS.value
    assert loaded.confidence_score == 0.5
    assert loaded.tags == ["python", "api", "reliability"]


def test_create_decision(session):
    """Test decision creation with alternatives."""
    memory = Memory(
        type=MemoryType.DECISION.value,
        title="SQLite over PostgreSQL",
        content="Chose SQLite for single-file simplicity.",
    )
    session.add(memory)
    session.flush()

    decision = Decision(
        memory_id=memory.id,
        chosen="SQLite",
        alternatives=[
            {"name": "PostgreSQL", "reason_rejected": "Too complex for MVP"},
            {"name": "MongoDB", "reason_rejected": "No relational needs"},
        ],
    )
    session.add(decision)
    session.commit()

    loaded = session.get(Memory, memory.id)
    assert loaded.decision is not None
    assert loaded.decision.chosen == "SQLite"
    assert len(loaded.decision.alternatives) == 2


def test_create_anti_pattern(session):
    """Test anti-pattern creation."""
    memory = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title="Don't use pypdf for complex PDFs",
        content="pypdf is buggy with complex layouts.",
        tags=["python", "pdf"],
    )
    session.add(memory)
    session.flush()

    ap = AntiPattern(
        memory_id=memory.id,
        severity=Severity.HIGH.value,
        trigger="Processing complex multi-column PDFs",
        consequence="Garbled text, missing content",
        alternative="Use pymupdf or pdfplumber",
    )
    session.add(ap)
    session.commit()

    loaded = session.get(Memory, memory.id)
    assert loaded.anti_pattern is not None
    assert loaded.anti_pattern.severity == "high"
    assert loaded.anti_pattern.alternative == "Use pymupdf or pdfplumber"


def test_project_memory_link(session, org):
    """Test linking memories to projects."""
    project = Project(
        organization_id=org.id,
        name="TestProject",
        path="/tmp/testproject",
        stack=["Python", "FastAPI"],
    )
    session.add(project)

    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Test pattern",
        content="Test content",
    )
    session.add(memory)
    session.flush()

    link = ProjectMemory(
        project_id=project.id,
        memory_id=memory.id,
    )
    session.add(link)
    session.commit()

    assert len(memory.projects) == 1
    assert memory.projects[0].project.name == "TestProject"


def test_memory_validation_tracking(session):
    """Test validation event recording."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Test pattern",
        content="Test content",
    )
    session.add(memory)
    session.flush()

    v1 = MemoryValidation(memory_id=memory.id, validated=True, evidence="It worked!")
    v2 = MemoryValidation(memory_id=memory.id, validated=False, evidence="It failed here")
    session.add_all([v1, v2])
    session.commit()

    assert len(memory.validations) == 2
    assert sum(1 for v in memory.validations if v.validated) == 1
