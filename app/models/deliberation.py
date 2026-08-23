from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.v2 import JsonType, UuidType, new_uuid, now_utc


class Deliberation(Base):
    __tablename__ = "deliberations"
    __table_args__ = (
        UniqueConstraint("owner_id", "canonical_key", name="uq_deliberations_owner_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id"), nullable=False, index=True
    )
    canonical_key: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    constraints_json: Mapped[list] = mapped_column(JsonType, default=list)
    acceptance_criteria_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    target_model: Mapped[str | None] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    resolution_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_by_client: Mapped[str] = mapped_column(String(200), nullable=False)
    claimed_by_client: Mapped[str | None] = mapped_column(String(200), index=True)
    claimed_by_model: Mapped[str | None] = mapped_column(String(160))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_client: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeliberationContribution(Base):
    __tablename__ = "deliberation_contributions"

    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    deliberation_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("deliberations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id"), index=True
    )
    contribution_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float)
    unresolved_points_json: Mapped[list] = mapped_column(JsonType, default=list)
    responds_to_json: Mapped[list] = mapped_column(JsonType, default=list)
    source_model: Mapped[str | None] = mapped_column(String(160))
    verification_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_by_client: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
