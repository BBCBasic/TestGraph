from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import SubjectType, TypeRelationship
from app.services.classification_mode import (
    classification_mode,
    taxonomy_relationship,
    validate_classification_relationship,
)
from app.services.semantic_head import validate_semantic_type_name
from app.services.v2 import (
    add_subject_type_alias,
    canonical_label,
    ensure_subject_type,
    normalise_term,
    resolve_subject_type,
)
from app.services.synthetic_root import assert_semantic_type, attach_root_if_needed


class VocabularyConvergenceRequired(ValueError):
    """A new typed peer needs an explicit semantic reuse-or-create decision."""

    def __init__(
        self,
        *,
        term: str,
        parent: str,
        candidates: list[str],
        candidates_truncated: bool = False,
    ):
        self.term = term
        self.parent = parent
        self.candidates = candidates
        self.candidates_truncated = candidates_truncated
        super().__init__(
            f"A convergence decision is required before creating '{term}' beneath "
            f"'{parent}'. Reuse an equivalent existing child or justify a distinct "
            f"type after comparing these candidates: {', '.join(candidates)}"
        )


def _has_relationship_path(
    db: Session,
    source_type_id: uuid.UUID,
    target_type_id: uuid.UUID,
    relationship: str,
) -> bool:
    """Return True when following one edge meaning can reach target_type_id."""
    if source_type_id == target_type_id:
        return True
    seen = {source_type_id}
    frontier = {source_type_id}
    while frontier:
        rows = list(db.scalars(select(TypeRelationship).where(
            TypeRelationship.relationship == relationship,
            TypeRelationship.status == "active",
            TypeRelationship.source_type_id.in_(frontier),
        )).all())
        parents = {row.target_type_id for row in rows}
        if target_type_id in parents:
            return True
        frontier = parents - seen
        seen |= frontier
    return False


def _lock_taxonomy_parent(db: Session, parent: SubjectType) -> None:
    """Serialize peer checks and inserts for one parent on databases with row locks."""
    db.execute(
        select(SubjectType.id)
        .where(SubjectType.id == parent.id)
        .with_for_update()
    ).scalar_one()


def _foreign_peer_candidates(
    db: Session,
    parent: SubjectType,
    *,
    actor: str,
    exclude_type_id: uuid.UUID | None = None,
    limit: int = 100,
) -> tuple[list[SubjectType], bool]:
    statement = (
        select(SubjectType)
        .join(TypeRelationship, TypeRelationship.source_type_id == SubjectType.id)
        .where(
            TypeRelationship.target_type_id == parent.id,
            TypeRelationship.relationship == taxonomy_relationship(),
            TypeRelationship.status == "active",
            SubjectType.is_synthetic.is_(False),
            SubjectType.created_by != actor,
        )
        .order_by(SubjectType.normalized_name, SubjectType.id)
    )
    if exclude_type_id is not None:
        statement = statement.where(SubjectType.id != exclude_type_id)
    rows = list(db.scalars(statement.limit(limit + 1)).all())
    return rows[:limit], len(rows) > limit


def _is_active_taxonomy_child(
    db: Session,
    *,
    child: SubjectType,
    parent: SubjectType,
) -> bool:
    return db.scalar(select(TypeRelationship.id).where(
        TypeRelationship.source_type_id == child.id,
        TypeRelationship.target_type_id == parent.id,
        TypeRelationship.relationship == taxonomy_relationship(),
        TypeRelationship.status == "active",
    )) is not None


