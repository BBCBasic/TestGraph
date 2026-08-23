"""Clear disposable TestGraph review and graph data while preserving authentication.

Revision ID: 0013_clear_review_data
Revises: 0012_subject_ownership
"""
from alembic import op


revision = "0013_clear_review_data"
down_revision = "0012_subject_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Derived v2 data must be removed before the records it references.
    for table in (
        "assessments",
        "subject_relationships",
        "v2_experiences",
        "v2_subjects",
        "sources",
        "subject_type_fields",
        "field_aliases",
        "field_definitions",
        "subject_type_aliases",
        "subject_type_relationships",
        "subject_types",
        # Legacy review data and review-derived state.
        "profile_signals",
        "pairwise_alignments",
        "experiences",
        "subjects",
        "schema_definitions",
        # These may contain payloads or references to deleted test records.
        "idempotency_records",
        "audit_events",
    ):
        op.execute(f"DELETE FROM {table}")

    # Intentionally preserved:
    # users, oauth_clients, oauth_authorization_codes,
    # oauth_refresh_tokens, capability_credentials.


def downgrade() -> None:
    # Deleted disposable test data cannot be reconstructed.
    pass
