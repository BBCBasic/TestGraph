from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class FieldProposal(StrictModel):
    submitted_name: str = Field(min_length=1, max_length=200)
    canonical_name: str = Field(min_length=1, max_length=200)
    json_schema: dict[str, Any]
    description: str | None = None
    aliases: list[str] = []


class ConceptEnsure(StrictModel):
    path: str = Field(min_length=1, max_length=300)
    description: str | None = None
    definition: dict[str, Any] = {}
    created_by: str = "ai-client"


class NormaliseRequest(StrictModel):
    concept_path: str
    data: dict[str, Any]
    source: str = "ai-client"


class SubjectEnsure(StrictModel):
    concept_path: str
    name: str = Field(min_length=1, max_length=240)
    canonical_key: str = Field(min_length=1, max_length=300)
    identifiers: dict[str, Any] = {}
    attributes: dict[str, Any] = {}
    create_concept_if_missing: bool = True


class SourceCreate(StrictModel):
    source_type: str
    provider: str
    external_id: str | None = None
    url: str | None = None
    author: str | None = None
    license: str | None = None
    raw_data: dict[str, Any] = {}
    source_metadata: dict[str, Any] = {}


class ExperienceCreate(StrictModel):
    owner_id: UUID
    subject_id: UUID
    headline: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    structured_data: dict[str, Any] = {}
    experienced_at: datetime | None = None
    visibility: Literal["private", "unlisted", "public", "aggregate_only"] = "private"
    user_approved: bool = False
    source: SourceCreate | None = None
    source_client: str = "ai-client"


class AssessmentCreate(StrictModel):
    subject_id: UUID
    user_id: UUID | None = None
    assessment_type: str
    evidence: dict[str, Any] = {}
    analysis: dict[str, Any] = {}
    conclusion: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_model: str | None = None
    provenance: dict[str, Any] = {}


class ConceptRead(StrictModel):
    id: UUID
    path: str
    name: str
    parent_id: UUID | None
    description: str | None
    version: int
    status: str
    definition_json: dict[str, Any]
    created_by: str
    created_at: datetime
    updated_at: datetime


class SubjectRead(StrictModel):
    id: UUID
    concept_id: UUID
    name: str
    canonical_key: str
    identifiers_json: dict[str, Any]
    attributes_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ExperienceRead(StrictModel):
    id: UUID
    owner_id: UUID
    subject_id: UUID
    source_id: UUID | None
    experienced_at: datetime | None
    headline: str
    summary: str
    raw_text: str
    structured_data: dict[str, Any]
    submitted_data: dict[str, Any]
    normalization_log: list[Any]
    visibility: str
    publication_status: str
    version: int
    provenance: dict[str, Any]
    created_by_client: str
    created_at: datetime
    updated_at: datetime
