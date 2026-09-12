"""Add the synthetic universal is_a root.

Revision ID: 0024_synthetic_universal_root
Revises: 0023_semantic_head_bale_migration

Existing vocabulary is disposable test data, so this migration deliberately does not
backfill or rewrite any existing classification edges.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0024_synthetic_universal_root"
down_revision = "0023_semantic_head_bale_migration"
branch_labels = None
depends_on = None

ROOT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    op.add_column(
        "subject_types",
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_subject_types_is_synthetic", "subject_types", ["is_synthetic"])
    op.create_index(
        "uq_subject_types_single_synthetic",
        "subject_types",
        ["is_synthetic"],
        unique=True,
        postgresql_where=sa.text("is_synthetic"),
        sqlite_where=sa.text("is_synthetic = 1"),
    )
    now = datetime.now(timezone.utc)
    subject_types = sa.table(
        "subject_types",
        sa.column("id", sa.Uuid()),
        sa.column("canonical_name", sa.String()),
        sa.column("normalized_name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("is_synthetic", sa.Boolean()),
        sa.column("public_location_eligible", sa.Boolean()),
        sa.column("created_by", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.get_bind().execute(subject_types.insert().values(
        id=ROOT_ID,
        canonical_name=".",
        normalized_name=".",
        description="Non-semantic universal root of the TestGraph is_a topology.",
        status="system",
        is_synthetic=True,
        public_location_eligible=False,
        created_by="system:synthetic-root",
        created_at=now,
        updated_at=now,
    ))


def downgrade() -> None:
    bind = op.get_bind()
    relationships = sa.table(
        "subject_type_relationships",
        sa.column("source_type_id", sa.Uuid()),
        sa.column("target_type_id", sa.Uuid()),
    )
    subject_types = sa.table("subject_types", sa.column("id", sa.Uuid()))
    bind.execute(relationships.delete().where(sa.or_(
        relationships.c.source_type_id == ROOT_ID,
        relationships.c.target_type_id == ROOT_ID,
    )))
    bind.execute(subject_types.delete().where(subject_types.c.id == ROOT_ID))
    op.drop_index("uq_subject_types_single_synthetic", table_name="subject_types")
    op.drop_index("ix_subject_types_is_synthetic", table_name="subject_types")
    op.drop_column("subject_types", "is_synthetic")
