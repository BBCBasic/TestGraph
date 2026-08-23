"""Add user-owned, model-attributed deliberations.

Revision ID: 0014_deliberations
Revises: 0013_clear_review_data
"""
from alembic import op
import sqlalchemy as sa


revision = "0014_deliberations"
down_revision = "0013_clear_review_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deliberations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_key", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("constraints_json", sa.JSON(), nullable=False),
        sa.Column("acceptance_criteria_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("resolution_json", sa.JSON(), nullable=False),
        sa.Column("created_by_client", sa.String(length=200), nullable=False),
        sa.Column("resolved_by_client", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "canonical_key", name="uq_deliberations_owner_key"
        ),
    )
    op.create_index("ix_deliberations_owner_id", "deliberations", ["owner_id"])
    op.create_index("ix_deliberations_status", "deliberations", ["status"])

    op.create_table(
        "deliberation_contributions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deliberation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("contribution_type", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("unresolved_points_json", sa.JSON(), nullable=False),
        sa.Column("responds_to_json", sa.JSON(), nullable=False),
        sa.Column("source_model", sa.String(length=160), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_by_client", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["deliberation_id"], ["deliberations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deliberation_contributions_deliberation_id",
        "deliberation_contributions", ["deliberation_id"],
    )
    op.create_index(
        "ix_deliberation_contributions_user_id",
        "deliberation_contributions", ["user_id"],
    )
    op.create_index(
        "ix_deliberation_contributions_contribution_type",
        "deliberation_contributions", ["contribution_type"],
    )
    op.create_index(
        "ix_deliberation_contributions_created_by_client",
        "deliberation_contributions", ["created_by_client"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_deliberation_contributions_created_by_client",
        table_name="deliberation_contributions",
    )
    op.drop_index(
        "ix_deliberation_contributions_contribution_type",
        table_name="deliberation_contributions",
    )
    op.drop_index(
        "ix_deliberation_contributions_user_id",
        table_name="deliberation_contributions",
    )
    op.drop_index(
        "ix_deliberation_contributions_deliberation_id",
        table_name="deliberation_contributions",
    )
    op.drop_table("deliberation_contributions")
    op.drop_index("ix_deliberations_status", table_name="deliberations")
    op.drop_index("ix_deliberations_owner_id", table_name="deliberations")
    op.drop_table("deliberations")
