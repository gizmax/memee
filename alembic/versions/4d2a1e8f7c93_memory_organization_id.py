"""memory.organization_id + composite indexes

Revision ID: 4d2a1e8f7c93
Revises: 843e414a0596
Create Date: 2026-04-25 12:30:00.000000

R7 multi-tenancy: Memory now carries an organization_id boundary so the
memee-team plugin's visibility hooks can partition by org without a
secondary lookup. Additive change — nullable column on existing rows; no
destructive backfill. ``init_db`` (or ``memee setup``) backfills NULLs
in-place to the default org so existing single-user OSS DBs upgrade
silently.

This migration is symmetrical with what ``init_db`` already does for
SQLite users who never run alembic. Both paths converge on the same
schema.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d2a1e8f7c93"
down_revision: Union[str, Sequence[str], None] = "843e414a0596"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Memory.organization_id + tenancy-hot-path indexes."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("memories")}

    # SQLite ALTER TABLE ADD COLUMN is supported since 3.2; keep it idempotent
    # so a user who already ran init_db (which adds the column at bootstrap)
    # can still run alembic upgrade head without an error.
    if "organization_id" not in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("organization_id", sa.String(length=36), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_memories_organization",
                "organizations",
                ["organization_id"],
                ["id"],
            )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("memories")}

    if "ix_memories_org" not in existing_indexes:
        op.create_index(
            "ix_memories_org",
            "memories",
            ["organization_id"],
            unique=False,
        )
    if "ix_memories_org_type_maturity" not in existing_indexes:
        op.create_index(
            "ix_memories_org_type_maturity",
            "memories",
            ["organization_id", "type", "maturity"],
            unique=False,
        )
    if "ix_memories_org_scope" not in existing_indexes:
        op.create_index(
            "ix_memories_org_scope",
            "memories",
            ["organization_id", "scope"],
            unique=False,
        )

    # Backfill NULL organization_id to the default org. We do this here so
    # alembic-only users converge with the init_db path. If no default org
    # exists yet (very first launch under alembic), we create one — it's the
    # same row init_db would have created at bootstrap.
    default_org_id = bind.execute(
        sa.text("SELECT id FROM organizations WHERE name = 'default' LIMIT 1")
    ).scalar()
    if default_org_id is None:
        # Use the same name init_db uses; the storage layer reads by name.
        default_org_id = sa.func.lower(sa.func.hex(sa.func.randomblob(16)))
        # Fall back to a deterministic literal if the DB driver doesn't
        # surface randomblob (extremely rare on SQLite, never on Postgres).
        try:
            new_id = bind.execute(
                sa.text("SELECT lower(hex(randomblob(16)))")
            ).scalar()
        except Exception:
            new_id = "00000000-0000-0000-0000-000000000001"
        bind.execute(
            sa.text(
                "INSERT INTO organizations (id, name, created_at) "
                "VALUES (:id, 'default', CURRENT_TIMESTAMP)"
            ),
            {"id": new_id},
        )
        default_org_id = new_id

    bind.execute(
        sa.text(
            "UPDATE memories SET organization_id = :oid "
            "WHERE organization_id IS NULL"
        ),
        {"oid": default_org_id},
    )


def downgrade() -> None:
    """Drop the indexes + column. Safe — additive change."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("memories")}

    for name in (
        "ix_memories_org_scope",
        "ix_memories_org_type_maturity",
        "ix_memories_org",
    ):
        if name in existing_indexes:
            op.drop_index(name, table_name="memories")

    columns = {c["name"] for c in inspector.get_columns("memories")}
    if "organization_id" in columns:
        with op.batch_alter_table("memories", schema=None) as batch_op:
            try:
                batch_op.drop_constraint("fk_memories_organization", type_="foreignkey")
            except Exception:
                # SQLite below 3.26 can't drop named FKs; batch op rebuilds.
                pass
            batch_op.drop_column("organization_id")
