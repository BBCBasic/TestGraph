from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import SubjectType, TypeRelationship
from app.services.semantic_head import validate_semantic_type_name
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
    semantic_justification: str | None = None,
) -> TypeRelationship:
    """Add a relationship while preventing semantic-head mistakes, cycles and stale classifications.

    ``belongs_to`` is the canonical classification edge in TestGraph. A subject type may
    have one active parent classification at a time. When an AI later supplies a different
    parent through the existing ``set_type_relationship`` tool, the old active edge is
    retired automatically and retained as provenance instead of forcing a human to perform
    a separate retire-then-add sequence.

    Previously retired exact edges remain tombstoned and cannot be silently recreated.
    """
    validate_semantic_type_name(
        source_type.canonical_name,
        distinct_class_justification=semantic_justification,
    )
    validate_semantic_type_name(
        target_type.canonical_name,
        distinct_class_justification=semantic_justification,
    )

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

    # Classification is editable. If an AI supplies a new belongs_to target, retire the
    # previous active parent automatically while keeping a full audit trail. A database
    # containing multiple active parents is treated as ambiguous legacy state rather than
    # guessed at automatically.
    if rel == "belongs_to":
        active_parents = list(db.scalars(select(TypeRelationship).where(
            TypeRelationship.source_type_id == source_type.id,
            TypeRelationship.relationship == "belongs_to",
            TypeRelationship.status == "active",
        )).all())
        if len(active_parents) > 1:
            raise ValueError(
                f"Cannot automatically reclassify '{source_type.canonical_name}': "
                "multiple active belongs_to relationships already exist. Retire the incorrect edges first."
            )
        if active_parents:
            previous = active_parents[0]
            previous.status = "retired"
            previous.retired_reason = (
                f"Automatically reclassified from the previous belongs_to target by {source}"
            )
            previous.retired_by = source
            previous.retired_at = datetime.now(timezone.utc)
            db.flush()

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


def resolve_subject_hierarchy(
    db: Session,
    terms: list[str],
    *,
    created_by: str,
    semantic_justification: str | None = None,
) -> dict:
    """Resolve/create a broad-to-specific semantic hierarchy as one transaction.

    The semantic-head guard is server-owned: obvious material, arrangement, state,
    colour, size, quantity, location and purpose modifiers are rejected as type nodes
    unless a distinct-class justification is supplied. The rest of the function supplies
    deterministic dictionary reuse, provisional creation, belongs_to links and cycle safety.
    """
    cleaned = [str(term).strip() for term in terms if str(term).strip()]
    if not cleaned:
        raise ValueError("At least one hierarchy term is required")
    if len(cleaned) > 8:
        raise ValueError("Hierarchy is implausibly deep; use at most 8 broad-to-specific terms")

    for term in cleaned:
        validate_semantic_type_name(
            term,
            distinct_class_justification=semantic_justification,
        )

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
                semantic_justification=semantic_justification,
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
