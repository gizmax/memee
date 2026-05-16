"""memory.half_life + memory.last_retrieved — FSRS-light decay

Revision ID: c3f9e8a1b4d2
Revises: 9c2e187f3ab4
Create Date: 2026-05-15 16:00:00.000000

v2.4.5 — Tier 1.5 from ``docs/memee-2026-roadmap.md``. Adds two
columns that let the router's Layer 0.7 schedule re-validation by
per-memory predicted retrievability (FSRS-style) instead of a global
30-day cliff.

  * ``half_life`` (Float, NOT NULL DEFAULT 14.0) — days for the
    predicted-recall probability to halve. Grows on successful
    validation, shrinks on invalidation. Anki/FSRS use a comparable
    initial stability around two weeks.
  * ``last_retrieved`` (DateTime, NULLable) — when this memory was
    last surfaced via search / brief / verify. The recall clock that
    half_life decays against.

Additive, non-destructive. Default half_life=14 means every legacy row
starts at "two-week half-life" — the v2.4.0 Beta-Binomial backfill
already populated α/β, so legacy lifecycle behaviour is preserved
exactly until the first new retrieval lands.

Symmetric with ``init_db._bootstrap_memory_fsrs_light``: SQLite-only
deployments converge on the same schema without running alembic.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f9e8a1b4d2"
down_revision: Union[str, Sequence[str], None] = "9c2e187f3ab4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Memory.half_life + Memory.last_retrieved."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("memories")}

    if "half_life" not in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "half_life",
                    sa.Float(),
                    nullable=False,
                    server_default=sa.text("14.0"),
                )
            )

    if "last_retrieved" not in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("last_retrieved", sa.DateTime(), nullable=True)
            )


def downgrade() -> None:
    """Drop both columns. Safe — additive change."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("memories")}

    if "last_retrieved" in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.drop_column("last_retrieved")
    if "half_life" in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.drop_column("half_life")
