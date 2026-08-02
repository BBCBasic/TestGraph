from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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

class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text)
    profile_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("subject_type", "canonical_key", name="uq_subject_type_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    subject_type: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(240), index=True)
    canonical_key: Mapped[str] = mapped_column(String(300), index=True)
    canonical_identifiers: Mapped[dict] = mapped_column(JsonType, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class SchemaDefinition(Base):
    __tablename__ = "schema_definitions"
    __table_args__ = (UniqueConstraint("subject_type", "version", name="uq_schema_subject_version"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    subject_type: Mapped[str] = mapped_column(String(50), index=True)
    version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="stable")
    json_schema: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Experience(Base):
    __tablename__ = "experiences"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("users.id"), index=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("subjects.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(50), index=True)
    schema_version: Mapped[str] = mapped_column(String(20))
    publication_status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    version: Mapped[int] = mapped_column(Integer, default=1)
    headline: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text)
    common_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    domain_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    consent: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_by_client: Mapped[str] = mapped_column(String(120), default="unknown")
    auth_subject: Mapped[str] = mapped_column(String(200), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(String(200))

class ProfileSignal(Base):
    __tablename__ = "profile_signals"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("users.id"), index=True)
    dimension: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[float] = mapped_column()
    confidence: Mapped[float] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="active")
    supporting_evidence_ids: Mapped[list] = mapped_column(JsonType, default=list)
    supersedes_signal_id: Mapped[uuid.UUID | None] = mapped_column(UuidType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class PairwiseAlignment(Base):
    __tablename__ = "pairwise_alignments"
    __table_args__ = (UniqueConstraint("source_user_id", "target_user_id", name="uq_alignment_pair"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    source_user_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("users.id"), index=True)
    target_user_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("users.id"), index=True)
    dimensions: Mapped[dict] = mapped_column(JsonType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("client_id", "key", name="uq_idempotency_client_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(String(120), index=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    actor_id: Mapped[str] = mapped_column(String(200))
    client_id: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(100), index=True)
    object_type: Mapped[str] = mapped_column(String(50))
    object_id: Mapped[str] = mapped_column(String(200), index=True)
    request_id: Mapped[str] = mapped_column(String(100), index=True)
    details: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
