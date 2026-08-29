from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.v2 import JsonType, UuidType, new_uuid, now_utc


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    workflow_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, ForeignKey("users.id"), index=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("v2_subjects.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    current_step: Mapped[str] = mapped_column(String(80), nullable=False)
    required_actor: Mapped[str | None] = mapped_column(String(80))
    context_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    step: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_client: Mapped[str | None] = mapped_column(String(200))
    actor_model: Mapped[str | None] = mapped_column(String(160))
    details_json: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class McpInteraction(Base):
    __tablename__ = "mcp_interactions"

    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_uuid)
    request_id: Mapped[str | None] = mapped_column(String(200), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, ForeignKey("users.id"), index=True)
    client_id: Mapped[str | None] = mapped_column(String(200), index=True)
    source_model: Mapped[str | None] = mapped_column(String(160), index=True)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("workflow_runs.id", ondelete="SET NULL"), index=True
    )
    workflow_step: Mapped[str | None] = mapped_column(String(80))
    arguments_summary: Mapped[dict] = mapped_column(JsonType, default=dict)
    result_summary: Mapped[dict] = mapped_column(JsonType, default=dict)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    server_version: Mapped[str | None] = mapped_column(String(80))
    build_sha: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
