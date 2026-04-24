"""SQLAlchemy 2.0 models for Memee institutional memory."""

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums ──


class MemoryType(str, Enum):
    PATTERN = "pattern"
    DECISION = "decision"
    ANTI_PATTERN = "anti_pattern"
    LESSON = "lesson"
    OBSERVATION = "observation"


class MaturityLevel(str, Enum):
    HYPOTHESIS = "hypothesis"
    TESTED = "tested"
    VALIDATED = "validated"
    CANON = "canon"
    DEPRECATED = "deprecated"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResearchStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Core Tables ──


# ── Multi-user models (User, Team) live in the `memee-team` package ──
# On import, memee-team adds `User` and `Team` SQLAlchemy models to the
# same metadata as `Organization`, with cross-references via ForeignKey.


class Organization(Base):
    """Tenancy container. In OSS there is a single default org created at
    init time; it exists because every Project must belong to one. The paid
    `memee-team` package adds Users and Teams under the same Organization.
    """
    __tablename__ = "organizations"

    id = Column(String(36), primary_key=True, default=new_id)
    name = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=utcnow)

    projects = relationship("Project", back_populates="organization")


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=new_id)
    organization_id = Column(String(36), ForeignKey("organizations.id"), nullable=False)
    name = Column(String(255), nullable=False)
    path = Column(Text)
    description = Column(Text)
    tags = Column(JSON, default=list)
    stack = Column(JSON, default=list)
    created_at = Column(DateTime, default=utcnow)
    last_active = Column(DateTime)

    organization = relationship("Organization", back_populates="projects")
    memories = relationship(
        "ProjectMemory",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    experiments = relationship(
        "ResearchExperiment",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "path", name="uq_org_path"),
    )


