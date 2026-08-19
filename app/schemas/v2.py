from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class SubjectTypeEnsure(StrictModel):
    term: str = Field(min_length=1, max_length=160)
    description: str | None = None
    create_if_missing: bool = True


class SubjectEnsure(StrictModel):
    subject_type: str
    name: str = Field(min_length=1, max_length=240)
    canonical_key: str = Field(min_length=1, max_length=300)
    identifiers: dict[str, Any] = {}
    attributes: dict[str, Any] = {}
    provenance: dict[str, Any] = {}


class ContextSubjectEnsure(SubjectEnsure):
    ref: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


class ContextRelationshipEnsure(StrictModel):
    source_ref: str = Field(min_length=1, max_length=80)
    relationship: str = Field(min_length=1, max_length=60)
    target_ref: str = Field(min_length=1, max_length=80)
    provenance: dict[str, Any] = {}


class SubjectContextEnsure(StrictModel):
    subjects: list[ContextSubjectEnsure] = []
    relationships: list[ContextRelationshipEnsure] = []


class SubjectEnrichmentCheck(StrictModel):
    status: Literal["completed", "unavailable", "not_applicable", "ambiguous"]
    sources: list[str] = []
    applied_fields: dict[str, list[str]] = {}
    unapplied_sources: dict[str, str] = {}
    attempts: list[str] = []
    reason: str | None = Field(default=None, min_length=1)
    candidate_identities: list[str] = []


class CollectionAssessment(StrictModel):
    status: Literal["member", "independent", "unavailable", "ambiguous"]
    collection_name: str | None = Field(default=None, min_length=1, max_length=240)
    collection_type: str | None = Field(default=None, min_length=1, max_length=160)
    directory_url: str | None = Field(default=None, min_length=1)
    discovered_count: int | None = Field(default=None, ge=2)
    submitted_member_refs: list[str] = []
    evidence_sources: list[str] = []
    attempts: list[str] = []
    reason: str | None = Field(default=None, min_length=1)
    candidate_collections: list[str] = []
    checked_at: datetime | None = None


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
    subject_enrichment_check: SubjectEnrichmentCheck | None = None
    collection_assessment: CollectionAssessment | None = None
    source_client: str = "ai-client"


class AssessmentCreate(StrictModel):
    experience_id: UUID
    assessment_type: str
    evidence: dict[str, Any] = {}
    analysis: dict[str, Any] = {}
    conclusion: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_model: str | None = None
    provenance: dict[str, Any] = {}


class FieldEnsure(StrictModel):
    canonical_name: str = Field(min_length=1, max_length=160)
    json_schema: dict[str, Any]
    description: str | None = None
    aliases: list[str] = []
    subject_types: list[str] = []


class RelationshipEnsure(StrictModel):
    source_type: str
    relationship: str = "belongs_to"
    target_type: str


class SubjectRead(StrictModel):
    id: UUID
    subject_type_id: UUID
    name: str
    canonical_key: str
    identifiers_json: dict[str, Any]
    attributes_json: dict[str, Any]
    provenance_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ExperienceRead(StrictModel):
    id: UUID
    owner_id: UUID
    subject_id: UUID
    source_id: UUID | None
    record_type: str
    experienced_at: datetime | None
    headline: str
    summary: str
    raw_text: str
    structured_data: dict[str, Any]
    submitted_data: dict[str, Any]
    normalization_log: list[Any]
    visibility: str
    publication_status: str
    provenance: dict[str, Any]
    created_by_client: str
    created_at: datetime
    updated_at: datetime
