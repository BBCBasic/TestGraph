"""Add static capability credentials.

Revision ID: 0004_capability_credentials
Revises: 0003_merge_oauth_review
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_capability_credentials"
down_revision = "0003_merge_oauth_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capability_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_capability_credentials_user_id", "capability_credentials", ["user_id"])
    op.create_index("ix_capability_credentials_key_hash", "capability_credentials", ["key_hash"])


def downgrade() -> None:
    op.drop_table("capability_credentials")
