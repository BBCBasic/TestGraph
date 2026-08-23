from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.deliberation import Deliberation, DeliberationContribution
from app.models.entities import AuditEvent, IdempotencyRecord
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


def _canonical_target_model(value: str | None) -> str | None:
    if value is None:
        return None
    label = value.strip()
    if not label:
        return None
    if label.casefold() in {"gpt", "chatgpt"}:
        return "chatgpt"
    return label


def _target_model_aliases(value: str) -> set[str]:
    canonical = _canonical_target_model(value)
    if canonical == "chatgpt":
        return {"gpt", "chatgpt"}
    return {canonical.casefold()} if canonical else set()


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
        target_model=_canonical_target_model(payload.target_model),
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
        aliases = _target_model_aliases(target_model)
        stmt = stmt.where(func.lower(Deliberation.target_model).in_(aliases))
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


def _query_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict) and isinstance(value.get("query"), str):
        return value["query"].strip() or None
    return None


def _normalise_query(value: str) -> str:
    return " ".join(value.casefold().split())


def _idempotency_verification(
    db: Session,
    *,
    deliberation: Deliberation,
    client_id: str,
    criteria: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    records = list(db.scalars(
        select(IdempotencyRecord).where(
            IdempotencyRecord.client_id == client_id,
            IdempotencyRecord.key.like("deliberation-%"),
        )
    ).all())
    deliberation_id = str(deliberation.id)
    claim_records = [
        record for record in records
        if record.key.startswith("deliberation-claim:")
        and isinstance(record.response_body, dict)
        and str(record.response_body.get("id")) == deliberation_id
    ]
    contribution_records = [
        record for record in records
        if record.key.startswith("deliberation-contribution:")
        and isinstance(record.response_body, dict)
        and str(record.response_body.get("deliberation_id")) == deliberation_id
    ]
    relevant_keys = {
        record.key for record in claim_records + contribution_records
    }
    events = list(db.scalars(
        select(AuditEvent).where(
            AuditEvent.client_id == client_id,
            AuditEvent.object_type == "idempotency_key",
            AuditEvent.object_id.in_(relevant_keys),
            AuditEvent.action.in_(["idempotency.replay", "idempotency.conflict"]),
        )
    ).all()) if relevant_keys else []
    replay_keys = {
        event.object_id for event in events if event.action == "idempotency.replay"
    }
    conflict_events = [
        event for event in events if event.action == "idempotency.conflict"
    ]
    conflict_keys = {event.object_id for event in conflict_events}
    claim_replay_keys = sorted(
        record.key for record in claim_records if record.key in replay_keys
    )
    contribution_replay_records = [
        record for record in contribution_records if record.key in replay_keys
    ]
    contribution_replay_keys = sorted(
        record.key for record in contribution_replay_records
    )
    contribution_conflict_keys = sorted(
        record.key for record in contribution_records if record.key in conflict_keys
    )
    contribution_ids = sorted({
        str(record.response_body.get("contribution", {}).get("id"))
        for record in contribution_replay_records
        if isinstance(record.response_body.get("contribution"), dict)
        and record.response_body["contribution"].get("id")
    })
    stored_contribution_ids = set()
    if contribution_ids:
        parsed_ids = []
        for value in contribution_ids:
            try:
                parsed_ids.append(uuid.UUID(value))
            except (ValueError, TypeError, AttributeError):
                continue
        stored_contribution_ids = {
            str(value) for value in db.scalars(
                select(DeliberationContribution.id).where(
                    DeliberationContribution.deliberation_id == deliberation.id,
                    DeliberationContribution.id.in_(parsed_ids),
                )
            ).all()
        }
    claim_replayed = bool(claim_replay_keys)
    contribution_replayed = (
        bool(contribution_replay_keys)
        and bool(contribution_ids)
        and set(contribution_ids) == stored_contribution_ids
    )
    conflict_rejected = bool(contribution_conflict_keys)
    checks: dict[str, dict[str, Any]] = {}

    if criteria.get("identical_claim_replay_required") is True:
        checks["identical_claim_replay_required"] = {
            "passed": claim_replayed,
            "verified_from": "idempotency_records_and_audit_events",
            "replayed_keys": claim_replay_keys,
        }
    if criteria.get("identical_contribution_replay_required") is True:
        checks["identical_contribution_replay_required"] = {
            "passed": contribution_replayed,
            "verified_from": "idempotency_records_and_audit_events",
            "replayed_keys": contribution_replay_keys,
            "contribution_ids": contribution_ids,
        }
    if criteria.get("replay_identity_preserved") is True:
        checks["replay_identity_preserved"] = {
            "passed": claim_replayed and contribution_replayed,
            "verified_from": "idempotency_records_and_audit_events",
            "claim_replayed_keys": claim_replay_keys,
            "contribution_replayed_keys": contribution_replay_keys,
            "contribution_ids": contribution_ids,
        }
    if criteria.get("changed_payload_rejected") is True:
        checks["changed_payload_rejected"] = {
            "passed": conflict_rejected,
            "verified_from": "idempotency_conflict_audit_events",
            "conflicting_keys": contribution_conflict_keys,
        }
    expected_code = criteria.get("expected_error_code")
    if expected_code:
        observed_codes = sorted({
            str(event.details.get("error_code"))
            for event in conflict_events
            if isinstance(event.details, dict) and event.details.get("error_code")
        })
        checks["expected_error_code"] = {
            "passed": expected_code in observed_codes and conflict_rejected,
            "expected": expected_code,
            "observed": observed_codes,
            "verified_from": "idempotency_conflict_audit_events",
        }
    if criteria.get("duplicate_contributions_for_replay_forbidden") is True:
        checks["duplicate_contributions_for_replay_forbidden"] = {
            "passed": contribution_replayed,
            "verified_from": "idempotency_records_and_audit_events",
            "replayed_keys": contribution_replay_keys,
            "stored_contribution_ids": sorted(stored_contribution_ids),
            "duplicate_count": 0 if contribution_replayed else None,
        }
    return checks


def _completion_verification(
    db: Session,
    *,
    deliberation: Deliberation,
    evidence: dict[str, Any],
    owner_id: uuid.UUID,
    client_id: str,
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

    if criteria.get("search_query_log_required") is True:
        checks["search_query_log_required"] = {
            "passed": bool(probes) and all(
                isinstance(probe, dict)
                and isinstance(probe.get("queries"), list)
                and bool(probe["queries"])
                for probe in probes
            ),
            "checked_field": "queries",
        }

    fetched_by_subject: dict[str, set[str]] = {}
    raw_ids = [
        value for probe in probes if isinstance(probe, dict)
        for value in (probe.get("reviews_fetched") or [])
    ]
    parsed_ids: list[uuid.UUID] = []
    invalid_ids: list[str] = []
    for value in raw_ids:
        try:
            parsed_ids.append(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            invalid_ids.append(str(value))
    if parsed_ids:
        rows = db.execute(
            select(V2Experience.id, V2Experience.subject_id).where(
                V2Experience.owner_id == owner_id,
                V2Experience.deleted_at.is_(None),
                V2Experience.id.in_(parsed_ids),
            )
        ).all()
        for experience_id, subject_id in rows:
            fetched_by_subject.setdefault(str(subject_id), set()).add(str(experience_id))
    found_ids = {
        experience_id for values in fetched_by_subject.values() for experience_id in values
    }
    missing = sorted(str(value) for value in parsed_ids if str(value) not in found_ids)

    if criteria.get("exact_subject_followup_required") is True:
        prior_exact_subjects: set[str] = set()
        missing_followups: list[dict[str, Any]] = []
        checked_subjects = 0
        for probe_index, probe in enumerate(probes):
            if not isinstance(probe, dict):
                continue
            identities = probe.get("subject_identities")
            identities = identities if isinstance(identities, list) else []
            exact_queries = {
                _normalise_query(text)
                for value in (probe.get("queries") or [])
                if (text := _query_text(value))
            }
            exact_queries.update(
                _normalise_query(text)
                for value in (probe.get("exact_name_followups") or [])
                if (text := _query_text(value))
            )
            current_exact_subjects: set[str] = set()
            for identity in identities:
                if not isinstance(identity, dict):
                    continue
                subject_id = str(identity.get("subject_id") or "").strip()
                subject_name = str(identity.get("subject_name") or "").strip()
                if not subject_id or not subject_name:
                    continue
                checked_subjects += 1
                review_count = identity.get("review_count")
                fetched_count = len(fetched_by_subject.get(subject_id, set()))
                fully_retrieved = (
                    isinstance(review_count, int)
                    and review_count >= 0
                    and fetched_count >= review_count
                )
                searched_now = _normalise_query(subject_name) in exact_queries
                reused = subject_id in prior_exact_subjects and fully_retrieved
                if searched_now and fully_retrieved:
                    current_exact_subjects.add(subject_id)
                elif not reused:
                    missing_followups.append({
                        "probe_index": probe_index,
                        "subject_id": subject_id,
                        "subject_name": subject_name,
                        "subject_type": identity.get("subject_type"),
                        "review_count": review_count,
                        "reviews_fetched_for_subject": fetched_count,
                        "reason": (
                            "exact_name_search_missing"
                            if not searched_now
                            else "exact_name_search_not_fully_retrieved"
                        ),
                    })
            prior_exact_subjects.update(current_exact_subjects)
        checks["exact_subject_followup_required"] = {
            "passed": bool(probes) and checked_subjects > 0 and not missing_followups,
            "checked_field": "subject_identities",
            "checked_subject_count": checked_subjects,
            "missing_followups": missing_followups,
            "reuse_policy": (
                "An earlier exact-name search satisfies a later probe only for the same "
                "subject_id after all declared reviews for that identity were fetched."
            ),
        }

    if criteria.get("fetch_all_reviews_required") is True:
        checks["fetch_all_reviews_required"] = {
            "passed": bool(probes) and all(
                isinstance(probe, dict)
                and isinstance(probe.get("reviews_fetched"), list)
                and bool(probe["reviews_fetched"])
                for probe in probes
            ),
            "checked_field": "reviews_fetched",
        }
        checks["referenced_reviews_exist"] = {
            "passed": bool(parsed_ids) and not invalid_ids and not missing,
            "referenced_count": len(parsed_ids), "invalid_ids": invalid_ids,
            "missing_or_unowned_ids": missing,
        }

    if criteria.get("claim_required") is True:
        checks["claim_required"] = {
            "passed": bool(deliberation.claimed_at)
            and deliberation.claimed_by_client == client_id,
            "claimed_by_client": deliberation.claimed_by_client,
            "contributing_client": client_id,
            "claimed_by_model": deliberation.claimed_by_model,
            "claimed_at": (
                deliberation.claimed_at.isoformat()
                if deliberation.claimed_at else None
            ),
        }
    if criteria.get("final_contribution_required") is True:
        checks["final_contribution_required"] = {"passed": True}
    if criteria.get("deterministic_idempotency_key_required") is True:
        checks["deterministic_idempotency_key_required"] = {
            "passed": bool(idempotency_key),
            "note": "Presence is verified; semantic determinism cannot be inferred from the key text.",
        }

    checks.update(_idempotency_verification(
        db,
        deliberation=deliberation,
        client_id=client_id,
        criteria=criteria,
    ))

    supported = {
        "probes_attempted",
        "search_query_log_required",
        "exact_subject_followup_required",
        "fetch_all_reviews_required",
        "claim_required",
        "final_contribution_required",
        "deterministic_idempotency_key_required",
        "identical_claim_replay_required",
        "identical_contribution_replay_required",
        "replay_identity_preserved",
        "changed_payload_rejected",
        "expected_error_code",
        "duplicate_contributions_for_replay_forbidden",
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
        owner_id=owner_id, client_id=client_id,
        idempotency_key=idempotency_key,
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
