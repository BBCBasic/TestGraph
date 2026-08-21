from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.deliberation import Deliberation, DeliberationContribution
from app.models.v2 import now_utc
from app.schemas.deliberation import (
    DeliberationContributionCreate,
    DeliberationCreate,
    DeliberationResolutionCreate,
)


class DeliberationError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _owned_deliberation(
    db: Session,
    owner_id: uuid.UUID,
    *,
    deliberation_id: uuid.UUID | None = None,
    canonical_key: str | None = None,
) -> Deliberation:
    if deliberation_id is None and not canonical_key:
        raise DeliberationError(
            "DELIBERATION_IDENTIFIER_REQUIRED",
            "Provide deliberation_id or canonical_key",
        )
    stmt = select(Deliberation).where(Deliberation.owner_id == owner_id)
    if deliberation_id is not None:
        stmt = stmt.where(Deliberation.id == deliberation_id)
    else:
        stmt = stmt.where(Deliberation.canonical_key == canonical_key)
    deliberation = db.scalar(stmt)
    if not deliberation:
        raise DeliberationError(
            "DELIBERATION_NOT_FOUND",
            "Deliberation not found for the authenticated user",
            {"deliberation_id": str(deliberation_id) if deliberation_id else None,
             "canonical_key": canonical_key},
        )
    return deliberation


def _contribution_body(item: DeliberationContribution) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "contribution_type": item.contribution_type,
        "content": item.content,
        "evidence": item.evidence_json or {},
        "confidence": item.confidence,
        "unresolved_points": item.unresolved_points_json or [],
        "responds_to_contribution_ids": item.responds_to_json or [],
        "source_model": item.source_model,
        "source_client": item.created_by_client,
        "provenance": item.provenance or {},
        "created_at": item.created_at.isoformat(),
    }


def deliberation_body(
    db: Session, deliberation: Deliberation, *, include_contributions: bool = True
) -> dict[str, Any]:
    body = {
        "id": str(deliberation.id),
        "canonical_key": deliberation.canonical_key,
        "title": deliberation.title,
        "question": deliberation.question,
        "context": deliberation.context_json or {},
        "constraints": deliberation.constraints_json or [],
        "acceptance_criteria": deliberation.acceptance_criteria_json or {},
        "status": deliberation.status,
        "resolution": deliberation.resolution_json or None,
        "created_by_client": deliberation.created_by_client,
        "resolved_by_client": deliberation.resolved_by_client,
        "created_at": deliberation.created_at.isoformat(),
        "updated_at": deliberation.updated_at.isoformat(),
        "resolved_at": (
            deliberation.resolved_at.isoformat() if deliberation.resolved_at else None
        ),
    }
    if include_contributions:
        rows = list(db.scalars(
            select(DeliberationContribution)
            .where(DeliberationContribution.deliberation_id == deliberation.id)
            .order_by(DeliberationContribution.created_at, DeliberationContribution.id)
        ).all())
        body["contributions"] = [_contribution_body(row) for row in rows]
    return body


