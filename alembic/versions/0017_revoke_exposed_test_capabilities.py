"""Revoke capability credentials exposed by historical live-test commits.

Revision ID: 0017_revoke_exposed_test_capabilities
Revises: 0016_location_assertions
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0017_revoke_exposed_test_capabilities"
down_revision = "0016_location_assertions"
branch_labels = None
depends_on = None


# SHA-256 hashes only. The raw capability keys must never be reintroduced here.
EXPOSED_TEST_CAPABILITY_HASHES = (
    "b8aee27d4d798fdd9abf7561b7cb33d1c972167870b966a7d7fcf5fec43fb490",
    "2c133867a062fc3bd75fa6bca36de396a5dd0226fd055abe2a9221adc20d6701",
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE capability_credentials "
            "SET revoked_at = COALESCE(revoked_at, :revoked_at) "
            "WHERE key_hash IN (:hash_1, :hash_2)"
        ),
        {
            "revoked_at": datetime.now(timezone.utc),
            "hash_1": EXPOSED_TEST_CAPABILITY_HASHES[0],
            "hash_2": EXPOSED_TEST_CAPABILITY_HASHES[1],
        },
    )


def downgrade() -> None:
    # Revocation is intentionally irreversible. A downgrade must not reactivate
    # credentials that have appeared in Git history.
    pass