def add_semantic_relationship(
    db: Session,
    source_type: SubjectType,
    relationship: str,
    target_type: SubjectType,
    *,
    source: str,
    commit: bool = True,
    semantic_justification: str | None = None,
    peer_decision: dict | None = None,
) -> TypeRelationship:
    """Add a relationship while preventing semantic-head mistakes, cycles and stale classifications.

    Legacy ``belongs_to`` has one active parent and retires a replaced parent. Typed mode
    accepts independent ``is_a`` and ``part_of`` edges and permits multiple valid parents.

    Previously retired exact edges remain tombstoned and cannot be silently recreated.
    """
    assert_semantic_type(source_type)
    assert_semantic_type(target_type)
    validate_semantic_type_name(
        source_type.canonical_name,
        distinct_class_justification=semantic_justification,
    )
    validate_semantic_type_name(
        target_type.canonical_name,
        distinct_class_justification=semantic_justification,
    )

    rel = validate_classification_relationship(normalise_term(relationship).replace(" ", "_"))
    if source_type.id == target_type.id:
        raise ValueError("A subject type cannot relate to itself")
    cycle_checked_relationships = (
        {"is_a", "part_of"} if classification_mode() == "typed" else {"belongs_to"}
    )
    if rel in cycle_checked_relationships and _has_relationship_path(
        db, target_type.id, source_type.id, rel,
    ):
        raise ValueError(
            f"Relationship would create a cycle: '{source_type.canonical_name}' {rel} "
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

    if classification_mode() == "typed" and rel == "is_a":
        _lock_taxonomy_parent(db, target_type)
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
        candidates, candidates_truncated = _foreign_peer_candidates(
            db,
            target_type,
            actor=source_type.created_by,
            exclude_type_id=source_type.id,
        )
        decision = peer_decision or {}
        if candidates and (
            decision.get("decision") != "create"
            or not str(decision.get("reason", "")).strip()
        ):
            raise VocabularyConvergenceRequired(
                term=source_type.canonical_name,
                parent=target_type.canonical_name,
                candidates=[item.canonical_name for item in candidates],
                candidates_truncated=candidates_truncated,
            )

    # Classification is editable. If an AI supplies a new belongs_to target, retire the
    # previous active parent automatically while keeping a full audit trail. A database
    # containing multiple active parents is treated as ambiguous legacy state rather than
    # guessed at automatically.
    if classification_mode() == "legacy" and rel == "belongs_to":
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
    if classification_mode() == "typed" and rel == "is_a":
        db.flush()
        attach_root_if_needed(db, source_type, commit=False)
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
    assert_semantic_type(source_type)
    assert_semantic_type(target_type)
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
    if classification_mode() == "typed" and rel == "is_a":
        db.flush()
        attach_root_if_needed(db, source_type, commit=False)
    db.commit()
    db.refresh(obj)
    return obj


def resolve_subject_hierarchy(
    db: Session,
    terms: list[str],
    *,
    created_by: str,
    semantic_justification: str | None = None,
    peer_decisions: list[dict] | None = None,
) -> dict:
    """Resolve/create a broad-to-specific semantic hierarchy as one transaction.

    The semantic-head guard is server-owned: obvious material, arrangement, state,
    colour, size, quantity, location and purpose modifiers are rejected as type nodes
    unless a distinct-class justification is supplied. The rest of the function supplies
    deterministic dictionary reuse, provisional creation, active taxonomy links and cycle safety.
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

    decisions: dict[str, dict] = {}
    for raw_decision in peer_decisions or []:
        if not isinstance(raw_decision, dict):
            raise ValueError("Each peer decision must be an object")
        decision_term = normalise_term(str(raw_decision.get("term", "")))
        if decision_term not in keys:
            raise ValueError(f"Peer decision term '{decision_term}' is not in the hierarchy")
        if decision_term in decisions:
            raise ValueError(f"Duplicate peer decision for '{decision_term}'")
        decision = str(raw_decision.get("decision", "")).strip().casefold()
        if decision not in {"reuse", "create"}:
            raise ValueError("Peer decision must be 'reuse' or 'create'")
        reason = str(raw_decision.get("reason", "")).strip()
        if not reason:
            raise ValueError(f"Peer decision for '{decision_term}' requires a reason")
        decisions[decision_term] = {**raw_decision, "decision": decision, "reason": reason}

    # Perform all dictionary lookups before creating anything. This makes vocabulary
    # discovery independent of the order in which reviews happen to arrive.
    resolved_before = [resolve_subject_type(db, term) for term in cleaned]
    if classification_mode() == "legacy" and len(cleaned) == 1 and resolved_before[0] is None:
        raise ValueError(
            f"Unknown subject type '{cleaned[0]}' cannot be created as an isolated root. "
            "Use list_root_subject_types and list_child_subject_types to find the best existing parent, "
            "then provide a broad-to-specific hierarchy, "
            "for example ['food', 'recipe']."
        )

    try:
        resolved: list[tuple[SubjectType, bool, str]] = []
        convergence: list[dict] = []
        for index, term in enumerate(cleaned):
            if classification_mode() == "typed" and resolved_before[index] is None:
                if index == 0:
                    from app.services.synthetic_root import ensure_synthetic_root
                    parent = ensure_synthetic_root(db, commit=False)
                else:
                    parent = resolved[index - 1][0]
                _lock_taxonomy_parent(db, parent)
                resolved_after_lock = resolve_subject_type(db, term)
                if resolved_after_lock is not None:
                    resolution = (
                        "canonical"
                        if resolved_after_lock.normalized_name == keys[index]
                        else "alias"
                    )
                    resolved.append((resolved_after_lock, False, resolution))
                    continue
                candidates, candidates_truncated = _foreign_peer_candidates(
                    db,
                    parent,
                    actor=created_by,
                )
                if candidates:
                    decision = decisions.get(keys[index])
                    if decision is None:
                        raise VocabularyConvergenceRequired(
                            term=canonical_label(term),
                            parent=parent.canonical_name,
                            candidates=[item.canonical_name for item in candidates],
                            candidates_truncated=candidates_truncated,
                        )
                    if decision["decision"] == "reuse":
                        existing_term = str(decision.get("existing_type", "")).strip()
                        existing = resolve_subject_type(db, existing_term)
                        if existing is None or not _is_active_taxonomy_child(
                            db,
                            child=existing,
                            parent=parent,
                        ):
                            raise ValueError(
                                f"Reuse target '{existing_term}' must be an active immediate child "
                                f"of '{parent.canonical_name}'"
                            )
                        add_subject_type_alias(
                            db,
                            existing,
                            term,
                            source=created_by,
                            commit=False,
                        )
                        resolved.append((existing, False, "registered_alias"))
                        convergence.append({
                            "term": canonical_label(term),
                            "decision": "reuse",
                            "canonical_name": existing.canonical_name,
                            "resolution": "registered_alias",
                        })
                        continue
                    convergence.append({
                        "term": canonical_label(term),
                        "decision": "create",
                        "canonical_name": canonical_label(term),
                        "resolution": "created_distinct",
                    })
            resolved.append(ensure_subject_type(
                db,
                term,
                created_by=created_by,
                create_if_missing=True,
                commit=False,
            ))

        hierarchy_relationship = taxonomy_relationship()
        for index, (parent_result, child_result) in enumerate(
            zip(resolved, resolved[1:]),
            start=1,
        ):
            parent = parent_result[0]
            child = child_result[0]
            add_semantic_relationship(
                db,
                child,
                hierarchy_relationship,
                parent,
                source=created_by,
                commit=False,
                semantic_justification=semantic_justification,
                peer_decision=decisions.get(keys[index]),
            )

        db.commit()
        for subject_type, _, _ in resolved:
            db.refresh(subject_type)

        return {
            "leaf": resolved[-1][0],
            "taxonomy_relationship": hierarchy_relationship,
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
                    "relationship": hierarchy_relationship,
                    "target": parent_result[0].canonical_name,
                }
                for parent_result, child_result in zip(resolved, resolved[1:])
            ],
            "convergence": convergence,
        }
    except Exception:
        db.rollback()
        raise
