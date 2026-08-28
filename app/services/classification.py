from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import (
    SubjectClassificationDecision, SubjectType, TypeRelationship, V2Subject, now_utc,
)
from app.services.v2 import resolve_subject_type


REOPEN_TRIGGERS = {"user_correction", "contradictory_evidence", "type_retired", "vocabulary_invalidated"}


def _is_descendant(db: Session, child_id: uuid.UUID, ancestor_id: uuid.UUID) -> bool:
    """Return true only for a strict descendant through active belongs_to edges."""
    frontier = {child_id}
    seen: set[uuid.UUID] = set()
    while frontier:
        node = frontier.pop()
        if node in seen:
            continue
        seen.add(node)
        parents = set(db.scalars(select(TypeRelationship.target_type_id).where(
            TypeRelationship.source_type_id == node,
            TypeRelationship.relationship == "belongs_to",
            TypeRelationship.status == "active",
        )).all())
        if ancestor_id in parents:
            return True
        frontier.update(parents - seen)
    return False


def _decision_body(db: Session, decision: SubjectClassificationDecision) -> dict:
    target = db.get(SubjectType, decision.target_type_id)
    return {
        "id": str(decision.id),
        "classification_version": decision.classification_version,
        "target_subject_type": target.canonical_name if target else None,
        "source_model": decision.source_model,
        "reason": decision.reason,
        "evidence": decision.evidence_json,
        "evidence_fingerprint": decision.evidence_fingerprint,
        "outcome": decision.outcome,
        "created_at": decision.created_at.isoformat(),
    }


def classification_state(db: Session, subject: V2Subject) -> dict:
    current_type = db.get(SubjectType, subject.subject_type_id)
    decisions = list(db.scalars(select(SubjectClassificationDecision).where(
        SubjectClassificationDecision.subject_id == subject.id,
    ).order_by(SubjectClassificationDecision.created_at)).all())
    active = [d for d in decisions if d.classification_version == subject.classification_version]
    return {
        "subject_id": str(subject.id),
        "subject_type": current_type.canonical_name if current_type else None,
        "status": subject.classification_status,
        "version": subject.classification_version,
        "locked_at": subject.classification_locked_at.isoformat() if subject.classification_locked_at else None,
        "active_decisions": [_decision_body(db, d) for d in active],
        "audit_history": [_decision_body(db, d) for d in decisions],
    }


def propose_reclassification(
    db: Session, subject: V2Subject, *, target_subject_type: str, source_model: str,
    source_client: str, reason: str, evidence: dict, evidence_fingerprint: str | None = None,
    allow_current_type: bool = False,
) -> dict:
    model = source_model.strip()
    if not model:
        raise ValueError("source_model is required to prove independent AI agreement")
    if not reason.strip():
        raise ValueError("reason is required")
    target = resolve_subject_type(db, target_subject_type)
    if not target:
        raise ValueError(f"Unknown target subject type '{target_subject_type}'")
    current = db.get(SubjectType, subject.subject_type_id)
    if not current:
        raise ValueError("Current subject type not found")

    existing = db.scalar(select(SubjectClassificationDecision).where(
        SubjectClassificationDecision.subject_id == subject.id,
        SubjectClassificationDecision.classification_version == subject.classification_version,
        SubjectClassificationDecision.source_model == model,
    ))
    if existing:
        if existing.target_type_id != target.id:
            raise ValueError("This AI model already made a different decision in the current classification round")
        return classification_state(db, subject)

    if subject.classification_status != "confirmed":
        affirms_current = target.id == current.id
        if affirms_current and not allow_current_type:
            raise ValueError(
                "Use affirm_subject_classification to agree with the current provisional type"
            )
        if not affirms_current and not _is_descendant(db, target.id, current.id):
            raise ValueError(
                f"'{target.canonical_name}' is not a strict descendant of current type '{current.canonical_name}'"
            )

    decision = SubjectClassificationDecision(
        subject_id=subject.id,
        classification_version=subject.classification_version,
        from_type_id=current.id,
        target_type_id=target.id,
        source_model=model,
        source_client=source_client,
        reason=reason.strip(),
        evidence_json=evidence,
        evidence_fingerprint=evidence_fingerprint,
        outcome="ignored_locked" if subject.classification_status == "confirmed" else "candidate",
    )
    db.add(decision)
    db.flush()

    if subject.classification_status != "confirmed":
        active = list(db.scalars(select(SubjectClassificationDecision).where(
            SubjectClassificationDecision.subject_id == subject.id,
            SubjectClassificationDecision.classification_version == subject.classification_version,
            SubjectClassificationDecision.outcome == "candidate",
        )).all())
        counts = Counter(d.target_type_id for d in active)
        if len(counts) == 1 and counts[target.id] >= 2:
            collision = db.scalar(select(V2Subject).where(
                V2Subject.id != subject.id,
                V2Subject.subject_type_id == target.id,
                V2Subject.canonical_key == subject.canonical_key,
                V2Subject.deleted_at.is_(None),
            ))
            if collision:
                raise ValueError("Reclassification would collide with an existing subject")
            subject.subject_type_id = target.id
            subject.classification_status = "confirmed"
            subject.classification_locked_at = now_utc()
            for item in active:
                item.outcome = "confirmed"
        elif len(counts) > 1:
            subject.classification_status = "disputed"
        else:
            subject.classification_status = "candidate"

    db.commit()
    db.refresh(subject)
    return classification_state(db, subject)