def create_deliberation(
    db: Session,
    payload: DeliberationCreate,
    *,
    owner_id: uuid.UUID,
    client_id: str,
) -> Deliberation:
    existing = db.scalar(select(Deliberation).where(
        Deliberation.owner_id == owner_id,
        Deliberation.canonical_key == payload.canonical_key,
    ))
    if existing:
        raise DeliberationError(
            "DELIBERATION_KEY_CONFLICT",
            "A deliberation with this canonical_key already exists",
            {"canonical_key": payload.canonical_key, "existing_id": str(existing.id),
             "action": "Use get_deliberation with this canonical_key or choose a new key."},
        )
    item = Deliberation(
        owner_id=owner_id,
        canonical_key=payload.canonical_key,
        title=payload.title,
        question=payload.question,
        context_json=payload.context,
        constraints_json=payload.constraints,
        acceptance_criteria_json=payload.acceptance_criteria,
        status="open",
        resolution_json={},
        created_by_client=client_id,
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DeliberationError(
            "DELIBERATION_KEY_CONFLICT",
            "A deliberation with this canonical_key already exists",
            {"canonical_key": payload.canonical_key,
             "action": "Use get_deliberation with this canonical_key or choose a new key."},
        ) from exc
    db.refresh(item)
    return item


def get_deliberation(
    db: Session,
    *,
    owner_id: uuid.UUID,
    deliberation_id: uuid.UUID | None = None,
    canonical_key: str | None = None,
) -> dict[str, Any]:
    item = _owned_deliberation(
        db, owner_id, deliberation_id=deliberation_id, canonical_key=canonical_key
    )
    return deliberation_body(db, item)


def submit_contribution(
    db: Session,
    payload: DeliberationContributionCreate,
    *,
    owner_id: uuid.UUID,
    client_id: str,
) -> DeliberationContribution:
    deliberation = _owned_deliberation(
        db, owner_id, deliberation_id=payload.deliberation_id
    )
    if deliberation.status != "open":
        raise DeliberationError(
            "DELIBERATION_CLOSED",
            "Contributions cannot be added after resolution",
            {"deliberation_id": str(deliberation.id), "status": deliberation.status},
        )

    response_ids = list(dict.fromkeys(payload.responds_to_contribution_ids))
    if response_ids:
        found = set(db.scalars(select(DeliberationContribution.id).where(
            DeliberationContribution.deliberation_id == deliberation.id,
            DeliberationContribution.id.in_(response_ids),
        )).all())
        missing = [str(item_id) for item_id in response_ids if item_id not in found]
        if missing:
            raise DeliberationError(
                "INVALID_CONTRIBUTION_REFERENCE",
                "Every response target must belong to this deliberation",
                {"missing_or_foreign_contribution_ids": missing},
            )

    item = DeliberationContribution(
        deliberation_id=deliberation.id,
        user_id=owner_id,
        contribution_type=payload.contribution_type,
        content=payload.content,
        evidence_json=payload.evidence,
        confidence=payload.confidence,
        unresolved_points_json=payload.unresolved_points,
        responds_to_json=[str(item_id) for item_id in response_ids],
        source_model=payload.source_model,
        provenance={
            "kind": "ai_deliberation_contribution",
            "source_client": client_id,
            "source_model": payload.source_model,
        },
        created_by_client=client_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def record_resolution(
    db: Session,
    payload: DeliberationResolutionCreate,
    *,
    owner_id: uuid.UUID,
    client_id: str,
) -> Deliberation:
    if payload.user_approved is not True:
        raise DeliberationError(
            "USER_APPROVAL_REQUIRED",
            "Explicit user approval is required to resolve a deliberation",
            {"user_approved": False},
        )
    deliberation = _owned_deliberation(
        db, owner_id, deliberation_id=payload.deliberation_id
    )
    if deliberation.status != "open":
        raise DeliberationError(
            "DELIBERATION_ALREADY_RESOLVED",
            "This deliberation already has a user-approved resolution",
            {"deliberation_id": str(deliberation.id),
             "resolution": deliberation.resolution_json or None},
        )

    accepted_ids = list(dict.fromkeys(payload.accepted_contribution_ids))
    if accepted_ids:
        found = set(db.scalars(select(DeliberationContribution.id).where(
            DeliberationContribution.deliberation_id == deliberation.id,
            DeliberationContribution.id.in_(accepted_ids),
        )).all())
        missing = [str(item_id) for item_id in accepted_ids if item_id not in found]
        if missing:
            raise DeliberationError(
                "INVALID_RESOLUTION_REFERENCE",
                "Every accepted contribution must belong to this deliberation",
                {"missing_or_foreign_contribution_ids": missing},
            )

    resolved_at = now_utc()
    deliberation.status = "resolved"
    deliberation.resolution_json = {
        "resolution": payload.resolution,
        "rationale": payload.rationale,
        "accepted_contribution_ids": [str(item_id) for item_id in accepted_ids],
        "unresolved_points": payload.unresolved_points,
        "user_approved": True,
        "resolved_at": resolved_at.isoformat(),
    }
    deliberation.resolved_by_client = client_id
    deliberation.resolved_at = resolved_at
    deliberation.updated_at = resolved_at
    db.commit()
    db.refresh(deliberation)
    return deliberation
