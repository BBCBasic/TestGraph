from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.v2 import SubjectType, TypeRelationship, V2Subject
from app.services.classification_mode import classification_mode


SYNTHETIC_ROOT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SYNTHETIC_ROOT_NAME = "."
SYNTHETIC_ROOT_SOURCE = "system:synthetic-root"
SYNTHETIC_ROOT_DESCRIPTION = "Non-semantic universal root of the TestGraph is_a topology."


def is_synthetic_root(subject_type: SubjectType | None) -> bool:
    return bool(subject_type and subject_type.id == SYNTHETIC_ROOT_ID and subject_type.is_synthetic)


def assert_semantic_type(subject_type: SubjectType) -> None:
    if is_synthetic_root(subject_type) or subject_type.is_synthetic:
        raise ValueError("The synthetic root is infrastructure and cannot be used as a semantic type")


def ensure_synthetic_root(db: Session, *, commit: bool = True) -> SubjectType:
    root = db.get(SubjectType, SYNTHETIC_ROOT_ID)
    named_root = db.scalar(select(SubjectType).where(
        SubjectType.normalized_name == SYNTHETIC_ROOT_NAME
    ))
    synthetic_count = db.scalar(select(func.count()).select_from(SubjectType).where(
        SubjectType.is_synthetic.is_(True)
    )) or 0
    if root is not None:
        if (
            root.canonical_name != SYNTHETIC_ROOT_NAME
            or root.normalized_name != SYNTHETIC_ROOT_NAME
            or root.status != "system"
            or not root.is_synthetic
            or root.description != SYNTHETIC_ROOT_DESCRIPTION
            or root.public_location_eligible
            or root.created_by != SYNTHETIC_ROOT_SOURCE
            or (named_root is not None and named_root.id != root.id)
            or synthetic_count != 1
        ):
            raise ValueError("Synthetic root integrity violation")
        return root
    if named_root is not None or synthetic_count:
        raise ValueError("Synthetic root integrity violation")
    root = SubjectType(
        id=SYNTHETIC_ROOT_ID,
        canonical_name=SYNTHETIC_ROOT_NAME,
        normalized_name=SYNTHETIC_ROOT_NAME,
        description=SYNTHETIC_ROOT_DESCRIPTION,
        status="system",
        is_synthetic=True,
        created_by=SYNTHETIC_ROOT_SOURCE,
    )
    db.add(root)
    if commit:
        db.commit()
        db.refresh(root)
    else:
        db.flush()
    return root


def _active_root_edge(db: Session, subject_type_id: uuid.UUID) -> TypeRelationship | None:
    return db.scalar(select(TypeRelationship).where(
        TypeRelationship.source_type_id == subject_type_id,
        TypeRelationship.relationship == "is_a",
        TypeRelationship.target_type_id == SYNTHETIC_ROOT_ID,
        TypeRelationship.status == "active",
    ))


def attach_root_if_needed(
    db: Session, subject_type: SubjectType, *, commit: bool = True
) -> TypeRelationship | None:
    """Make a semantic type a direct root iff it has no meaningful is_a parent."""
    if classification_mode() != "typed":
        return None
    assert_semantic_type(subject_type)
    root = ensure_synthetic_root(db, commit=False)
    semantic_parent = db.scalar(select(TypeRelationship.id).where(
        TypeRelationship.source_type_id == subject_type.id,
        TypeRelationship.relationship == "is_a",
        TypeRelationship.target_type_id != root.id,
        TypeRelationship.status == "active",
    ).limit(1))
    root_edge = _active_root_edge(db, subject_type.id)
    changed = False
    if semantic_parent is not None:
        if root_edge is not None:
            db.delete(root_edge)
            db.flush()
            changed = True
        if commit and changed:
            db.commit()
        return None
    if root_edge is None:
        root_edge = TypeRelationship(
            source_type_id=subject_type.id,
            relationship="is_a",
            target_type_id=root.id,
            source=SYNTHETIC_ROOT_SOURCE,
        )
        db.add(root_edge)
        db.flush()
        changed = True
    if commit and changed:
        db.commit()
        db.refresh(root_edge)
    return root_edge


def check_synthetic_root_integrity(db: Session) -> dict:
    roots = list(db.scalars(select(SubjectType).where(
        SubjectType.is_synthetic.is_(True)
    )).all())
    reserved = db.get(SubjectType, SYNTHETIC_ROOT_ID)
    root_identity_valid = (
        len(roots) == 1
        and reserved is not None
        and roots[0].id == reserved.id
        and reserved.canonical_name == SYNTHETIC_ROOT_NAME
        and reserved.normalized_name == SYNTHETIC_ROOT_NAME
        and reserved.status == "system"
    )

    reachable = {SYNTHETIC_ROOT_ID} if root_identity_valid else set()
    frontier = set(reachable)
    while frontier:
        children = set(db.scalars(select(TypeRelationship.source_type_id).where(
            TypeRelationship.target_type_id.in_(frontier),
            TypeRelationship.relationship == "is_a",
            TypeRelationship.status == "active",
        )).all())
        frontier = children - reachable
        reachable |= frontier

    semantic_types = list(db.scalars(select(SubjectType).where(
        SubjectType.is_synthetic.is_(False)
    ).order_by(func.lower(SubjectType.canonical_name), SubjectType.id)).all())
    orphans = [item for item in semantic_types if item.id not in reachable]
    root_parent_edges = db.scalar(select(func.count()).select_from(TypeRelationship).where(
        TypeRelationship.source_type_id == SYNTHETIC_ROOT_ID,
        TypeRelationship.relationship == "is_a",
        TypeRelationship.status == "active",
    )) or 0
    illegal_subjects = db.scalar(select(func.count()).select_from(V2Subject).where(
        V2Subject.subject_type_id == SYNTHETIC_ROOT_ID,
        V2Subject.deleted_at.is_(None),
    )) or 0
    report = {
        "root_count": len(roots),
        "root_identity_valid": root_identity_valid,
        "orphan_semantic_roots": [
            {"id": str(item.id), "canonical_name": item.canonical_name} for item in orphans
        ],
        "root_parent_edges": int(root_parent_edges),
        "subjects_classified_as_root": int(illegal_subjects),
    }
    report["valid"] = (
        root_identity_valid and not orphans and not root_parent_edges and not illegal_subjects
    )
    return report
