"""Preserve retired semantic relationship tombstones.

Revision ID: 0010_retired_relationships
Revises: 0009_flat_standard_vocabulary
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_retired_relationships"
down_revision = "0009_flat_standard_vocabulary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subject_type_relationships",
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    )
    op.add_column(
        "subject_type_relationships",
        sa.Column("retired_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "subject_type_relationships",
        sa.Column("retired_by", sa.String(200), nullable=True),
    )
    op.add_column(
        "subject_type_relationships",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_subject_type_relationships_status",
        "subject_type_relationships",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_subject_type_relationships_status",
        table_name="subject_type_relationships",
    )
    op.drop_column("subject_type_relationships", "retired_at")
    op.drop_column("subject_type_relationships", "retired_by")
    op.drop_column("subject_type_relationships", "retired_reason")
    op.drop_column("subject_type_relationships", "status")
