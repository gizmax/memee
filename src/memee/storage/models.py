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
    # Tenancy boundary. In single-user OSS this is always set to the default
    # org at record time; in memee-team every write sets it to the acting
    # user's org and every visibility query filters by it. Nullable on the
    # column so that legacy DBs upgrade without a destructive backfill — the
    # ``init_db`` bootstrap backfills NULLs to the default org in-place.
    organization_id = Column(String(36), ForeignKey("organizations.id"))
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
        Index("ix_memories_org", "organization_id"),
        # Composite indexes for the multi-tenant hot paths: scoped type+maturity
        # filters are what powers search + briefing selection, and the org_id
        # prefix lets memee-team partition without a second lookup.
        Index("ix_memories_org_type_maturity", "organization_id", "type", "maturity"),
        Index("ix_memories_org_scope", "organization_id", "scope"),
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
    """Directed edge between two memories.

    relationship_type values currently emitted by ``dream.py``:
      * ``contradicts`` (R7) — pattern ↔ anti_pattern with overlapping intent
      * ``supports`` (R7) — pattern → pattern with shared tags + maturity
      * ``related_to`` (R7) — generic loose link
      * ``depends_on`` (R9) — source requires target as a prerequisite
      * ``supersedes`` (R9) — source replaces target (target should be skipped
        in briefing once present, optionally pending review for deprecation)

    ``expires_at`` (R9) lets supersession/dependency edges be time-bounded.
    NULL means the edge has no scheduled expiry. The lifecycle nightly
    sweeps tombstone-expire any edge whose ``expires_at`` has passed.
    """
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
    expires_at = Column(DateTime)

    __table_args__ = (
        # Briefing fans out from a candidate to its predecessors via target_id.
        # Lifecycle scans CANON dependents via source_id. Both paths benefit
        # from a leading-column index on the lookup key + the edge type so
        # the WHERE on relationship_type is a covered seek.
        Index(
            "ix_memory_connections_target_type",
            "target_id",
            "relationship_type",
        ),
        Index(
            "ix_memory_connections_source_type",
            "source_id",
            "relationship_type",
        ),
    )


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

    __table_args__ = (
        # R10 db audit: /timeline endpoint orders by created_at over the full
        # validation history. Without this index SQLite uses a TEMP B-TREE
        # FOR ORDER BY on every dashboard hit.
        Index("ix_memory_validations_created_at", "created_at"),
    )


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

    __table_args__ = (
        # R10 db audit: briefing critical-AP scan (router.smart_briefing
        # filters severity == 'critical') was a full SCAN. Index on severity
        # so ``WHERE severity = 'critical'`` becomes a SEARCH.
        Index("ix_anti_patterns_severity", "severity"),
    )


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

    __table_args__ = (
        # R10 db audit win #1: ``get_meta_learning`` runs one ordered fetch
        # of iterations per experiment (often hundreds of experiments per
        # call). Without a covering composite index SQLite SCANs the table
        # plus a TEMP B-TREE for ORDER BY iteration_number — measured 200×
        # full-scan loop. With this index the plan flips to SEARCH USING
        # INDEX (experiment_id=?), no temp sort.
        Index("ix_research_iter_exp_num", "experiment_id", "iteration_number"),
    )


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

    R9 LTR fields:
      * ``ranker_version`` — string identifying the ranker that produced
        this row's order (e.g. ``rrf_v1``, ``ltr_v1``). Lets analytics
        slice hit@k by ranker for A/B comparison.
      * ``ranker_model_id`` — FK-like reference to ``ltr_models.id`` when
        an LTR model was used. NULL when only the heuristic stack ran.
    """
    __tablename__ = "search_events"

    id = Column(String(36), primary_key=True, default=new_id)
    query_text = Column(Text, nullable=False)
    position_of_accepted = Column(Integer)       # 0-based, nullable
    returned_count = Column(Integer, nullable=False, default=0)
    top_memory_id = Column(String(36))           # id of the #1 hit (nullable if empty results)
    latency_ms = Column(Float, nullable=False, default=0.0)
    accepted_memory_id = Column(String(36))      # filled in by feedback helper
    ranker_version = Column(String(40), default="rrf_v1")
    ranker_model_id = Column(String(36))
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_search_events_created_at", "created_at"),
        Index("ix_search_events_accepted", "accepted_memory_id"),
        Index("ix_search_events_ranker", "ranker_version"),
    )


class SearchRankingSnapshot(Base):
    """R9 hard-negative mining (#4): per-(event, candidate) feature row.

    At search time we persist the ranking features for the top-N candidates
    so the LTR retraining job can later mine pairs of (rejected_top,
    accepted_lower) without recomputing features from a possibly-mutated
    Memory state.

    Stored columns are deliberately scalar — JSON would be cheaper to read
    but a flat row plays well with pandas/lightgbm and makes column-wise
    drift analysis (e.g. "rrf_score distribution shifted between v1 and
    v2") a single SELECT.
    """
    __tablename__ = "search_ranking_snapshots"

    id = Column(String(36), primary_key=True, default=new_id)
    event_id = Column(
        String(36),
        ForeignKey("search_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_id = Column(String(36), nullable=False)
    rank = Column(Integer, nullable=False)        # 0-based position in returned list
    bm25_score = Column(Float, default=0.0)
    bm25_rank = Column(Integer)
    vector_score = Column(Float, default=0.0)
    vector_rank = Column(Integer)
    rrf_score = Column(Float, default=0.0)
    tag_score = Column(Float, default=0.0)
    confidence_boost = Column(Float, default=0.0)
    title_phrase_match = Column(Boolean, default=False)
    intent_multiplier = Column(Float, default=1.0)
    # Memory snapshot at search time (used by trainer to detect drift).
    memory_confidence = Column(Float)
    memory_maturity = Column(String(20))
    memory_type = Column(String(20))
    memory_validation_count = Column(Integer)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_ranking_snapshots_event", "event_id"),
        Index("ix_ranking_snapshots_memory", "memory_id"),
    )


class LTRModel(Base):
    """R9 LTR (#3): trained ranker registry.

    Memee can hold multiple ranker versions. ``status`` is the rollout flag:
      * ``candidate`` — trained, not yet serving traffic
      * ``canary`` — serving a fraction of traffic via env-var hash
      * ``production`` — current default ranker
      * ``deprecated`` — superseded by a newer model
    """
    __tablename__ = "ltr_models"

    id = Column(String(36), primary_key=True, default=new_id)
    version = Column(String(40), nullable=False, unique=True)
    path = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="candidate")
    eval_ndcg_at_10 = Column(Float)
    eval_recall_at_5 = Column(Float)
    eval_mrr = Column(Float)
    training_event_count = Column(Integer)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    activated_at = Column(DateTime)
    notes = Column(Text)


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

    __table_args__ = (
        # R10 db audit: /snapshots endpoint orders by snapshot_date.
        Index("ix_learning_snapshots_date", "snapshot_date"),
    )