def affirm_classification(
    db: Session, subject: V2Subject, *, source_model: str, source_client: str,
    reason: str, evidence: dict, evidence_fingerprint: str | None = None,
) -> dict:
    """Record an independent AI agreement with the subject's current type."""
    current = db.get(SubjectType, subject.subject_type_id)
    if not current:
        raise ValueError("Current subject type not found")
    return propose_reclassification(
        db,
        subject,
        target_subject_type=current.canonical_name,
        source_model=source_model,
        source_client=source_client,
        reason=reason,
        evidence=evidence,
        evidence_fingerprint=evidence_fingerprint,
        allow_current_type=True,
    )


def reopen_classification(
    db: Session, subject: V2Subject, *, trigger: str, reason: str,
    evidence: dict, requested_by: str, user_approved: bool = False,
) -> dict:
    if trigger not in REOPEN_TRIGGERS:
        raise ValueError(f"Unsupported reopening trigger '{trigger}'")
    if subject.classification_status != "confirmed":
        raise ValueError("Only a confirmed classification can be reopened")
    if trigger == "user_correction" and not user_approved:
        raise ValueError("user_approved is required for a user correction")
    if trigger == "contradictory_evidence" and not evidence:
        raise ValueError("Contradictory evidence is required")
    confirmed = db.scalar(select(SubjectClassificationDecision).where(
        SubjectClassificationDecision.subject_id == subject.id,
        SubjectClassificationDecision.classification_version == subject.classification_version,
        SubjectClassificationDecision.outcome == "confirmed",
    ).order_by(SubjectClassificationDecision.created_at))
    if not confirmed:
        raise ValueError("Confirmed classification audit is missing")
    settled_type_id = subject.subject_type_id
    subject.subject_type_id = confirmed.from_type_id
    subject.classification_version += 1
    subject.classification_status = "provisional"
    subject.classification_locked_at = None
    provenance = dict(subject.provenance_json or {})
    history = list(provenance.get("classification_reopenings", []))
    history.append({
        "version": subject.classification_version,
        "settled_type_id": str(settled_type_id),
        "restored_baseline_type_id": str(confirmed.from_type_id),
        "trigger": trigger,
        "reason": reason,
        "evidence": evidence,
        "requested_by": requested_by,
        "reopened_at": now_utc().isoformat(),
    })
    provenance["classification_reopenings"] = history
    subject.provenance_json = provenance
    db.commit()
    db.refresh(subject)
    return classification_state(db, subject)
