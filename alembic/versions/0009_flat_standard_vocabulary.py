"""Replace DNS concepts with stable flat vocabulary IDs.

Revision ID: 0009_flat_standard_vocabulary
Revises: 0008_assessment_experience_target

All affected rows are disposable review/vocabulary data. Users, OAuth clients,
refresh tokens, capability credentials, schema definitions and other
authentication state are deliberately untouched.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_flat_standard_vocabulary"
down_revision = "0008_assessment_experience_target"
branch_labels = None
depends_on = None


def _drop_if_present(name: str) -> None:
    if name in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(name)


def upgrade() -> None:
    for name in (
        "assessments", "v2_experiences", "semantic_alias_proposals",
        "field_aliases", "concept_field_proposals", "concept_fields",
        "v2_subjects", "sources", "concepts",
    ):
        _drop_if_present(name)

    op.create_table("subject_types",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("canonical_name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="provisional"),
        sa.Column("created_by", sa.String(200), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("normalized_name", name="uq_subject_type_normalized_name"),
    )
    op.create_index("ix_subject_types_normalized_name", "subject_types", ["normalized_name"])
    op.create_index("ix_subject_types_status", "subject_types", ["status"])

    op.create_table("subject_type_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_type_id", sa.Uuid(), sa.ForeignKey("subject_types.id"), nullable=False),
        sa.Column("alias", sa.String(160), nullable=False),
        sa.Column("normalized_alias", sa.String(160), nullable=False),
        sa.Column("source", sa.String(200), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("normalized_alias", name="uq_subject_type_normalized_alias"),
    )
    op.create_index("ix_subject_type_aliases_type", "subject_type_aliases", ["subject_type_id"])
    op.create_index("ix_subject_type_aliases_normalized", "subject_type_aliases", ["normalized_alias"])

    op.create_table("subject_type_relationships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_type_id", sa.Uuid(), sa.ForeignKey("subject_types.id"), nullable=False),
        sa.Column("relationship", sa.String(60), nullable=False, server_default="belongs_to"),
        sa.Column("target_type_id", sa.Uuid(), sa.ForeignKey("subject_types.id"), nullable=False),
        sa.Column("source", sa.String(200), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_type_id", "relationship", "target_type_id", name="uq_type_relationship"),
    )
    op.create_index("ix_type_relationship_source", "subject_type_relationships", ["source_type_id"])
    op.create_index("ix_type_relationship_target", "subject_type_relationships", ["target_type_id"])

    op.create_table("field_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("canonical_name", sa.String(160), nullable=False),
        sa.Column("normalized_name", sa.String(160), nullable=False),
        sa.Column("json_schema", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(200), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("normalized_name", name="uq_field_normalized_name"),
    )
    op.create_index("ix_field_definitions_normalized", "field_definitions", ["normalized_name"])

    op.create_table("field_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("field_id", sa.Uuid(), sa.ForeignKey("field_definitions.id"), nullable=False),
        sa.Column("alias", sa.String(160), nullable=False),
        sa.Column("normalized_alias", sa.String(160), nullable=False),
        sa.Column("source", sa.String(200), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("normalized_alias", name="uq_field_normalized_alias"),
    )

    op.create_table("subject_type_fields",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_type_id", sa.Uuid(), sa.ForeignKey("subject_types.id"), nullable=False),
        sa.Column("field_id", sa.Uuid(), sa.ForeignKey("field_definitions.id"), nullable=False),
        sa.Column("source", sa.String(200), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_type_id", "field_id", name="uq_subject_type_field"),
    )

    op.create_table("v2_subjects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_type_id", sa.Uuid(), sa.ForeignKey("subject_types.id"), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("canonical_key", sa.String(300), nullable=False),
        sa.Column("identifiers_json", sa.JSON(), nullable=False),
        sa.Column("attributes_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("subject_type_id", "canonical_key", name="uq_v2_subject_type_key"),
    )
    op.create_index("ix_v2_subjects_type", "v2_subjects", ["subject_type_id"])

    op.create_table("sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_type", sa.String(50), nullable=False), sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("external_id", sa.String(300)), sa.Column("url", sa.Text()), sa.Column("author", sa.String(240)),
        sa.Column("license", sa.String(120)), sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False), sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "external_id", name="uq_source_provider_external"),
    )

    op.create_table("v2_experiences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("v2_subjects.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id")),
        sa.Column("record_type", sa.String(30), nullable=False, server_default="review"),
        sa.Column("experienced_at", sa.DateTime(timezone=True)), sa.Column("headline", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False), sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("structured_data", sa.JSON(), nullable=False), sa.Column("submitted_data", sa.JSON(), nullable=False),
        sa.Column("normalization_log", sa.JSON(), nullable=False), sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("publication_status", sa.String(20), nullable=False), sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_by_client", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )

    op.create_table("assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("v2_subjects.id"), nullable=False),
        sa.Column("experience_id", sa.Uuid(), sa.ForeignKey("v2_experiences.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("assessment_type", sa.String(80), nullable=False), sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("analysis_json", sa.JSON(), nullable=False), sa.Column("conclusion", sa.Text()),
        sa.Column("confidence", sa.Float()), sa.Column("source_model", sa.String(120)),
        sa.Column("provenance", sa.JSON(), nullable=False), sa.Column("created_by_client", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for name in ("assessments", "v2_experiences", "sources", "v2_subjects", "subject_type_fields", "field_aliases", "field_definitions", "subject_type_relationships", "subject_type_aliases", "subject_types"):
        _drop_if_present(name)
