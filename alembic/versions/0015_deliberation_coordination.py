"""Add deliberation discovery, claiming and contribution verification.

Revision ID: 0015_deliberation_coordination
Revises: 0014_deliberations
"""
from alembic import op
import sqlalchemy as sa


revision = "0015_deliberation_coordination"
down_revision = "0014_deliberations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deliberations", sa.Column("target_model", sa.String(160), nullable=True))
    op.add_column("deliberations", sa.Column("claimed_by_client", sa.String(200), nullable=True))
    op.add_column("deliberations", sa.Column("claimed_by_model", sa.String(160), nullable=True))
    op.add_column("deliberations", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_deliberations_target_model", "deliberations", ["target_model"])
    op.create_index("ix_deliberations_claimed_by_client", "deliberations", ["claimed_by_client"])
    op.add_column("deliberation_contributions", sa.Column("verification_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("deliberation_contributions", "verification_json")
    op.drop_index("ix_deliberations_claimed_by_client", table_name="deliberations")
    op.drop_index("ix_deliberations_target_model", table_name="deliberations")
    op.drop_column("deliberations", "claimed_at")
    op.drop_column("deliberations", "claimed_by_model")
    op.drop_column("deliberations", "claimed_by_client")
    op.drop_column("deliberations", "target_model")
