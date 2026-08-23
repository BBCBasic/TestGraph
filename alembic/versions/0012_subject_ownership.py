"""Track ownership of subjects for safe user-scoped deletion.

Revision ID: 0012_subject_ownership
Revises: 0011_subject_relationships
"""
from alembic import op
import sqlalchemy as sa


revision = "0012_subject_ownership"
down_revision = "0011_subject_relationships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("v2_subjects") as batch:
        batch.add_column(sa.Column("owner_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_v2_subjects_owner_id_users", "users", ["owner_id"], ["id"]
        )
        batch.create_index("ix_v2_subjects_owner_id", ["owner_id"])

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        """
        SELECT e.subject_id, e.owner_id
        FROM v2_experiences AS e
        JOIN (
            SELECT subject_id
            FROM v2_experiences
            GROUP BY subject_id
            HAVING COUNT(DISTINCT owner_id) = 1
        ) AS single_owner ON single_owner.subject_id = e.subject_id
        GROUP BY e.subject_id, e.owner_id
        """
    )).fetchall()
    for subject_id, owner_id in rows:
        bind.execute(
            sa.text(
                "UPDATE v2_subjects SET owner_id = :owner_id "
                "WHERE id = :subject_id AND owner_id IS NULL"
            ),
            {"owner_id": owner_id, "subject_id": subject_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("v2_subjects") as batch:
        batch.drop_index("ix_v2_subjects_owner_id")
        batch.drop_constraint("fk_v2_subjects_owner_id_users", type_="foreignkey")
        batch.drop_column("owner_id")
