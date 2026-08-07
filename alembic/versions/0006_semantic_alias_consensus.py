"""Add client-proposed semantic alias consensus.

Revision ID: 0006_semantic_alias_consensus
Revises: 0005_tastegraph_v2_concepts
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_semantic_alias_consensus"
down_revision = "0005_tastegraph_v2_concepts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "semantic_alias_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("concept_id", sa.Uuid(), sa.ForeignKey("concepts.id"), nullable=False),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("alias_normalized", sa.String(200), nullable=False),
        sa.Column("target_field_id", sa.Uuid(), sa.ForeignKey("concept_fields.id"), nullable=False),
        sa.Column("proposer_client_id", sa.String(200), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("rationale", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "concept_id", "alias_normalized", "proposer_client_id",
            name="uq_semantic_alias_vote_per_client",
        ),
    )
    op.create_index("ix_semantic_alias_proposals_concept_id", "semantic_alias_proposals", ["concept_id"])
    op.create_index("ix_semantic_alias_proposals_alias_normalized", "semantic_alias_proposals", ["alias_normalized"])
    op.create_index("ix_semantic_alias_proposals_target_field_id", "semantic_alias_proposals", ["target_field_id"])
    op.create_index("ix_semantic_alias_proposals_proposer_client_id", "semantic_alias_proposals", ["proposer_client_id"])


def downgrade() -> None:
    op.drop_table("semantic_alias_proposals")
