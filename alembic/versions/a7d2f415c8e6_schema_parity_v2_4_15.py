"""schema parity: impact_events + search_ranking_snapshots + ltr_models +
search_events ranker columns + memory_connections.expires_at

Revision ID: a7d2f415c8e6
Revises: c3f9e8a1b4d2
Create Date: 2026-05-29 12:00:00.000000

v2.4.15 — closes the drift between ``alembic upgrade head`` and the
SQLAlchemy metadata that accumulated as features landed without their
own migration.

Adds three tables and three columns. The OSS ``init_db`` bootstrap
(idempotent ``create_all`` + per-column ALTERs) had been silently
catching this on every install, so existing DBs are already on the
target schema; this migration just lets the Alembic path agree.

Tables:
  * ``impact_events``           — measurable impact rows (engine/impact.py)
  * ``search_ranking_snapshots`` — per-(event, candidate) features used
                                   by the LTR retrainer
  * ``ltr_models``              — trained ranker registry (R9)

Columns:
  * ``search_events.ranker_version``  — String(40), default 'rrf_v1'
  * ``search_events.ranker_model_id`` — String(36), nullable
  * ``memory_connections.expires_at`` — DateTime, nullable
    (time-bounded supersedes/depends_on edges)

Indexes match the ``__table_args__`` on the SQLAlchemy models so a
fresh alembic-only install is index-identical to ``init_db``.

Additive and idempotent: every CREATE / ALTER guards on inspector
state, so partial-migrated DBs (an old custom install, or a re-run)
converge without errors.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7d2f415c8e6"
down_revision: Union[str, Sequence[str], None] = "c3f9e8a1b4d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, name: str) -> bool:
    return name in set(inspector.get_table_names())


def _column_exists(inspector: sa.Inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def _index_exists(inspector: sa.Inspector, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── impact_events ────────────────────────────────────────────────
    if not _table_exists(inspector, "impact_events"):
        op.create_table(
            "impact_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "memory_id",
                sa.String(length=36),
                sa.ForeignKey("memories.id"),
                nullable=False,
            ),
            sa.Column(
                "project_id",
                sa.String(length=36),
                sa.ForeignKey("projects.id"),
            ),
            sa.Column("agent", sa.String(length=255)),
            sa.Column("model", sa.String(length=100)),
            sa.Column("impact_type", sa.String(length=30), nullable=False),
            sa.Column("trigger", sa.Text()),
            sa.Column("memory_shown", sa.Text()),
            sa.Column("agent_action", sa.Text()),
            sa.Column("outcome", sa.Text()),
            sa.Column(
                "time_saved_minutes", sa.Float(), server_default=sa.text("0"),
            ),
            sa.Column(
                "iterations_saved", sa.Integer(), server_default=sa.text("0"),
            ),
            sa.Column("severity_avoided", sa.String(length=20)),
            sa.Column("confidence_at_use", sa.Float()),
            sa.Column("created_at", sa.DateTime()),
        )

    # ── ltr_models ───────────────────────────────────────────────────
    # Must precede search_ranking_snapshots so the FK target exists when
    # snapshots reference ranker_model_id in a later release; the
    # snapshot table itself does not FK to ltr_models today but the
    # search_events.ranker_model_id semantically points here.
    if not _table_exists(inspector, "ltr_models"):
        op.create_table(
            "ltr_models",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "version", sa.String(length=40), nullable=False, unique=True,
            ),
            sa.Column("path", sa.Text(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="candidate",
            ),
            sa.Column("eval_ndcg_at_10", sa.Float()),
            sa.Column("eval_recall_at_5", sa.Float()),
            sa.Column("eval_mrr", sa.Float()),
            sa.Column("training_event_count", sa.Integer()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("activated_at", sa.DateTime()),
            sa.Column("notes", sa.Text()),
        )

    # ── search_events.ranker_version + ranker_model_id ───────────────
    # Add the columns BEFORE creating search_ranking_snapshots so an
    # accidental FK back-reference from a future migration finds them.
    se_cols = {c["name"] for c in inspector.get_columns("search_events")}
    if "ranker_version" not in se_cols:
        with op.batch_alter_table("search_events", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "ranker_version",
                    sa.String(length=40),
                    server_default="rrf_v1",
                )
            )
    if "ranker_model_id" not in se_cols:
        with op.batch_alter_table("search_events", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("ranker_model_id", sa.String(length=36))
            )
    # Index on ranker_version so the analytics slice (`hit@k by ranker`)
    # uses an index seek instead of a full scan.
    if not _index_exists(inspector, "search_events", "ix_search_events_ranker"):
        op.create_index(
            "ix_search_events_ranker", "search_events", ["ranker_version"],
        )

    # ── search_ranking_snapshots ─────────────────────────────────────
    if not _table_exists(inspector, "search_ranking_snapshots"):
        op.create_table(
            "search_ranking_snapshots",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "event_id",
                sa.String(length=36),
                sa.ForeignKey("search_events.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("memory_id", sa.String(length=36), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("bm25_score", sa.Float(), server_default=sa.text("0")),
            sa.Column("bm25_rank", sa.Integer()),
            sa.Column("vector_score", sa.Float(), server_default=sa.text("0")),
            sa.Column("vector_rank", sa.Integer()),
            sa.Column("rrf_score", sa.Float(), server_default=sa.text("0")),
            sa.Column("tag_score", sa.Float(), server_default=sa.text("0")),
            sa.Column(
                "confidence_boost", sa.Float(), server_default=sa.text("0"),
            ),
            sa.Column(
                "title_phrase_match",
                sa.Boolean(),
                server_default=sa.text("0"),
            ),
            sa.Column(
                "intent_multiplier", sa.Float(), server_default=sa.text("1"),
            ),
            sa.Column("memory_confidence", sa.Float()),
            sa.Column("memory_maturity", sa.String(length=20)),
            sa.Column("memory_type", sa.String(length=20)),
            sa.Column("memory_validation_count", sa.Integer()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_ranking_snapshots_event",
            "search_ranking_snapshots",
            ["event_id"],
        )
        op.create_index(
            "ix_ranking_snapshots_memory",
            "search_ranking_snapshots",
            ["memory_id"],
        )

    # ── memory_connections.expires_at ────────────────────────────────
    mc_cols = {c["name"] for c in inspector.get_columns("memory_connections")}
    if "expires_at" not in mc_cols:
        with op.batch_alter_table(
            "memory_connections", schema=None
        ) as batch_op:
            batch_op.add_column(
                sa.Column("expires_at", sa.DateTime(), nullable=True)
            )


def downgrade() -> None:
    """Reverse the parity additions.

    Tables drop in reverse-dependency order. Columns are dropped
    idempotently — a row that survived a partial upgrade still rolls
    back cleanly.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    mc_cols = {c["name"] for c in inspector.get_columns("memory_connections")}
    if "expires_at" in mc_cols:
        with op.batch_alter_table(
            "memory_connections", schema=None
        ) as batch_op:
            batch_op.drop_column("expires_at")

    if _table_exists(inspector, "search_ranking_snapshots"):
        op.drop_index(
            "ix_ranking_snapshots_memory", table_name="search_ranking_snapshots",
        )
        op.drop_index(
            "ix_ranking_snapshots_event", table_name="search_ranking_snapshots",
        )
        op.drop_table("search_ranking_snapshots")

    if _index_exists(inspector, "search_events", "ix_search_events_ranker"):
        op.drop_index("ix_search_events_ranker", table_name="search_events")
    se_cols = {c["name"] for c in inspector.get_columns("search_events")}
    if "ranker_model_id" in se_cols:
        with op.batch_alter_table("search_events", schema=None) as batch_op:
            batch_op.drop_column("ranker_model_id")
    if "ranker_version" in se_cols:
        with op.batch_alter_table("search_events", schema=None) as batch_op:
            batch_op.drop_column("ranker_version")

    if _table_exists(inspector, "ltr_models"):
        op.drop_table("ltr_models")

    if _table_exists(inspector, "impact_events"):
        op.drop_table("impact_events")
