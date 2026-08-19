from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import SubjectType, TypeRelationship
from app.services.v2 import ensure_subject_type, normalise_term, resolve_subject_type


def _has_belongs_to_path(db: Session, source_type_id: uuid.UUID, target_type_id: uuid.UUID) -> bool:
    """Return True when following belongs_to edges upward can reach target_type_id."""
    if source_type_id == target_type_id:
        return True
    seen = {source_type_id}
    frontier = {source_type_id}
    while frontier:
        rows = list(db.scalars(select(TypeRelationship).where(
            TypeRelationship.relationship == "belongs_to",
            TypeRelationship.status == "active",
            TypeRelationship.source_type_id.in_(frontier),
        )).all())
        parents = {row.target_type_id for row in rows}
        if target_type_id in parents:
            return True
        frontier = parents - seen
        seen |= frontier
    return False


def add_semantic_relationship(
    db: Session,
    source_type: SubjectType,
    relationship: str,
    target_type: SubjectType,
    *,
    source: str,
    commit: bool = True,
) -> TypeRelationship:
    """Add a relationship while preventing belongs_to cycles."""
    rel = normalise_term(relationship).replace(" ", "_")
    if source_type.id == target_type.id:
        raise ValueError("A subject type cannot relate to itself")
    if rel == "belongs_to" and _has_belongs_to_path(db, target_type.id, source_type.id):
        raise ValueError(
            f"Relationship would create a cycle: '{source_type.canonical_name}' belongs_to "
            f"'{target_type.canonical_name}'"
        )
    existing = db.scalar(select(TypeRelationship).where(
        TypeRelationship.source_type_id == source_type.id,
        TypeRelationship.relationship == rel,
        TypeRelationship.target_type_id == target_type.id,
    ))
    if existing:
        if existing.status == "retired":
            raise ValueError(
                f"Relationship was previously rejected: '{source_type.canonical_name}' {rel} "
                f"'{target_type.canonical_name}'. It cannot be recreated automatically."
            )
        return existing
    obj = TypeRelationship(
        source_type_id=source_type.id,
        relationship=rel,
        target_type_id=target_type.id,
        source=source,
    )
    db.add(obj)
    if commit:
        db.commit()
        db.refresh(obj)
    else:
        db.flush()
    return obj


def retire_semantic_relationship(
    db: Session,
    source_type: SubjectType,
    relationship: str,
    target_type: SubjectType,
    *,
    reason: str,
    retired_by: str,
) -> TypeRelationship:
    """Retire an exact edge while preserving a tombstone against AI flip-flopping."""
    rel = normalise_term(relationship).replace(" ", "_")
    obj = db.scalar(select(TypeRelationship).where(
        TypeRelationship.source_type_id == source_type.id,
        TypeRelationship.relationship == rel,
        TypeRelationship.target_type_id == target_type.id,
    ))
    if not obj:
        raise ValueError(
            f"Relationship does not exist: '{source_type.canonical_name}' {rel} "
            f"'{target_type.canonical_name}'"
        )
    if obj.status == "retired":
        return obj
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("A reason is required when retiring a relationship")
    obj.status = "retired"
    obj.retired_reason = clean_reason
    obj.retired_by = retired_by
    obj.retired_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(obj)
    return obj


def resolve_subject_hierarchy(db: Session, terms: list[str], *, created_by: str) -> dict:
    """Resolve/create a broad-to-specific semantic hierarchy as one transaction.

    Semantic judgement belongs to the AI/client after it has inspected the whole
    vocabulary. This function supplies deterministic governance: dictionary reuse,
    provisional creation only in context, validated belongs_to links and cycle safety.
    """
    cleaned = [str(term).strip() for term in terms if str(term).strip()]
    if not cleaned:
        raise ValueError("At least one hierarchy term is required")
    if len(cleaned) > 8:
        raise ValueError("Hierarchy is implausibly deep; use at most 8 broad-to-specific terms")

    keys = [normalise_term(term) for term in cleaned]
    if len(set(keys)) != len(keys):
        raise ValueError("Hierarchy contains duplicate or mechanically equivalent terms")

    # Perform all dictionary lookups before creating anything. This makes vocabulary
    # discovery independent of the order in which reviews happen to arrive.
    resolved_before = [resolve_subject_type(db, term) for term in cleaned]
    if len(cleaned) == 1 and resolved_before[0] is None:
        raise ValueError(
            f"Unknown subject type '{cleaned[0]}' cannot be created as an isolated root. "
            "Inspect vocabulary_index and provide a broad-to-specific hierarchy, "
            "for example ['food', 'recipe']."
        )

    try:
        resolved: list[tuple[SubjectType, bool, str]] = []
        for term in cleaned:
            resolved.append(ensure_subject_type(
                db,
                term,
                created_by=created_by,
                create_if_missing=True,
                commit=False,
            ))

        for parent_result, child_result in zip(resolved, resolved[1:]):
            parent = parent_result[0]
            child = child_result[0]
            add_semantic_relationship(
                db,
                child,
                "belongs_to",
                parent,
                source=created_by,
                commit=False,
            )

        db.commit()
        for subject_type, _, _ in resolved:
            db.refresh(subject_type)

        return {
            "leaf": resolved[-1][0],
            "path": [
                {
                    "id": str(subject_type.id),
                    "canonical_name": subject_type.canonical_name,
                    "status": subject_type.status,
                    "created": created,
                    "resolution": resolution,
                }
                for subject_type, created, resolution in resolved
            ],
            "created_terms": [
                subject_type.canonical_name
                for subject_type, created, _ in resolved
                if created
            ],
            "relationships": [
                {
                    "source": child_result[0].canonical_name,
                    "relationship": "belongs_to",
                    "target": parent_result[0].canonical_name,
                }
                for parent_result, child_result in zip(resolved, resolved[1:])
            ],
        }
    except Exception:
        db.rollback()
        raise
