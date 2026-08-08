from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid

from app.db.base import Base

JsonType = JSON().with_variant(JSONB, "postgresql")
UuidType = Uuid().with_variant(PGUUID(as_uuid=True), "postgresql")


def now_utc():
    return datetime.now(timezone.utc)


def new_uuid():
    return uuid.uuid4()


class Concept(Base):
    __tablename__ = "concepts"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    path: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, ForeignKey("concepts.id"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    definition_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_by: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ConceptField(Base):
    __tablename__ = "concept_fields"
    __table_args__ = (UniqueConstraint("concept_id", "canonical_name", name="uq_concept_field_name"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    concept_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("concepts.id"), index=True)
    canonical_name: Mapped[str] = mapped_column(String(200), index=True)
    data_type: Mapped[str] = mapped_column(String(30), default="any")
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(50))
    allowed_values: Mapped[list] = mapped_column(JsonType, default=list)
    metadata_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    introduced_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_by: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ConceptFieldProposal(Base):
    __tablename__ = "concept_field_proposals"
    __table_args__ = (
        UniqueConstraint(
            "concept_id", "canonical_name_normalized",
            name="uq_concept_field_proposal_name",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    concept_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("concepts.id"), index=True)
    submitted_name: Mapped[str] = mapped_column(String(200))
    canonical_name: Mapped[str] = mapped_column(String(200))
    canonical_name_normalized: Mapped[str] = mapped_column(String(200), index=True)
    json_schema: Mapped[dict] = mapped_column(JsonType)
    description: Mapped[str | None] = mapped_column(Text)
    aliases_json: Mapped[list] = mapped_column(JsonType, default=list)
    proposer_client_id: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decision_by: Mapped[str | None] = mapped_column(String(120))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class FieldAlias(Base):
    __tablename__ = "field_aliases"
    __table_args__ = (UniqueConstraint("concept_id", "alias_normalized", name="uq_concept_alias"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    concept_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("concepts.id"), index=True)
    field_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("concept_fields.id"), index=True)
    alias: Mapped[str] = mapped_column(String(200))
    alias_normalized: Mapped[str] = mapped_column(String(200), index=True)
    confidence: Mapped[float] = mapped_column(default=1.0)
    source: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class V2Subject(Base):
    __tablename__ = "v2_subjects"
    __table_args__ = (UniqueConstraint("concept_id", "canonical_key", name="uq_v2_subject_concept_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    concept_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("concepts.id"), index=True)
    name: Mapped[str] = mapped_column(String(240), index=True)
    canonical_key: Mapped[str] = mapped_column(String(300), index=True)
    identifiers_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    attributes_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_source_provider_external"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    provider: Mapped[str] = mapped_column(String(120), index=True)
    external_id: Mapped[str | None] = mapped_column(String(300), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(240))
    license: Mapped[str | None] = mapped_column(String(120))
    raw_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    source_metadata: Mapped[dict] = mapped_column(JsonType, default=dict)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class V2Experience(Base):
    __tablename__ = "v2_experiences"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("users.id"), index=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("v2_subjects.id"), index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, ForeignKey("sources.id"), index=True)
    experienced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    headline: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    structured_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    submitted_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    normalization_log: Mapped[list] = mapped_column(JsonType, default=list)
    visibility: Mapped[str] = mapped_column(String(20), default="private", index=True)
    publication_status: Mapped[str] = mapped_column(String(20), default="published", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_by_client: Mapped[str] = mapped_column(String(120), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    subject_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("v2_subjects.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, ForeignKey("users.id"), index=True)
    assessment_type: Mapped[str] = mapped_column(String(80), index=True)
    evidence_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    analysis_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    conclusion: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column()
    source_model: Mapped[str | None] = mapped_column(String(120))
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
