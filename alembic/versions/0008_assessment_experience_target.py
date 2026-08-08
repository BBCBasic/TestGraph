"""Attach AI assessments to the exact experience they evaluate.

Revision ID: 0008_assessment_experience_target
Revises: 0007_concept_field_governance
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_assessment_experience_target"
down_revision = "0007_concept_field_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode uses SQLite's copy-and-move strategy while remaining valid on
    # PostgreSQL, so local development and Railway run the same migration.
    with op.batch_alter_table("assessments") as batch_op:
        batch_op.add_column(sa.Column("experience_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_assessments_experience_id",
            "v2_experiences",
            ["experience_id"],
            ["id"],
        )
        batch_op.create_index("ix_assessments_experience_id", ["experience_id"])
        batch_op.add_column(
            sa.Column("created_by_client", sa.String(200), server_default="legacy", nullable=False)
        )
        batch_op.create_index("ix_assessments_created_by_client", ["created_by_client"])


def downgrade() -> None:
    with op.batch_alter_table("assessments") as batch_op:
        batch_op.drop_index("ix_assessments_created_by_client")
        batch_op.drop_column("created_by_client")
        batch_op.drop_index("ix_assessments_experience_id")
        batch_op.drop_constraint("fk_assessments_experience_id", type_="foreignkey")
        batch_op.drop_column("experience_id")
