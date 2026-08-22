from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictDeliberationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliberationCreate(StrictDeliberationModel):
    canonical_key: str = Field(
        min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$"
    )
    title: str = Field(min_length=1, max_length=240)
    question: str = Field(min_length=1)
    context: dict[str, Any] = {}
    constraints: list[str] = []
    acceptance_criteria: dict[str, Any] = {}
    target_model: str | None = Field(default=None, max_length=160)


class DeliberationClaim(StrictDeliberationModel):
    deliberation_id: UUID
    source_model: str | None = Field(default=None, max_length=160)


class DeliberationContributionCreate(StrictDeliberationModel):
    deliberation_id: UUID
    contribution_type: Literal[
        "proposal", "critique", "counterproposal", "reconciliation", "vote"
    ]
    content: str = Field(min_length=1)
    evidence: dict[str, Any] = {}
    confidence: float | None = Field(default=None, ge=0, le=1)
    unresolved_points: list[str] = []
    responds_to_contribution_ids: list[UUID] = []
    source_model: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_vote(self):
        if self.contribution_type != "vote":
            return self
        vote = str(self.evidence.get("vote") or "").strip().casefold()
        reason = str(self.evidence.get("reason") or "").strip()
        if vote not in {"approve", "reject", "abstain"}:
            raise ValueError("vote contributions require evidence.vote=approve|reject|abstain")
        if not reason:
            raise ValueError("vote contributions require a non-empty evidence.reason")
        return self


class DeliberationResolutionCreate(StrictDeliberationModel):
    deliberation_id: UUID
    resolution: str = Field(min_length=1)
    rationale: str | None = None
    accepted_contribution_ids: list[UUID] = []
    unresolved_points: list[str] = []
    user_approved: bool
