"""memory.alpha, memory.beta — Beta-Binomial posterior

Revision ID: 9c2e187f3ab4
Revises: 8f3a52c1d4e7
Create Date: 2026-05-15 14:00:00.000000

v2.4.0 — Tier 1.2 from `docs/memee-2026-roadmap.md`. Replaces the
ad-hoc ``conf += w·(1-conf)`` / ``conf -= 0.12·conf`` update rule
with a proper Beta-Binomial posterior. Each validation contributes
``+w`` to ``alpha``; each invalidation contributes ``+w`` to
``beta``. ``confidence_score`` becomes the derived posterior mean
``α/(α+β)`` (kept populated for callsite compatibility).

Additive, non-destructive:

  * Both columns are ``NOT NULL DEFAULT 1.0`` so every existing row
    starts at Beta(1, 1) — uniform prior with mean 0.5, matches the
    legacy ``confidence_score`` default.
  * Memories with prior validation history (validation_count > 0)
    get backfilled at first read by ``engine.confidence._backfill``
    using ``α = conf*n + 1`` and ``β = (1-conf)*n + 1``. That keeps
    the posterior mean equal to the old ``confidence_score`` while
    preserving the right amount of evidence.

Idempotent: re-running ``alembic upgrade head`` is a no-op once
both columns are present. Symmetric with the ``init_db`` bootstrap
helper ``_bootstrap_memory_alpha_beta`` so SQLite-only deployments
that never run alembic converge on the same schema.

Math anchor: Bayes Rules! ch. 3 (Beta-Binomial conjugacy); Paun
et al. 2018 (Bayesian hierarchical Dawid-Skene).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c2e187f3ab4"
down_revision: Union[str, Sequence[str], None] = "8f3a52c1d4e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Memory.alpha, Memory.beta with Beta(1, 1) uniform-prior default."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("memories")}

    if "alpha" not in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "alpha",
                    sa.Float(),
                    nullable=False,
                    server_default=sa.text("1.0"),
                )
            )

    if "beta" not in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "beta",
                    sa.Float(),
                    nullable=False,
                    server_default=sa.text("1.0"),
                )
            )


def downgrade() -> None:
    """Drop the columns. Safe — additive change, no downstream FKs."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("memories")}

    if "beta" in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.drop_column("beta")

    if "alpha" in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.drop_column("alpha")
