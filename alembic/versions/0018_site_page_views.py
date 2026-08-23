"""Add privacy-preserving site page view counter.

Revision ID: 0018_site_page_views
Revises: 0017_revoke_exposed_test_capabilities
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_site_page_views"
down_revision = "0017_revoke_exposed_test_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_counters",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.execute("INSERT INTO site_counters (key, value) VALUES ('landing_page_views', 0)")


def downgrade() -> None:
    op.drop_table("site_counters")
