from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

class Observation(StrictModel):
    category: str
    statement: str
    confidence: float = Field(ge=0, le=1)

class SubjectiveImpression(StrictModel):
    category: str
    statement: str
    sentiment: float = Field(ge=-1, le=1)
    importance_to_reviewer: float = Field(ge=0, le=1)
    confidence: float = Field(default=0.8, ge=0, le=1)

class CommonExperienceData(StrictModel):
    observations: list[Observation] = []
    subjective_impressions: list[SubjectiveImpression] = []
    strengths: list[str] = []
    weaknesses: list[str] = []
    would_repeat: bool | None = None
    special_journey_worthy: bool | None = None
    confidence: dict[str, float] = {}

class Provenance(StrictModel):
    source_method: str
    source_client: str | None = None
    source_url: str | None = None
    source_record_id: str | None = None
    source_created_at: datetime | None = None
    license: str | None = None
    attribution: str | None = None
    source_metadata: dict[str, Any] = {}
    raw_conversation_stored: bool = False
    raw_conversation_published: bool = False
    inferred_fields: list[str] = []
    notes: str | None = None

class Consent(StrictModel):
    user_approved: bool = False
    authorization_basis: Literal["user_approval", "licensed_source"] | None = None
    license_reference: str | None = None
    approved_at: datetime | None = None
    approved_version: int | None = None

class UserCreate(StrictModel):
    display_name: str
    bio: str | None = None
    profile_data: dict[str, Any] = {}

class UserRead(UserCreate):
    id: UUID
    created_at: datetime

class SubjectCreate(StrictModel):
    subject_type: str
    name: str
    canonical_key: str
    canonical_identifiers: dict[str, Any] = {}
    metadata_json: dict[str, Any] = {}

class SubjectRead(SubjectCreate):
    id: UUID
    created_at: datetime

class ExperienceCreate(StrictModel):
    owner_id: UUID
    subject_id: UUID
    subject_type: str
    schema_version: str
    visibility: Literal["private", "unlisted", "public", "aggregate_only"] = "private"
    headline: str
    summary: str
    common_data: CommonExperienceData
    domain_data: dict[str, Any]
    provenance: Provenance
    consent: Consent = Consent()

class ExperienceRead(ExperienceCreate):
    id: UUID
    publication_status: str
    version: int
    created_by_client: str
    auth_subject: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    deleted_at: datetime | None

class PublishRequest(StrictModel):
    user_approved: bool
    approved_version: int

class PairwiseAlignmentUpsert(StrictModel):
    source_user_id: UUID
    target_user_id: UUID
    dimensions: dict[str, float]

class PersonalisedDimension(StrictModel):
    dimension: str
    reviewer_sentiment: float
    reader_importance: float
    pairwise_alignment: float
    relevance: float
    explanation: str

class PersonalisedReview(StrictModel):
    experience_id: UUID
    reader_id: UUID
    overall_relevance: float
    reader_specific_conclusion: str
    dimensions: list[PersonalisedDimension]
