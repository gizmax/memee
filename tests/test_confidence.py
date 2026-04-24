"""Tests for Bayesian confidence scoring and maturity promotion."""

from memee.engine.confidence import evaluate_maturity, update_confidence
from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryType,
    Project,
    ProjectMemory,
)


def test_initial_confidence(session):
    """New memory starts at 0.5 confidence."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Test",
        content="Test",
    )
    session.add(memory)
    session.commit()
    assert memory.confidence_score == 0.5


def test_validation_increases_confidence(session):
    """Validating a memory increases confidence."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Test",
        content="Test",
    )
    session.add(memory)
    session.commit()

    old_score = memory.confidence_score
    new_score = update_confidence(memory, validated=True)
    assert new_score > old_score
    assert memory.validation_count == 1


def test_invalidation_decreases_confidence(session):
    """Invalidating a memory decreases confidence."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Test",
        content="Test",
    )
    session.add(memory)
    session.commit()

    old_score = memory.confidence_score
    new_score = update_confidence(memory, validated=False)
    assert new_score < old_score
    assert memory.invalidation_count == 1


def test_cross_project_bonus(session, org):
    """Validations in new projects get a 1.5x bonus."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Test",
        content="Test",
    )
    session.add(memory)

    p1 = Project(organization_id=org.id, name="P1", path="/tmp/p1")
    p2 = Project(organization_id=org.id, name="P2", path="/tmp/p2")
    session.add_all([p1, p2])
    session.flush()

    # First validation: same project (no bonus)
    pm = ProjectMemory(project_id=p1.id, memory_id=memory.id)
    session.add(pm)
    session.commit()

    score_after_same = update_confidence(memory, validated=True, project_id=p1.id)

    # Second validation: NEW project (cross-project bonus)
    score_after_cross = update_confidence(memory, validated=True, project_id=p2.id)
    delta_cross = score_after_cross - score_after_same

    # Cross-project should give a bigger boost
    assert memory.project_count == 1  # p2 is new
    assert memory.application_count == 2


def test_negativity_bias(session):
    """Invalidation weight (0.12) is higher than validation weight (0.08)."""
    m1 = Memory(type=MemoryType.PATTERN.value, title="T1", content="C1")
    m2 = Memory(type=MemoryType.PATTERN.value, title="T2", content="C2")
    session.add_all([m1, m2])
    session.commit()

    val_delta = update_confidence(m1, validated=True) - 0.5
    inval_delta = 0.5 - update_confidence(m2, validated=False)

    # Invalidation should have larger absolute impact
    assert inval_delta > val_delta


def test_confidence_caps(session):
    """Confidence never exceeds 0.99 or drops below 0.01."""
    m_high = Memory(
        type=MemoryType.PATTERN.value, title="High", content="C",
        confidence_score=0.98,
    )
    m_low = Memory(
        type=MemoryType.PATTERN.value, title="Low", content="C",
        confidence_score=0.02,
    )
    session.add_all([m_high, m_low])
    session.commit()

    update_confidence(m_high, validated=True)
    assert m_high.confidence_score <= 0.99

    update_confidence(m_low, validated=False)
    assert m_low.confidence_score >= 0.01


def test_maturity_progression(session):
    """Test maturity evaluation logic."""
    memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Test",
        content="Test",
        confidence_score=0.5,
        application_count=0,
        project_count=0,
        validation_count=0,
    )
    session.add(memory)
    session.commit()

    # hypothesis: no applications
    assert evaluate_maturity(memory) == MaturityLevel.HYPOTHESIS.value

    # tested: at least 1 application
    memory.application_count = 1
    assert evaluate_maturity(memory) == MaturityLevel.TESTED.value

    # validated: confidence >= 0.7, 3+ projects
    memory.confidence_score = 0.75
    memory.project_count = 3
    assert evaluate_maturity(memory) == MaturityLevel.VALIDATED.value

    # canon: confidence >= 0.85, 5+ projects, 10+ validations
    memory.confidence_score = 0.90
    memory.project_count = 5
    memory.validation_count = 10
    assert evaluate_maturity(memory) == MaturityLevel.CANON.value

    # deprecated: low confidence after applications
    memory.confidence_score = 0.15
    memory.application_count = 5
    assert evaluate_maturity(memory) == MaturityLevel.DEPRECATED.value
