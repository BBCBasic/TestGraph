"""Migrate descriptor-derived bale vocabulary to a semantic-head type.

Revision ID: 0023_semantic_head_bale_migration
Revises: 0022_workflows_and_mcp_audit
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0023_semantic_head_bale_migration"
down_revision = "0022_workflows_and_mcp_audit"
branch_labels = None
depends_on = None


subject_types = sa.table(
    "subject_types",
    sa.column("id", sa.Uuid()),
    sa.column("canonical_name", sa.String()),
    sa.column("normalized_name", sa.String()),
    sa.column("description", sa.Text()),
    sa.column("status", sa.String()),
    sa.column("public_location_eligible", sa.Boolean()),
    sa.column("created_by", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
relationships = sa.table(
    "subject_type_relationships",
    sa.column("id", sa.Uuid()),
    sa.column("source_type_id", sa.Uuid()),
    sa.column("relationship", sa.String()),
    sa.column("target_type_id", sa.Uuid()),
    sa.column("source", sa.String()),
    sa.column("status", sa.String()),
    sa.column("retired_reason", sa.Text()),
    sa.column("retired_by", sa.String()),
    sa.column("retired_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
subjects = sa.table(
    "v2_subjects",
    sa.column("id", sa.Uuid()),
    sa.column("subject_type_id", sa.Uuid()),
    sa.column("canonical_key", sa.String()),
    sa.column("attributes_json", sa.JSON()),
    sa.column("classification_status", sa.String()),
    sa.column("classification_version", sa.Integer()),
    sa.column("classification_locked_at", sa.DateTime(timezone=True)),
)
aliases = sa.table(
    "subject_type_aliases",
    sa.column("id", sa.Uuid()),
    sa.column("subject_type_id", sa.Uuid()),
    sa.column("alias", sa.String()),
    sa.column("normalized_alias", sa.String()),
    sa.column("source", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
type_fields = sa.table(
    "subject_type_fields",
    sa.column("id", sa.Uuid()),
    sa.column("subject_type_id", sa.Uuid()),
    sa.column("field_id", sa.Uuid()),
    sa.column("source", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def _type_row(bind, normalized_name: str):
    return bind.execute(
        sa.select(subject_types).where(subject_types.c.normalized_name == normalized_name)
    ).mappings().first()


def _ensure_alias(bind, bale_id, alias: str, now) -> None:
    normalized = alias.casefold()
    existing = bind.execute(
        sa.select(aliases.c.id).where(aliases.c.normalized_alias == normalized)
    ).first()
    if not existing:
        bind.execute(aliases.insert().values(
            id=uuid.uuid4(), subject_type_id=bale_id, alias=alias,
            normalized_alias=normalized, source="migration:0023", created_at=now,
        ))


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    legacy = [row for row in (_type_row(bind, "bale group"), _type_row(bind, "bale stack")) if row]
    if not legacy:
        return

    agricultural = _type_row(bind, "agricultural inventory")
    bale = _type_row(bind, "bale")
    if bale is None:
        bale_id = uuid.uuid4()
        bind.execute(subject_types.insert().values(
            id=bale_id,
            canonical_name="bale",
            normalized_name="bale",
            description=(
                "A bale as the fundamental entity. Material, arrangement/grouping, bale format, wrapping and "
                "count belong in subject attributes or relationships rather than descendant type names."
            ),
            status="provisional",
            public_location_eligible=False,
            created_by="migration:0023",
            created_at=now,
            updated_at=now,
        ))
    else:
        bale_id = bale["id"]

    legacy_ids = [row["id"] for row in legacy]

    # Refuse a lossy move if an equivalent live subject already exists under bale.
    collisions = bind.execute(
        sa.select(subjects.c.canonical_key)
        .where(subjects.c.subject_type_id.in_(legacy_ids))
        .where(subjects.c.canonical_key.in_(
            sa.select(subjects.c.canonical_key).where(subjects.c.subject_type_id == bale_id)
        ))
    ).all()
    if collisions:
        keys = ", ".join(sorted({str(row[0]) for row in collisions}))
        raise RuntimeError(
            "Bale semantic-head migration found canonical-key collisions; aborting without data loss: " + keys
        )

    # Preserve subject IDs and therefore every review/assessment relationship attached to them.
    for row in legacy:
        arrangement = "stack" if row["normalized_name"] == "bale stack" else "group"
        subject_rows = bind.execute(
            sa.select(subjects.c.id, subjects.c.attributes_json, subjects.c.classification_version)
            .where(subjects.c.subject_type_id == row["id"])
        ).mappings().all()
        for subject in subject_rows:
            attrs = dict(subject["attributes_json"] or {})
            attrs.setdefault("arrangement", arrangement)
            bind.execute(
                subjects.update().where(subjects.c.id == subject["id"]).values(
                    subject_type_id=bale_id,
                    attributes_json=attrs,
                    classification_status="provisional",
                    classification_version=(subject["classification_version"] or 1) + 1,
                    classification_locked_at=None,
                )
            )

    # Carry reusable field attachments onto bale without duplicating definitions.
    for legacy_id in legacy_ids:
        field_rows = bind.execute(
            sa.select(type_fields.c.field_id).where(type_fields.c.subject_type_id == legacy_id)
        ).all()
        for (field_id,) in field_rows:
            exists = bind.execute(
                sa.select(type_fields.c.id).where(
                    type_fields.c.subject_type_id == bale_id,
                    type_fields.c.field_id == field_id,
                )
            ).first()
            if not exists:
                bind.execute(type_fields.insert().values(
                    id=uuid.uuid4(), subject_type_id=bale_id, field_id=field_id,
                    source="migration:0023", created_at=now,
                ))

    # Retire the old descriptor hierarchy but keep its rows and classification decision references as audit history.
    bind.execute(
        relationships.update()
        .where(sa.or_(
            relationships.c.source_type_id.in_(legacy_ids),
            relationships.c.target_type_id.in_(legacy_ids),
        ))
        .where(relationships.c.status == "active")
        .values(
            status="retired",
            retired_reason="Replaced by semantic-head bale classification; descriptor moved to attributes",
            retired_by="migration:0023",
            retired_at=now,
        )
    )

    if agricultural:
        existing_edge = bind.execute(
            sa.select(relationships).where(
                relationships.c.source_type_id == bale_id,
                relationships.c.relationship == "belongs_to",
                relationships.c.target_type_id == agricultural["id"],
            )
        ).mappings().first()
        if existing_edge:
            bind.execute(
                relationships.update().where(relationships.c.id == existing_edge["id"]).values(status="active")
            )
        else:
            bind.execute(relationships.insert().values(
                id=uuid.uuid4(), source_type_id=bale_id, relationship="belongs_to",
                target_type_id=agricultural["id"], source="migration:0023", status="active",
                retired_reason=None, retired_by=None, retired_at=None, created_at=now,
            ))

    # Free the original lookup names for aliases to bale while retaining the historical type IDs.
    for row in legacy:
        original = row["normalized_name"]
        legacy_name = f"legacy {original} descriptor"
        bind.execute(
            subject_types.update().where(subject_types.c.id == row["id"]).values(
                canonical_name=legacy_name,
                normalized_name=legacy_name,
                status="retired",
                updated_at=now,
            )
        )
        _ensure_alias(bind, bale_id, original, now)


def downgrade() -> None:
    # This migration intentionally preserves historical rows and may have moved live subjects.
    # Automatic downgrade would risk undoing later edits, so rollback must be handled as a forward repair.
    pass
