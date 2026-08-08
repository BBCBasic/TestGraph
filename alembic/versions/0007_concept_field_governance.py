"""Add manually governed concept field proposals.

Revision ID: 0007_concept_field_governance
Revises: 0006_semantic_alias_consensus
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_concept_field_governance"
down_revision = "0006_semantic_alias_consensus"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "concept_field_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("concept_id", sa.Uuid(), sa.ForeignKey("concepts.id"), nullable=False),
        sa.Column("submitted_name", sa.String(200), nullable=False),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("canonical_name_normalized", sa.String(200), nullable=False),
        sa.Column("json_schema", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("aliases_json", sa.JSON(), nullable=False),
        sa.Column("proposer_client_id", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decision_by", sa.String(120)),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "concept_id", "canonical_name_normalized",
            name="uq_concept_field_proposal_name",
        ),
    )
    op.create_index("ix_concept_field_proposals_concept_id", "concept_field_proposals", ["concept_id"])
    op.create_index("ix_concept_field_proposals_canonical_name_normalized", "concept_field_proposals", ["canonical_name_normalized"])
    op.create_index("ix_concept_field_proposals_proposer_client_id", "concept_field_proposals", ["proposer_client_id"])
    op.create_index("ix_concept_field_proposals_status", "concept_field_proposals", ["status"])


def downgrade() -> None:
    op.drop_table("concept_field_proposals")
