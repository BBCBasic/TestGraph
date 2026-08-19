"""Add related-subject graph and subject provenance.

Revision ID: 0011_subject_relationships
Revises: 0010_retired_relationships
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_subject_relationships"
down_revision = "0010_retired_relationships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "v2_subjects",
        sa.Column("provenance_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "subject_relationships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_subject_id", sa.Uuid(), nullable=False),
        sa.Column("relationship", sa.String(60), nullable=False),
        sa.Column("target_subject_id", sa.Uuid(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(200), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_subject_id"], ["v2_subjects.id"]),
        sa.ForeignKeyConstraint(["target_subject_id"], ["v2_subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_subject_id", "relationship", "target_subject_id",
            name="uq_subject_relationship",
        ),
    )
    op.create_index(
        "ix_subject_relationships_source_subject_id",
        "subject_relationships", ["source_subject_id"],
    )
    op.create_index(
        "ix_subject_relationships_target_subject_id",
        "subject_relationships", ["target_subject_id"],
    )
    op.create_index(
        "ix_subject_relationships_relationship",
        "subject_relationships", ["relationship"],
    )
    op.create_index(
        "ix_subject_relationships_status",
        "subject_relationships", ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_subject_relationships_status", table_name="subject_relationships")
    op.drop_index("ix_subject_relationships_relationship", table_name="subject_relationships")
    op.drop_index("ix_subject_relationships_target_subject_id", table_name="subject_relationships")
    op.drop_index("ix_subject_relationships_source_subject_id", table_name="subject_relationships")
    op.drop_table("subject_relationships")
    op.drop_column("v2_subjects", "provenance_json")
