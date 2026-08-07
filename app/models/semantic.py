from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base

UuidType = Uuid().with_variant(PGUUID(as_uuid=True), "postgresql")


def now_utc():
    return datetime.now(timezone.utc)


def new_uuid():
    return uuid.uuid4()


class SemanticAliasProposal(Base):
    __tablename__ = "semantic_alias_proposals"
    __table_args__ = (
        UniqueConstraint(
            "concept_id",
            "alias_normalized",
            "proposer_client_id",
            name="uq_semantic_alias_vote_per_client",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    concept_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("concepts.id"), index=True)
    alias: Mapped[str] = mapped_column(String(200))
    alias_normalized: Mapped[str] = mapped_column(String(200), index=True)
    target_field_id: Mapped[uuid.UUID] = mapped_column(UuidType, ForeignKey("concept_fields.id"), index=True)
    proposer_client_id: Mapped[str] = mapped_column(String(200), index=True)
    confidence: Mapped[float | None] = mapped_column()
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
