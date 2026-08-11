from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
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


class SubjectType(Base):
    __tablename__ = "subject_types"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    canonical_name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="provisional", index=True)
    created_by: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class SubjectTypeAlias(Base):
    __tablename__ = "subject_type_aliases"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    subject_type_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("subject_types.id"), index=True)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class TypeRelationship(Base):
    __tablename__ = "subject_type_relationships"
    __table_args__ = (UniqueConstraint("source_type_id", "relationship", "target_type_id", name="uq_type_relationship"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    source_type_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("subject_types.id"), index=True)
    relationship: Mapped[str] = mapped_column(String(60), default="belongs_to", index=True)
    target_type_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("subject_types.id"), index=True)
    source: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class FieldDefinition(Base):
    __tablename__ = "field_definitions"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    canonical_name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    json_schema: Mapped[dict] = mapped_column(JsonType, default=dict)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_by: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class FieldAlias(Base):
    __tablename__ = "field_aliases"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    field_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("field_definitions.id"), index=True)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SubjectTypeField(Base):
    __tablename__ = "subject_type_fields"
    __table_args__ = (UniqueConstraint("subject_type_id", "field_id", name="uq_subject_type_field"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    subject_type_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("subject_types.id"), index=True)
    field_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("field_definitions.id"), index=True)
    source: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class V2Subject(Base):
    __tablename__ = "v2_subjects"
    __table_args__ = (UniqueConstraint("subject_type_id", "canonical_key", name="uq_v2_subject_type_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    subject_type_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("subject_types.id"), index=True)
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
    record_type: Mapped[str] = mapped_column(String(30), default="review", index=True)
    experienced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    headline: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)
    structured_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    submitted_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    normalization_log: Mapped[list] = mapped_column(JsonType, default=list)
    visibility: Mapped[str] = mapped_column(String(20), default="private", index=True)
    publication_status: Mapped[str] = mapped_column(String(20), default="published", index=True)
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_by_client: Mapped[str] = mapped_column(String(200), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    subject_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("v2_subjects.id"), index=True)
    experience_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("v2_experiences.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, ForeignKey("users.id"), index=True)
    assessment_type: Mapped[str] = mapped_column(String(80), index=True)
    evidence_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    analysis_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    conclusion: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column()
    source_model: Mapped[str | None] = mapped_column(String(120))
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_by_client: Mapped[str] = mapped_column(String(200), default="unknown", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
