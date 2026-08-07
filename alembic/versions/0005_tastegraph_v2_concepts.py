"""Add TasteGraph v2 concept registry and flexible storage.

Revision ID: 0005_tastegraph_v2_concepts
Revises: 0004_capability_credentials
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_tastegraph_v2_concepts"
down_revision = "0004_capability_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "concepts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("path", sa.String(300), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("concepts.id")),
        sa.Column("description", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("definition_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_concepts_path", "concepts", ["path"])
    op.create_index("ix_concepts_name", "concepts", ["name"])
    op.create_index("ix_concepts_parent_id", "concepts", ["parent_id"])
    op.create_index("ix_concepts_status", "concepts", ["status"])

    op.create_table(
        "concept_fields",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("concept_id", sa.Uuid(), sa.ForeignKey("concepts.id"), nullable=False),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("data_type", sa.String(30), nullable=False, server_default="any"),
        sa.Column("description", sa.Text()),
        sa.Column("unit", sa.String(50)),
        sa.Column("allowed_values", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("introduced_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("concept_id", "canonical_name", name="uq_concept_field_name"),
    )
    op.create_index("ix_concept_fields_concept_id", "concept_fields", ["concept_id"])
    op.create_index("ix_concept_fields_canonical_name", "concept_fields", ["canonical_name"])
    op.create_index("ix_concept_fields_status", "concept_fields", ["status"])

    op.create_table(
        "field_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("concept_id", sa.Uuid(), sa.ForeignKey("concepts.id"), nullable=False),
        sa.Column("field_id", sa.Uuid(), sa.ForeignKey("concept_fields.id"), nullable=False),
        sa.Column("alias", sa.String(200), nullable=False),
        sa.Column("alias_normalized", sa.String(200), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(120), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("concept_id", "alias_normalized", name="uq_concept_alias"),
    )
    op.create_index("ix_field_aliases_concept_id", "field_aliases", ["concept_id"])
    op.create_index("ix_field_aliases_field_id", "field_aliases", ["field_id"])
    op.create_index("ix_field_aliases_alias_normalized", "field_aliases", ["alias_normalized"])

    op.create_table(
        "v2_subjects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("concept_id", sa.Uuid(), sa.ForeignKey("concepts.id"), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("canonical_key", sa.String(300), nullable=False),
        sa.Column("identifiers_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("attributes_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("concept_id", "canonical_key", name="uq_v2_subject_concept_key"),
    )
    op.create_index("ix_v2_subjects_concept_id", "v2_subjects", ["concept_id"])
    op.create_index("ix_v2_subjects_name", "v2_subjects", ["name"])
    op.create_index("ix_v2_subjects_canonical_key", "v2_subjects", ["canonical_key"])

    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("external_id", sa.String(300)),
        sa.Column("url", sa.Text()),
        sa.Column("author", sa.String(240)),
        sa.Column("license", sa.String(120)),
        sa.Column("raw_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "external_id", name="uq_source_provider_external"),
    )
    op.create_index("ix_sources_source_type", "sources", ["source_type"])
    op.create_index("ix_sources_provider", "sources", ["provider"])
    op.create_index("ix_sources_external_id", "sources", ["external_id"])

    op.create_table(
        "v2_experiences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("v2_subjects.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id")),
        sa.Column("experienced_at", sa.DateTime(timezone=True)),
        sa.Column("headline", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text()),
        sa.Column("structured_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("submitted_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("normalization_log", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="private"),
        sa.Column("publication_status", sa.String(20), nullable=False, server_default="published"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by_client", sa.String(120), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    for name, column in [
        ("ix_v2_experiences_owner_id", "owner_id"),
        ("ix_v2_experiences_subject_id", "subject_id"),
        ("ix_v2_experiences_source_id", "source_id"),
        ("ix_v2_experiences_experienced_at", "experienced_at"),
        ("ix_v2_experiences_visibility", "visibility"),
        ("ix_v2_experiences_publication_status", "publication_status"),
    ]:
        op.create_index(name, "v2_experiences", [column])

    op.create_table(
        "assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("v2_subjects.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("assessment_type", sa.String(80), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("analysis_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("conclusion", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("source_model", sa.String(120)),
        sa.Column("provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assessments_subject_id", "assessments", ["subject_id"])
    op.create_index("ix_assessments_user_id", "assessments", ["user_id"])
    op.create_index("ix_assessments_assessment_type", "assessments", ["assessment_type"])


def downgrade() -> None:
    op.drop_table("assessments")
    op.drop_table("v2_experiences")
    op.drop_table("sources")
    op.drop_table("v2_subjects")
    op.drop_table("field_aliases")
    op.drop_table("concept_fields")
    op.drop_table("concepts")
