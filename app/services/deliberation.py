from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.deliberation import Deliberation, DeliberationContribution
from app.models.v2 import V2Experience, now_utc
from app.schemas.deliberation import (
    DeliberationContributionCreate,
    DeliberationClaim,
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


def contribution_body(item: DeliberationContribution) -> dict[str, Any]:
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
        "completion_verification": item.verification_json or {},
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
        "target_model": deliberation.target_model,
        "status": deliberation.status,
        "claim": {
            "claimed_by_client": deliberation.claimed_by_client,
            "claimed_by_model": deliberation.claimed_by_model,
            "claimed_at": deliberation.claimed_at.isoformat() if deliberation.claimed_at else None,
        } if deliberation.claimed_by_client else None,
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
        body["contributions"] = [contribution_body(row) for row in rows]
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
        target_model=payload.target_model,
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


def list_open_deliberations(
    db: Session,
    *,
    owner_id: uuid.UUID,
    target_model: str | None = None,
    unclaimed_only: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    stmt = select(Deliberation).where(
        Deliberation.owner_id == owner_id,
        Deliberation.status == "open",
    )
    if target_model:
        stmt = stmt.where(Deliberation.target_model == target_model)
    if unclaimed_only:
        stmt = stmt.where(Deliberation.claimed_by_client.is_(None))
    rows = list(db.scalars(
        stmt.order_by(Deliberation.created_at, Deliberation.id).limit(limit)
    ).all())
    return [deliberation_body(db, row, include_contributions=False) for row in rows]


def claim_deliberation(
    db: Session,
    payload: DeliberationClaim,
    *,
    owner_id: uuid.UUID,
    client_id: str,
) -> Deliberation:
    deliberation = db.scalar(
        select(Deliberation).where(
            Deliberation.owner_id == owner_id,
            Deliberation.id == payload.deliberation_id,
        ).with_for_update()
    )
    if not deliberation:
        raise DeliberationError(
            "DELIBERATION_NOT_FOUND",
            "Deliberation not found for the authenticated user",
            {"deliberation_id": str(payload.deliberation_id)},
        )
    if deliberation.status != "open":
        raise DeliberationError(
            "DELIBERATION_CLOSED", "A resolved deliberation cannot be claimed",
            {"deliberation_id": str(deliberation.id)},
        )
    if deliberation.claimed_by_client and deliberation.claimed_by_client != client_id:
        raise DeliberationError(
            "DELIBERATION_ALREADY_CLAIMED",
            "This deliberation is already claimed by another authenticated client",
            {"deliberation_id": str(deliberation.id),
             "claimed_by_client": deliberation.claimed_by_client},
        )
    if not deliberation.claimed_by_client:
        deliberation.claimed_by_client = client_id
        deliberation.claimed_by_model = payload.source_model
        deliberation.claimed_at = now_utc()
        deliberation.updated_at = deliberation.claimed_at
        db.commit()
        db.refresh(deliberation)
    return deliberation


def _completion_verification(
    db: Session,
    *,
    deliberation: Deliberation,
    evidence: dict[str, Any],
    owner_id: uuid.UUID,
    idempotency_key: str | None,
) -> dict[str, Any]:
    criteria = deliberation.acceptance_criteria_json or {}
    log = evidence.get("probe_log")
    probes = log if isinstance(log, list) else []
    checks: dict[str, dict[str, Any]] = {}

    expected = criteria.get("probes_attempted")
    if isinstance(expected, int):
        checks["probes_attempted"] = {
            "passed": len(probes) == expected, "expected": expected, "observed": len(probes)
        }
    field_checks = {
        "search_query_log_required": "queries",
        "exact_subject_followup_required": "exact_name_followups",
        "fetch_all_reviews_required": "reviews_fetched",
    }
    for criterion, field in field_checks.items():
        if criteria.get(criterion) is True:
            checks[criterion] = {
                "passed": bool(probes) and all(isinstance(p, dict) and isinstance(p.get(field), list) and bool(p[field]) for p in probes),
                "checked_field": field,
            }

    if criteria.get("fetch_all_reviews_required") is True:
        raw_ids = [value for probe in probes if isinstance(probe, dict)
                   for value in (probe.get("reviews_fetched") or [])]
        parsed_ids: list[uuid.UUID] = []
        invalid_ids: list[str] = []
        for value in raw_ids:
            try:
                parsed_ids.append(uuid.UUID(str(value)))
            except (ValueError, TypeError, AttributeError):
                invalid_ids.append(str(value))
        found = set(db.scalars(select(V2Experience.id).where(
            V2Experience.owner_id == owner_id,
            V2Experience.deleted_at.is_(None),
            V2Experience.id.in_(parsed_ids),
        )).all()) if parsed_ids else set()
        missing = sorted(str(value) for value in parsed_ids if value not in found)
        checks["referenced_reviews_exist"] = {
            "passed": bool(parsed_ids) and not invalid_ids and not missing,
            "referenced_count": len(parsed_ids), "invalid_ids": invalid_ids,
            "missing_or_unowned_ids": missing,
        }

    if criteria.get("final_contribution_required") is True:
        checks["final_contribution_required"] = {"passed": True}
    if criteria.get("deterministic_idempotency_key_required") is True:
        checks["deterministic_idempotency_key_required"] = {
            "passed": bool(idempotency_key),
            "note": "Presence is verified; semantic determinism cannot be inferred from the key text.",
        }

    supported = set(field_checks) | {
        "probes_attempted", "final_contribution_required",
        "deterministic_idempotency_key_required",
    }
    unsupported = sorted(key for key, required in criteria.items()
                         if required and key not in supported)
    return {
        "machine_checks": checks,
        "all_machine_checks_passed": bool(checks) and all(item["passed"] for item in checks.values()),
        "not_machine_verifiable": unsupported,
    }


def submit_contribution(
    db: Session,
    payload: DeliberationContributionCreate,
    *,
    owner_id: uuid.UUID,
    client_id: str,
    idempotency_key: str | None = None,
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

    verification = _completion_verification(
        db, deliberation=deliberation, evidence=payload.evidence,
        owner_id=owner_id, idempotency_key=idempotency_key,
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
        verification_json=verification,
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
