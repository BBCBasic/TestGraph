"""Remove graph state stranded by the pre-atomic Rockwealth save failure.

Revision ID: 0019_cleanup_stranded_rockwealth
Revises: 0018_site_page_views

This is a deliberately narrow data correction. It removes only the three known
unreviewed subjects created by the failed Rockwealth save_experience attempt.
If any of those IDs now identifies different data, or has acquired a review,
the migration aborts rather than deleting it.
"""
from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa


revision = "0019_cleanup_stranded_rockwealth"
down_revision = "0018_site_page_views"
branch_labels = None
depends_on = None


STRANDED = {
    uuid.UUID("b5475322-65bd-418d-b7aa-16235eadc5ad"): "rockwealth",
    uuid.UUID("e4e47935-853a-492f-bb67-ec3903bfa5f3"): "rockwealth-cotswolds-cirencester",
    uuid.UUID("6cd38435-fc4a-47ea-874e-65f0372f8f94"): "rockwealth-cheltenham",
}


def upgrade() -> None:
    bind = op.get_bind()
    subjects = sa.table(
        "v2_subjects",
        sa.column("id", sa.Uuid()),
        sa.column("canonical_key", sa.String()),
    )
    experiences = sa.table(
        "v2_experiences",
        sa.column("id", sa.Uuid()),
        sa.column("subject_id", sa.Uuid()),
    )
    relationships = sa.table(
        "subject_relationships",
        sa.column("source_subject_id", sa.Uuid()),
        sa.column("target_subject_id", sa.Uuid()),
    )

    present_ids = []
    for subject_id, expected_key in STRANDED.items():
        actual_key = bind.scalar(
            sa.select(subjects.c.canonical_key).where(subjects.c.id == subject_id)
        )
        if actual_key is None:
            continue
        if actual_key != expected_key:
            raise RuntimeError(
                f"Refusing Rockwealth cleanup: {subject_id} is now {actual_key!r}, "
                f"expected {expected_key!r}."
            )
        present_ids.append(subject_id)

    if not present_ids:
        return

    review_count = bind.scalar(
        sa.select(sa.func.count()).select_from(experiences).where(
            experiences.c.subject_id.in_(present_ids)
        )
    )
    if review_count:
        raise RuntimeError(
            "Refusing Rockwealth cleanup because a stranded subject now has a review."
        )

    bind.execute(
        sa.delete(relationships).where(
            sa.or_(
                relationships.c.source_subject_id.in_(present_ids),
                relationships.c.target_subject_id.in_(present_ids),
            )
        )
    )

    for subject_id, expected_key in STRANDED.items():
        if subject_id in present_ids:
            bind.execute(
                sa.delete(subjects).where(
                    subjects.c.id == subject_id,
                    subjects.c.canonical_key == expected_key,
                )
            )


def downgrade() -> None:
    # The removed rows were invalid partial state from a failed operation and
    # must not be recreated by a downgrade.
    pass
