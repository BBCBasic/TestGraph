"""Add governed location assertions.

Revision ID: 0016_location_assertions
Revises: 0015_deliberation_coordination
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0016_location_assertions"
down_revision = "0015_deliberation_coordination"
branch_labels = None
depends_on = None

UK_POSTCODE_RE = re.compile(
    r"\b(?:GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b", re.IGNORECASE
)


def _hash(predicate, value):
    material = {
        "predicate": predicate,
        "object_subject_id": None,
        "value": value,
        "qualifiers": {},
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _postcode(text):
    compact = re.sub(r"\s+", "", text.upper())
    return {"text": f"{compact[:-3]} {compact[-3:]}", "normalized": compact}


def upgrade() -> None:
    with op.batch_alter_table("subject_types") as batch:
        batch.add_column(sa.Column(
            "public_location_eligible", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ))

    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE subject_types SET public_location_eligible = "
        "CASE WHEN normalized_name = 'person' THEN false ELSE true END"
    ))
    place_id = bind.execute(sa.text(
        "SELECT id FROM subject_types WHERE normalized_name = 'place'"
    )).scalar()
    if place_id is None:
        place_id = uuid.uuid4()
        bind.execute(sa.text(
            "INSERT INTO subject_types "
            "(id, canonical_name, normalized_name, description, status, created_by, "
            "created_at, updated_at, public_location_eligible) "
            "VALUES (:id, 'place', 'place', :description, 'provisional', 'migration:v1', "
            ":created_at, :updated_at, true)"
        ), {
            "id": place_id,
            "description": "Stable geographic identity used by evidence-backed location assertions.",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })

    op.create_table(
        "location_assertions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("predicate", sa.String(length=40), nullable=False),
        sa.Column("object_subject_id", sa.Uuid(), nullable=True),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("qualifiers_json", sa.JSON(), nullable=False),
        sa.Column("claim_hash", sa.String(length=64), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("asserted_by_client", sa.String(length=200), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conflict_state", sa.String(length=20), nullable=False),
        sa.Column("resolution_json", sa.JSON(), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "predicate IN ('located_in','contained_in','published_address','postcode','position')",
            name="ck_location_assertion_predicate",
        ),
        sa.CheckConstraint(
            "(predicate IN ('located_in','contained_in') AND object_subject_id IS NOT NULL) "
            "OR (predicate IN ('published_address','postcode','position') AND object_subject_id IS NULL)",
            name="ck_location_assertion_shape",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["v2_subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["object_subject_id"], ["v2_subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "owner_id", "subject_id", "predicate", "object_subject_id",
        "claim_hash", "conflict_state", "asserted_by_client",
    ):
        op.create_index(
            f"ix_location_assertions_{column}", "location_assertions", [column]
        )

    assertions = sa.table(
        "location_assertions",
        sa.column("id", sa.Uuid()),
        sa.column("owner_id", sa.Uuid()),
        sa.column("subject_id", sa.Uuid()),
        sa.column("predicate", sa.String()),
        sa.column("object_subject_id", sa.Uuid()),
        sa.column("value_json", sa.JSON()),
        sa.column("qualifiers_json", sa.JSON()),
        sa.column("claim_hash", sa.String()),
        sa.column("source_json", sa.JSON()),
        sa.column("asserted_by_client", sa.String()),
        sa.column("observed_at", sa.DateTime(timezone=True)),
        sa.column("valid_from", sa.DateTime(timezone=True)),
        sa.column("valid_to", sa.DateTime(timezone=True)),
        sa.column("conflict_state", sa.String()),
        sa.column("resolution_json", sa.JSON()),
        sa.column("visibility", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    subjects = sa.table(
        "v2_subjects",
        sa.column("id", sa.Uuid()),
        sa.column("owner_id", sa.Uuid()),
        sa.column("attributes_json", sa.JSON()),
        sa.column("provenance_json", sa.JSON()),
    )
    now = datetime.now(timezone.utc)
    for row in bind.execute(sa.select(
        subjects.c.id, subjects.c.owner_id,
        subjects.c.attributes_json, subjects.c.provenance_json,
    )).mappings():
        attributes = row["attributes_json"] or {}
        source = {
            "reference": "legacy_subject_attributes",
            "kind": "migration_backfill",
            "legacy_provenance": row["provenance_json"] or {},
        }
        values = []
        address = attributes.get("address")
        if isinstance(address, str) and address.strip():
            address_value = {"text": " ".join(address.split())}
            values.append(("published_address", address_value))
            match = UK_POSTCODE_RE.search(address)
            if match:
                values.append(("postcode", _postcode(match.group(0))))
        postcode = attributes.get("postcode")
        if isinstance(postcode, str) and postcode.strip():
            postcode_value = _postcode(postcode)
            if ("postcode", postcode_value) not in values:
                values.append(("postcode", postcode_value))
        latitude = attributes.get("latitude")
        longitude = attributes.get("longitude")
        try:
            latitude = float(latitude)
            longitude = float(longitude)
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                values.append(("position", {
                    "latitude": latitude, "longitude": longitude, "datum": "WGS84"
                }))
        except (TypeError, ValueError):
            pass
        for predicate, value in values:
            bind.execute(assertions.insert().values(
                id=uuid.uuid4(),
                owner_id=row["owner_id"],
                subject_id=row["id"],
                predicate=predicate,
                object_subject_id=None,
                value_json=value,
                qualifiers_json={},
                claim_hash=_hash(predicate, value),
                source_json=source,
                asserted_by_client="migration:v1",
                observed_at=now,
                valid_from=None,
                valid_to=None,
                conflict_state="uncontested",
                resolution_json={},
                visibility="private" if row["owner_id"] else "unlisted",
                created_at=now,
                updated_at=now,
            ))


def downgrade() -> None:
    for column in reversed((
        "owner_id", "subject_id", "predicate", "object_subject_id",
        "claim_hash", "conflict_state", "asserted_by_client",
    )):
        op.drop_index(
            f"ix_location_assertions_{column}", table_name="location_assertions"
        )
    op.drop_table("location_assertions")
    with op.batch_alter_table("subject_types") as batch:
        batch.drop_column("public_location_eligible")
