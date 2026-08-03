"""Recreate the French onion soup review as sparse, evidence-backed data.

Revision ID: 0002_recreate_soup
Revises: 0001_initial
"""

from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0002_recreate_soup"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


OLD_EXPERIENCE_ID = uuid.UUID("ed9b6249-3d8c-47a0-b45a-b014b711c4b7")
NEW_EXPERIENCE_ID = uuid.UUID("7f159803-4a90-4a82-8a09-5d9157f8b7a3")
OWNER_ID = uuid.UUID("2f822096-72f9-44f1-9b47-e3aaa47225f4")
SUBJECT_ID = uuid.UUID("f33838a7-8861-494e-8d85-f97798ab268d")


experiences = sa.table(
    "experiences",
    sa.column("id", sa.Uuid()),
    sa.column("owner_id", sa.Uuid()),
    sa.column("subject_id", sa.Uuid()),
    sa.column("subject_type", sa.String()),
    sa.column("schema_version", sa.String()),
    sa.column("publication_status", sa.String()),
    sa.column("visibility", sa.String()),
    sa.column("version", sa.Integer()),
    sa.column("headline", sa.String()),
    sa.column("summary", sa.Text()),
    sa.column("common_data", sa.JSON()),
    sa.column("domain_data", sa.JSON()),
    sa.column("provenance", sa.JSON()),
    sa.column("consent", sa.JSON()),
    sa.column("created_by_client", sa.String()),
    sa.column("auth_subject", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
    sa.column("published_at", sa.DateTime(timezone=True)),
    sa.column("deleted_at", sa.DateTime(timezone=True)),
    sa.column("deleted_by", sa.String()),
)


def upgrade() -> None:
    connection = op.get_bind()
    now = datetime.now(timezone.utc)

    old_exists = connection.scalar(
        sa.select(experiences.c.id).where(experiences.c.id == OLD_EXPERIENCE_ID)
    )
    # A fresh installation has no production review to replace. Keeping this
    # migration conditional prevents production-specific seed data appearing
    # in new databases.
    if old_exists is None:
        return

    connection.execute(
        experiences.update()
        .where(experiences.c.id == OLD_EXPERIENCE_ID)
        .where(experiences.c.deleted_at.is_(None))
        .values(deleted_at=now, deleted_by="schema-migration-0002")
    )

    already_created = connection.scalar(
        sa.select(experiences.c.id).where(experiences.c.id == NEW_EXPERIENCE_ID)
    )
    if already_created is not None:
        return

    summary = (
        "I make French onion soup from Delia Smith's recipe. The key is to take "
        "your time with the onions: sweat them down for much longer than you "
        "think, then caramelise them."
    )
    connection.execute(
        experiences.insert().values(
            id=NEW_EXPERIENCE_ID,
            owner_id=OWNER_ID,
            subject_id=SUBJECT_ID,
            subject_type="recipe",
            schema_version="1.0",
            publication_status="published",
            visibility="public",
            version=1,
            headline="The key is patience with the onions",
            summary=summary,
            common_data={
                "observations": [{
                    "category": "technique",
                    "statement": (
                        "Sweat the onions for much longer than expected, then "
                        "caramelise them."
                    ),
                    "confidence": 1.0,
                }]
            },
            domain_data={},
            provenance={
                "source_method": "llm_conversation",
                "source_client": "ChatGPT",
                "attribution": "Robert Stevens",
                "raw_conversation_stored": False,
                "raw_conversation_published": False,
                "inferred_fields": [],
                "source_metadata": {
                    "field_evidence": [{
                        "field": "common_data.observations[0]",
                        "provenance": "asserted",
                        "supporting_text": (
                            "the key is to take your time with the onions sweat "
                            "them down for much longer than u think then canalise them"
                        ),
                    }]
                },
                "notes": (
                    "Recreated from Robert's earlier first-person review after "
                    "the schema update; no numeric ratings inferred."
                ),
            },
            consent={
                "user_approved": True,
                "authorization_basis": "user_approval",
                "approved_at": "2026-08-02T21:26:04.031249Z",
                "approved_version": 1,
            },
            created_by_client="schema-migration-0002",
            auth_subject="development-user",
            created_at=now,
            updated_at=now,
            published_at=now,
            deleted_at=None,
            deleted_by=None,
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        experiences.delete().where(experiences.c.id == NEW_EXPERIENCE_ID)
    )
    connection.execute(
        experiences.update()
        .where(experiences.c.id == OLD_EXPERIENCE_ID)
        .values(deleted_at=None, deleted_by=None)
    )