class Memory(Base):
    __tablename__ = "memories"

    id = Column(String(36), primary_key=True, default=new_id)
    type = Column(String(20), nullable=False)
    maturity = Column(String(20), nullable=False, default=MaturityLevel.HYPOTHESIS.value)
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text)
    tags = Column(JSON, default=list)
    context = Column(JSON, default=dict)

    # Vector embedding (384-dim, stored as JSON)
    embedding = Column(JSON)

    # Quality gate
    source_type = Column(String(20), default="unknown")  # "human", "llm", "import"
    quality_score = Column(Float)                         # 1-5 heuristic score

    # Confidence
    confidence_score = Column(Float, default=0.5)
    validation_count = Column(Integer, default=0)
    invalidation_count = Column(Integer, default=0)
    application_count = Column(Integer, default=0)
    project_count = Column(Integer, default=0)

    # Denormalized counters — avoid N+1 lazy loads in update_confidence
    validated_project_ids = Column(JSON, default=list)    # List of project ids
    same_project_val_counts = Column(JSON, default=dict)  # {project_id: count}
    model_families_seen = Column(JSON, default=list)      # Unique model family strings

    # Lifecycle
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, onupdate=utcnow)
    last_validated_at = Column(DateTime)
    last_applied_at = Column(DateTime)
    expires_at = Column(DateTime)
    deprecated_at = Column(DateTime)
    deprecated_reason = Column(Text)

    # Scope: personal → team → org. In OSS only `personal` is used;
    # memee-team activates team/org with its own User/Team tables and wires
    # the FKs loosely (no hard constraint at the OSS layer).
    scope = Column(String(20), default="personal")
    owner_id = Column(String(36))
    team_id = Column(String(36))
    promoted_from = Column(String(36))   # Original memory ID if promoted

    # Source tracking
    source_agent = Column(String(255))
    source_model = Column(String(100))
    source_session = Column(String(255))
    source_commit = Column(String(40))
    source_url = Column(Text)            # Where it came from: PR URL, Slack link, etc.
    model_count = Column(Integer, default=0)

    # Evidence ledger: provenance chain
    evidence_chain = Column(JSON, default=list)  # [{type, ref, timestamp, agent, outcome}]

    # Dedup bookkeeping — how many near-duplicates have been merged into this memory.
    # Used by the quality gate to halt over-aggressive clustering at team/org scope
    # (flag for manual review once a single memory absorbs too many near-dupes).
    merge_count = Column(Integer, default=0)

    # Relations. `cascade="all, delete-orphan"` + `passive_deletes=True` lets
    # SQLAlchemy hand off cascade to the DB (ON DELETE CASCADE on the FK side)
    # so bulk deletes don't trigger N+1 loads and raw SQL deletes still clean
    # up dangling child rows.
    projects = relationship(
        "ProjectMemory",
        back_populates="memory",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    validations = relationship(
        "MemoryValidation",
        back_populates="memory",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    decision = relationship(
        "Decision",
        back_populates="memory",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    anti_pattern = relationship(
        "AntiPattern",
        back_populates="memory",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_memories_type", "type"),
        Index("ix_memories_maturity", "maturity"),
        Index("ix_memories_confidence", "confidence_score"),
        CheckConstraint("confidence_score >= 0.0 AND confidence_score <= 1.0"),
    )


class ProjectMemory(Base):
    __tablename__ = "project_memories"

    project_id = Column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    memory_id = Column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    relevance_score = Column(Float, default=1.0)
    applied = Column(Boolean, default=False)
    applied_at = Column(DateTime)
    outcome = Column(String(20))
    outcome_notes = Column(Text)

    # Evidence ledger for "mistake avoided" claims. Without a concrete
    # reference (diff, failing test, review comment, PR URL, or explicit
    # agent feedback) we will NOT count an outcome as a real avoidance —
    # only as "warning was shown / acknowledged". See engine/impact.py.
    outcome_evidence_type = Column(String(20))  # diff|test_failure|review_comment|pr_url|agent_feedback|NULL
    outcome_evidence_ref = Column(Text)         # the actual reference string

    project = relationship("Project", back_populates="memories")
    memory = relationship("Memory", back_populates="projects")


class MemoryConnection(Base):
    __tablename__ = "memory_connections"

    source_id = Column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    target_id = Column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    relationship_type = Column(String(50), nullable=False)
    strength = Column(Float, default=0.5)
    created_at = Column(DateTime, default=utcnow)


class MemoryValidation(Base):
    __tablename__ = "memory_validations"

    id = Column(String(36), primary_key=True, default=new_id)
    memory_id = Column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"))
    validated = Column(Boolean, nullable=False)
    evidence = Column(Text)
    validator_model = Column(String(100))  # Which model validated
    context = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utcnow)

    memory = relationship("Memory", back_populates="validations")


# ── Specialized Memory Types ──


class Decision(Base):
    __tablename__ = "decisions"

    memory_id = Column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    chosen = Column(Text, nullable=False)
    alternatives = Column(JSON, default=list)
    criteria = Column(JSON, default=list)
    outcome = Column(Text)
    outcome_date = Column(DateTime)
    reversible = Column(Boolean, default=True)
    decision_date = Column(DateTime, nullable=False, default=utcnow)

    memory = relationship("Memory", back_populates="decision")


class AntiPattern(Base):
    __tablename__ = "anti_patterns"

    memory_id = Column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    severity = Column(String(20), nullable=False, default=Severity.MEDIUM.value)
    trigger = Column(Text, nullable=False)
    consequence = Column(Text, nullable=False)
    alternative = Column(Text)
    detection = Column(Text)
    occurrences = Column(Integer, default=1)

    memory = relationship("Memory", back_populates="anti_pattern")


# ── Autoresearch Tracking ──


class ResearchExperiment(Base):
    __tablename__ = "research_experiments"

    id = Column(String(36), primary_key=True, default=new_id)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    goal = Column(Text, nullable=False)
    metric_name = Column(String(255), nullable=False)
    metric_direction = Column(String(10), nullable=False)
    verify_command = Column(Text, nullable=False)
    guard_command = Column(Text)
    scope_globs = Column(JSON, default=list)

    status = Column(String(20), nullable=False, default=ResearchStatus.RUNNING.value)
    baseline_value = Column(Float)
    final_value = Column(Float)
    best_value = Column(Float)
    total_iterations = Column(Integer, default=0)
    keeps = Column(Integer, default=0)
    discards = Column(Integer, default=0)
    crashes = Column(Integer, default=0)

    started_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime)

    project = relationship("Project", back_populates="experiments")
    iterations = relationship(
        "ResearchIteration",
        back_populates="experiment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ResearchIteration(Base):
    __tablename__ = "research_iterations"

    id = Column(String(36), primary_key=True, default=new_id)
    experiment_id = Column(
        String(36),
        ForeignKey("research_experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    iteration_number = Column(Integer, nullable=False)
    commit_hash = Column(String(40))
    metric_value = Column(Float)
    delta = Column(Float)
    guard_passed = Column(Boolean)
    status = Column(String(20), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=utcnow)

    experiment = relationship("ResearchExperiment", back_populates="iterations")


# ── Analytics ──


class MemoryTag(Base):
    """Normalized tag index for fast propagation/predictive lookups."""
    __tablename__ = "memory_tags"

    memory_id = Column(
        String(36), ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    tag = Column(String(100), primary_key=True)

    __table_args__ = (
        Index("ix_memory_tags_tag", "tag"),
    )


class ProjectTag(Base):
    """Normalized tag index for projects (stack + tags combined)."""
    __tablename__ = "project_tags"

    project_id = Column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    tag = Column(String(100), primary_key=True)

    __table_args__ = (
        Index("ix_project_tags_tag", "tag"),
    )


class SearchEvent(Base):
    """Retrieval telemetry: one row per ``search_memories`` call.

    The ``accepted_memory_id`` column is nullable — it is left blank at
    record-time and later filled in by ``search_feedback`` (MCP tool) or
    ``memee feedback`` (CLI) when the caller signals "I used this result".

    ``position_of_accepted`` is also nullable. The recorder can't know which
    result the caller will end up picking, so the acceptance helper stamps
    both the id and its 0-based position in the returned list. Events with
    a non-null ``accepted_memory_id`` but a null ``position_of_accepted``
    are treated as "accepted but position unknown" — they count toward
    ``accepted_memory_rate`` but not toward ``hit@3``.
    """
    __tablename__ = "search_events"

    id = Column(String(36), primary_key=True, default=new_id)
    query_text = Column(Text, nullable=False)
    position_of_accepted = Column(Integer)       # 0-based, nullable
    returned_count = Column(Integer, nullable=False, default=0)
    top_memory_id = Column(String(36))           # id of the #1 hit (nullable if empty results)
    latency_ms = Column(Float, nullable=False, default=0.0)
    accepted_memory_id = Column(String(36))      # filled in by feedback helper
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_search_events_created_at", "created_at"),
        Index("ix_search_events_accepted", "accepted_memory_id"),
    )


class LearningSnapshot(Base):
    __tablename__ = "learning_snapshots"

    id = Column(String(36), primary_key=True, default=new_id)
    snapshot_date = Column(DateTime, nullable=False, default=utcnow)
    total_memories = Column(Integer)
    canon_memories = Column(Integer)
    hypothesis_memories = Column(Integer)
    deprecated_memories = Column(Integer)
    avg_confidence = Column(Float)
    cross_project_applications = Column(Integer)
    anti_patterns_avoided = Column(Integer)
    research_experiments_completed = Column(Integer)
    learning_rate = Column(Float)
