"""drop research_experiments, research_iterations, learning_snapshots

Revision ID: 6d540c223770
Revises: 4d2a1e8f7c93
Create Date: 2026-04-27 00:00:00.000000

v2.0.0 — research engine removed.

Two surfaces are cut in this release:

  1. The autoresearch engine (``memee.engine.research``) and its 5 MCP
     tools / CLI group. The ``research_experiments`` and
     ``research_iterations`` tables were the entire schema for that
     engine; with the code gone they're dead weight on every install.
  2. The web dashboard at port 7878. Its only persistent backing was
     the ``learning_snapshots`` time-series, which fed the dashboard's
     ``/api/v1/snapshots`` endpoint. No CLI command or external
     integration ever read this table.

Idempotent: each ``DROP`` is guarded by ``IF EXISTS`` so installs that
never had the tables (fresh ``init_db`` on v2.0.0) upgrade silently. The
``downgrade`` is best-effort — it recreates the tables empty so a roll-
back leaves a working schema, but it cannot recover the dropped data.

Pattern follows ``4d2a1e8f7c93_memory_organization_id`` (idempotent +
reversible).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6d540c223770"
down_revision: Union[str, Sequence[str], None] = "4d2a1e8f7c93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the three obsolete tables.

    ``research_iterations`` first because it FKs into ``research_experiments``.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # Composite index on research_iterations is implicitly dropped with the
    # table on SQLite; PostgreSQL also cascades. We drop it explicitly for
    # backends that don't (and to keep the downgrade symmetrical).
    if "research_iterations" in existing:
        existing_indexes = {
            ix["name"] for ix in inspector.get_indexes("research_iterations")
        }
        if "ix_research_iter_exp_num" in existing_indexes:
            op.drop_index(
                "ix_research_iter_exp_num",
                table_name="research_iterations",
            )
        op.drop_table("research_iterations")

    if "research_experiments" in existing:
        op.drop_table("research_experiments")

    if "learning_snapshots" in existing:
        existing_indexes = {
            ix["name"] for ix in inspector.get_indexes("learning_snapshots")
        }
        if "ix_learning_snapshots_date" in existing_indexes:
            op.drop_index(
                "ix_learning_snapshots_date",
                table_name="learning_snapshots",
            )
        op.drop_table("learning_snapshots")


def downgrade() -> None:
    """Recreate the dropped tables empty.

    The schema mirrors what ``843e414a0596_initial_schema`` produced,
    including the composite index on research_iterations and the
    snapshot_date index on learning_snapshots. Data cannot be recovered.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "research_experiments" not in existing:
        op.create_table(
            "research_experiments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("goal", sa.Text(), nullable=False),
            sa.Column("metric_name", sa.String(length=255), nullable=False),
            sa.Column("metric_direction", sa.String(length=10), nullable=False),
            sa.Column("verify_command", sa.Text(), nullable=False),
            sa.Column("guard_command", sa.Text(), nullable=True),
            sa.Column("scope_globs", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("baseline_value", sa.Float(), nullable=True),
            sa.Column("final_value", sa.Float(), nullable=True),
            sa.Column("best_value", sa.Float(), nullable=True),
            sa.Column("total_iterations", sa.Integer(), nullable=True),
            sa.Column("keeps", sa.Integer(), nullable=True),
            sa.Column("discards", sa.Integer(), nullable=True),
            sa.Column("crashes", sa.Integer(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "research_iterations" not in existing:
        op.create_table(
            "research_iterations",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("experiment_id", sa.String(length=36), nullable=False),
            sa.Column("iteration_number", sa.Integer(), nullable=False),
            sa.Column("commit_hash", sa.String(length=40), nullable=True),
            sa.Column("metric_value", sa.Float(), nullable=True),
            sa.Column("delta", sa.Float(), nullable=True),
            sa.Column("guard_passed", sa.Boolean(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["experiment_id"],
                ["research_experiments.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_research_iter_exp_num",
            "research_iterations",
            ["experiment_id", "iteration_number"],
            unique=False,
        )

    if "learning_snapshots" not in existing:
        op.create_table(
            "learning_snapshots",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("snapshot_date", sa.DateTime(), nullable=False),
            sa.Column("total_memories", sa.Integer(), nullable=True),
            sa.Column("canon_memories", sa.Integer(), nullable=True),
            sa.Column("hypothesis_memories", sa.Integer(), nullable=True),
            sa.Column("deprecated_memories", sa.Integer(), nullable=True),
            sa.Column("avg_confidence", sa.Float(), nullable=True),
            sa.Column("cross_project_applications", sa.Integer(), nullable=True),
            sa.Column("anti_patterns_avoided", sa.Integer(), nullable=True),
            sa.Column("research_experiments_completed", sa.Integer(), nullable=True),
            sa.Column("learning_rate", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_learning_snapshots_date",
            "learning_snapshots",
            ["snapshot_date"],
            unique=False,
        )
