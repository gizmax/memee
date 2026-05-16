"""memory.is_authoritative — Layer 0.5 pin flag

Revision ID: 8f3a52c1d4e7
Revises: 6d540c223770
Create Date: 2026-05-15 09:00:00.000000

v2.3.1 addition. A boolean on every Memory row marking it as
authoritative — surfaces in the smart router's new Layer 0.5 for any
task whose tags overlap, regardless of cosine/BM25 score. Acts on
Mem0 issue #4926 and Letta issue #3116: policy / persona memories
get out-ranked by conversational similarity hits and never reach the
agent.

Additive, non-destructive: column has a NOT NULL DEFAULT 0 so every
existing row keeps the same routing behaviour it had before. Users
opt in per memory via ``memee record --authoritative`` or the
``memory_record`` MCP tool's ``is_authoritative`` parameter.

Idempotent: re-running ``alembic upgrade head`` is a no-op once the
column is present (init_db at boot also adds it, so SQLite-only
deployments that never run alembic converge on the same schema).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8f3a52c1d4e7"
down_revision: Union[str, Sequence[str], None] = "6d540c223770"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Memory.is_authoritative + a covering index for Layer 0.5 queries."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("memories")}

    if "is_authoritative" not in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            # NOT NULL DEFAULT 0 so the upgrade fills existing rows
            # in-place without a separate UPDATE step. SQLite handles
            # this via the batch_op rebuild; Postgres handles it natively.
            batch_op.add_column(
                sa.Column(
                    "is_authoritative",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("memories")}
    # Partial / filtered index keeps the table small even when the
    # column gets populated (most rows stay False). The router's Layer
    # 0.5 query is ``WHERE is_authoritative = TRUE`` with a tag-overlap
    # filter applied in Python, so this index serves the whole query
    # path. SQLite's `WHERE` clause on CREATE INDEX is supported since
    # 3.8 — well below our floor.
    if "ix_memories_authoritative" not in existing_indexes:
        op.create_index(
            "ix_memories_authoritative",
            "memories",
            ["is_authoritative"],
            unique=False,
            sqlite_where=sa.text("is_authoritative = 1"),
            postgresql_where=sa.text("is_authoritative IS TRUE"),
        )


def downgrade() -> None:
    """Drop the index + column. Safe — additive change."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("memories")}

    if "ix_memories_authoritative" in existing_indexes:
        op.drop_index("ix_memories_authoritative", table_name="memories")

    columns = {c["name"] for c in inspector.get_columns("memories")}
    if "is_authoritative" in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.drop_column("is_authoritative")
