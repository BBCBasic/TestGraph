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


def _state():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("assessments")}
    indexes = {index["name"] for index in inspector.get_indexes("assessments")}
    foreign_keys = {
        fk.get("name")
        for fk in inspector.get_foreign_keys("assessments")
        if fk.get("name")
    }
    return columns, indexes, foreign_keys


def upgrade() -> None:
    # This migration may be retried after a failed Railway pre-deploy. Inspect
    # the current schema and only create pieces that are still missing.
    columns, indexes, foreign_keys = _state()

    with op.batch_alter_table("assessments") as batch_op:
        if "experience_id" not in columns:
            batch_op.add_column(sa.Column("experience_id", sa.Uuid(), nullable=True))
        if "created_by_client" not in columns:
            batch_op.add_column(
                sa.Column(
                    "created_by_client",
                    sa.String(200),
                    server_default="legacy",
                    nullable=False,
                )
            )
        if "fk_assessments_experience_id" not in foreign_keys:
            batch_op.create_foreign_key(
                "fk_assessments_experience_id",
                "v2_experiences",
                ["experience_id"],
                ["id"],
            )
        if "ix_assessments_experience_id" not in indexes:
            batch_op.create_index("ix_assessments_experience_id", ["experience_id"])
        if "ix_assessments_created_by_client" not in indexes:
            batch_op.create_index("ix_assessments_created_by_client", ["created_by_client"])


def downgrade() -> None:
    columns, indexes, foreign_keys = _state()

    with op.batch_alter_table("assessments") as batch_op:
        if "ix_assessments_created_by_client" in indexes:
            batch_op.drop_index("ix_assessments_created_by_client")
        if "ix_assessments_experience_id" in indexes:
            batch_op.drop_index("ix_assessments_experience_id")
        if "fk_assessments_experience_id" in foreign_keys:
            batch_op.drop_constraint("fk_assessments_experience_id", type_="foreignkey")
        if "created_by_client" in columns:
            batch_op.drop_column("created_by_client")
        if "experience_id" in columns:
            batch_op.drop_column("experience_id")
