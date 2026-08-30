from __future__ import annotations

import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.v2 import SubjectType, V2Experience, V2Subject
from app.models.workflow import WorkflowRun
from app.services.deliberation import DeliberationError


def _lock_client_lane(db: Session, owner_id, client_id: str) -> None:
    """Serialize new-subject classification writes for one user/client lane on PostgreSQL."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    key = f"testgraph:classification-write:{owner_id}:{client_id}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


def _has_historical_experience(db: Session, subject: V2Subject) -> bool:
    return db.scalar(select(V2Experience.id).where(
        V2Experience.subject_id == subject.id,
        V2Experience.deleted_at.is_(None),
    ).limit(1)) is not None


def _run_belongs_to_client(db: Session, run: WorkflowRun, client_id: str) -> bool:
    context_client = (run.context_json or {}).get("source_client")
    if context_client:
        return context_client == client_id
    # Backfill-safe inference for workflows created before source_client was stored in context.
    experience_client = db.scalar(select(V2Experience.created_by_client).where(
        V2Experience.subject_id == run.subject_id,
        V2Experience.deleted_at.is_(None),
    ).order_by(V2Experience.created_at.asc()).limit(1))
    return experience_client == client_id


def _pending_first_decision(db: Session, owner_id, client_id: str, subject_id):
    runs = db.scalars(select(WorkflowRun).where(
        WorkflowRun.workflow_type == "enrich_subject",
        WorkflowRun.owner_id == owner_id,
        WorkflowRun.state == "classification_review_required",
        WorkflowRun.subject_id != subject_id,
    ).order_by(WorkflowRun.created_at.asc())).all()
    return next((run for run in runs if _run_belongs_to_client(db, run, client_id)), None)


def _latest_other_experience(db: Session, owner_id, client_id: str, subject_id):
    return db.execute(
        select(V2Experience, V2Subject)
        .join(V2Subject, V2Experience.subject_id == V2Subject.id)
        .where(
            V2Experience.owner_id == owner_id,
            V2Experience.created_by_client == client_id,
            V2Experience.deleted_at.is_(None),
            V2Subject.deleted_at.is_(None),
            V2Subject.id != subject_id,
        )
        .order_by(V2Experience.created_at.desc(), V2Experience.id.desc())
        .limit(1)
    ).first()


def _subject_tokens(subject: V2Subject) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", f"{subject.name} {subject.canonical_key}".casefold()))
    return {token for token in tokens if len(token) >= 4}


def _valid_specific_confirmation(subject: V2Subject, confirmation) -> tuple[bool, list[str]]:
    problems = []
    if not isinstance(confirmation, dict):
        return False, ["classification_confirmation must be an object"]
    if confirmation.get("subject_ref") not in {subject.canonical_key, str(subject.id)}:
        problems.append("subject_ref must identify this subject")
    if confirmation.get("independently_assessed") is not True:
        problems.append("independently_assessed must be true")
    if confirmation.get("specificity_checked") is not True:
        problems.append("specificity_checked must be true")
    evidence = str(confirmation.get("evidence") or "").strip()
    specificity = str(confirmation.get("specificity_reason") or "").strip()
    if len(evidence) < 20:
        problems.append("evidence must be subject-specific and substantive")
    if len(specificity) < 20:
        problems.append("specificity_reason must explain why this type is sufficiently precise")
    tokens = _subject_tokens(subject)
    combined = f"{evidence} {specificity}".casefold()
    if tokens and not any(token in combined for token in tokens):
        problems.append("confirmation text must refer to this subject rather than use generic batch wording")
    return not problems, problems


def enforce_sequential_classification_write(
    db: Session,
    subject: V2Subject,
    *,
    owner_id,
    client_id: str,
) -> dict | None:
    """Guard creation of a first review for a new subject.

    Existing reviewed subjects are deliberately exempt. For a new subject, one client must
    complete its first classification decision on the previous subject before moving on.
    Once that first decision exists (the workflow advances beyond classification_review_required),
    the client may continue without waiting for the independent second model.
    """
    if _has_historical_experience(db, subject):
        return None

    _lock_client_lane(db, owner_id, client_id)

    pending = _pending_first_decision(db, owner_id, client_id, subject.id)
    if pending is not None:
        raise DeliberationError(
            "CLASSIFICATION_WORKFLOW_PENDING",
            "Finish the previous subject's first classification decision before saving another new subject",
            {
                "pending_subject_id": str(pending.subject_id),
                "workflow_run_id": str(pending.id),
                "required_action": "get_subject_classification",
                "required_arguments": {"subject_id": str(pending.subject_id)},
                "instruction": (
                    "Inspect that subject, then call exactly one of affirm_subject_classification or "
                    "propose_subject_reclassification. After that first decision is recorded, this client may "
                    "continue to the next item without waiting for the second model."
                ),
            },
        )

    previous = _latest_other_experience(db, owner_id, client_id, subject.id)
    if previous is None:
        return None
    previous_experience, previous_subject = previous
    if previous_subject.subject_type_id != subject.subject_type_id:
        return None

    subject_type = db.get(SubjectType, subject.subject_type_id)
    confirmation = (subject.provenance_json or {}).get("classification_confirmation")
    valid, problems = _valid_specific_confirmation(subject, confirmation)
    if not valid:
        code = (
            "CLASSIFICATION_REUSE_CONFIRMATION_REQUIRED"
            if confirmation is None
            else "CLASSIFICATION_REUSE_CONFIRMATION_INVALID"
        )
        raise DeliberationError(
            code,
            "Reusing the previous classification for a new subject requires explicit subject-specific confirmation",
            {
                "subject_id": str(subject.id),
                "subject_ref": subject.canonical_key,
                "subject_type": subject_type.canonical_name if subject_type else None,
                "previous_subject_id": str(previous_subject.id),
                "previous_experience_id": str(previous_experience.id),
                "problems": problems,
                "required_confirmation": {
                    "path": "subject_provenance.classification_confirmation",
                    "fields": [
                        "subject_ref", "independently_assessed", "evidence",
                        "specificity_checked", "specificity_reason",
                    ],
                },
                "instruction": (
                    "Assess this subject independently. If the same type is still correct, retry with a confirmation "
                    "whose subject_ref is this canonical_key, independently_assessed=true, substantive evidence, "
                    "specificity_checked=true, and a subject-specific specificity_reason."
                ),
            },
        )
    return dict(confirmation)
