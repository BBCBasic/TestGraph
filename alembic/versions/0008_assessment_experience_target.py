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
    op.add_column("assessments", sa.Column("experience_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_assessments_experience_id",
        "assessments",
        "v2_experiences",
        ["experience_id"],
        ["id"],
    )
    op.create_index("ix_assessments_experience_id", "assessments", ["experience_id"])
    op.add_column(
        "assessments",
        sa.Column("created_by_client", sa.String(200), server_default="legacy", nullable=False),
    )
    op.create_index("ix_assessments_created_by_client", "assessments", ["created_by_client"])


def downgrade() -> None:
    op.drop_index("ix_assessments_created_by_client", table_name="assessments")
    op.drop_column("assessments", "created_by_client")
    op.drop_index("ix_assessments_experience_id", table_name="assessments")
    op.drop_constraint("fk_assessments_experience_id", "assessments", type_="foreignkey")
    op.drop_column("assessments", "experience_id")
