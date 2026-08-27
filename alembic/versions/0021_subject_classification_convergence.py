"""Add convergent, locked subject classification.

Revision ID: 0021_subject_classification_convergence
Revises: 0020_google_identity
"""
from alembic import op
import sqlalchemy as sa


revision = "0021_subject_classification_convergence"
down_revision = "0020_google_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("v2_subjects") as batch:
        batch.add_column(sa.Column("classification_status", sa.String(20), nullable=False, server_default="provisional"))
        batch.add_column(sa.Column("classification_version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("classification_locked_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_v2_subjects_classification_status", ["classification_status"])

    op.create_table(
        "subject_classification_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("v2_subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("classification_version", sa.Integer(), nullable=False),
        sa.Column("from_type_id", sa.Uuid(), sa.ForeignKey("subject_types.id"), nullable=False),
        sa.Column("target_type_id", sa.Uuid(), sa.ForeignKey("subject_types.id"), nullable=False),
        sa.Column("source_model", sa.String(160), nullable=False),
        sa.Column("source_client", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_id", "classification_version", "source_model", name="uq_subject_classification_model_version"),
    )
    for name, columns in (
        ("ix_subject_classification_decisions_subject_id", ["subject_id"]),
        ("ix_subject_classification_decisions_classification_version", ["classification_version"]),
        ("ix_subject_classification_decisions_from_type_id", ["from_type_id"]),
        ("ix_subject_classification_decisions_target_type_id", ["target_type_id"]),
        ("ix_subject_classification_decisions_source_model", ["source_model"]),
        ("ix_subject_classification_decisions_evidence_fingerprint", ["evidence_fingerprint"]),
        ("ix_subject_classification_decisions_outcome", ["outcome"]),
    ):
        op.create_index(name, "subject_classification_decisions", columns)


def downgrade() -> None:
    op.drop_table("subject_classification_decisions")
    with op.batch_alter_table("v2_subjects") as batch:
        batch.drop_index("ix_v2_subjects_classification_status")
        batch.drop_column("classification_locked_at")
        batch.drop_column("classification_version")
        batch.drop_column("classification_status")
