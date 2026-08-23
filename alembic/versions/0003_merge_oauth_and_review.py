"""Merge the OAuth and sparse-review migration branches.

Revision ID: 0003_merge_oauth_review
Revises: 0002_oauth, 0002_recreate_soup
"""

revision = "0003_merge_oauth_review"
down_revision = ("0002_oauth", "0002_recreate_soup")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
