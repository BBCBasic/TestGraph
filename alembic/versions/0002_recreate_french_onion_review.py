"""Legacy private-data correction marker.

Revision ID: 0002_recreate_soup
Revises: 0001_initial

This migration historically corrected a private development review. The correction
has already run on deployments that contained that record. Fresh installations
must not recreate or embed any private review content, identifiers, attribution,
or conversation-derived evidence.
"""

revision = "0002_recreate_soup"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Intentionally empty. This revision is retained only to preserve the
    # Alembic migration graph for existing installations.
    pass


def downgrade() -> None:
    # Historical private data must never be recreated by a downgrade.
    pass
