from __future__ import annotations

import base64
import binascii
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.v2 import SubjectType, SubjectTypeAlias, TypeRelationship
from app.services.v2 import resolve_subject_type


DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100


def _bounded_limit(value: int) -> int:
    return max(1, min(int(value), MAX_PAGE_LIMIT))


def _encode_cursor(*, operation: str, parent_id: uuid.UUID | None, offset: int) -> str:
    payload = json.dumps({
        "operation": operation,
        "parent_id": str(parent_id) if parent_id else None,
        "offset": offset,
    }, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(
    cursor: str | None, *, operation: str, parent_id: uuid.UUID | None,
) -> int:
    if not cursor:
        return 0
    try:
        token = str(cursor)
        token += "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    except (binascii.Error, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid vocabulary cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid vocabulary cursor")
    expected_parent = str(parent_id) if parent_id else None
    if payload.get("operation") != operation or payload.get("parent_id") != expected_parent:
        raise ValueError("Vocabulary cursor does not match this navigation branch")
    try:
        offset = int(payload.get("offset", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid vocabulary cursor") from exc
    if offset < 0:
        raise ValueError("Vocabulary cursor contains an invalid offset")
    return offset


def _type_summaries(db: Session, subject_types: list[SubjectType]) -> list[dict]:
    if not subject_types:
        return []
    type_ids = [item.id for item in subject_types]
    alias_rows = db.execute(
        select(SubjectTypeAlias.subject_type_id, SubjectTypeAlias.alias)
        .where(SubjectTypeAlias.subject_type_id.in_(type_ids))
        .order_by(func.lower(SubjectTypeAlias.alias), SubjectTypeAlias.id)
    ).all()
    aliases: dict[uuid.UUID, list[str]] = {type_id: [] for type_id in type_ids}
    for type_id, alias in alias_rows:
        aliases[type_id].append(alias)

    child_rows = db.execute(
        select(TypeRelationship.target_type_id, func.count(TypeRelationship.source_type_id))
        .where(
            TypeRelationship.target_type_id.in_(type_ids),
            TypeRelationship.relationship == "belongs_to",
            TypeRelationship.status == "active",
        )
        .group_by(TypeRelationship.target_type_id)
    ).all()
    child_counts = {type_id: count for type_id, count in child_rows}
    return [
        {
            "id": str(item.id),
            "canonical_name": item.canonical_name,
            "status": item.status,
            "description": item.description,
            "aliases": aliases[item.id],
            "child_count": int(child_counts.get(item.id, 0)),
        }
        for item in subject_types
    ]


def _page(
    db: Session,
    statement,
    *,
    operation: str,
    parent_id: uuid.UUID | None,
    limit: int,
    cursor: str | None,
) -> dict:
    page_limit = _bounded_limit(limit)
    offset = _decode_cursor(cursor, operation=operation, parent_id=parent_id)
    rows = list(db.scalars(statement.offset(offset).limit(page_limit + 1)).all())
    has_more = len(rows) > page_limit
    items = rows[:page_limit]
    return {
        "items": _type_summaries(db, items),
        "count": len(items),
        "has_more": has_more,
        "next_cursor": _encode_cursor(
            operation=operation,
            parent_id=parent_id,
            offset=offset + len(items),
        ) if has_more else None,
    }


def list_root_subject_types(
    db: Session, *, limit: int = DEFAULT_PAGE_LIMIT, cursor: str | None = None,
) -> dict:
    active_parent = select(TypeRelationship.id).where(
        TypeRelationship.source_type_id == SubjectType.id,
        TypeRelationship.relationship == "belongs_to",
        TypeRelationship.status == "active",
    ).exists()
    statement = (
        select(SubjectType)
        .where(~active_parent)
        .order_by(func.lower(SubjectType.canonical_name), SubjectType.id)
    )
    return _page(
        db,
        statement,
        operation="roots",
        parent_id=None,
        limit=limit,
        cursor=cursor,
    )


def list_child_subject_types(
    db: Session,
    parent: SubjectType | str,
    *,
    limit: int = DEFAULT_PAGE_LIMIT,
    cursor: str | None = None,
) -> dict:
    parent_type = parent if isinstance(parent, SubjectType) else resolve_subject_type(db, parent)
    if parent_type is None:
        raise ValueError(f"Unknown parent subject type '{parent}'")
    statement = (
        select(SubjectType)
        .join(TypeRelationship, TypeRelationship.source_type_id == SubjectType.id)
        .where(
            TypeRelationship.target_type_id == parent_type.id,
            TypeRelationship.relationship == "belongs_to",
            TypeRelationship.status == "active",
        )
        .order_by(func.lower(SubjectType.canonical_name), SubjectType.id)
    )
    result = _page(
        db,
        statement,
        operation="children",
        parent_id=parent_type.id,
        limit=limit,
        cursor=cursor,
    )
    result["parent"] = _type_summaries(db, [parent_type])[0]
    return result


def _active_parents(db: Session, subject_type_id: uuid.UUID) -> list[SubjectType]:
    return list(db.scalars(
        select(SubjectType)
        .join(TypeRelationship, TypeRelationship.target_type_id == SubjectType.id)
        .where(
            TypeRelationship.source_type_id == subject_type_id,
            TypeRelationship.relationship == "belongs_to",
            TypeRelationship.status == "active",
        )
        .order_by(func.lower(SubjectType.canonical_name), SubjectType.id)
    ).all())


def get_subject_type_paths(
    db: Session,
    subject_type: SubjectType | str,
    *,
    max_depth: int = 32,
    max_paths: int = 20,
) -> dict:
    resolved = subject_type if isinstance(subject_type, SubjectType) else resolve_subject_type(db, subject_type)
    if resolved is None:
        raise ValueError(f"Unknown subject type '{subject_type}'")
    depth_limit = max(1, min(int(max_depth), 64))
    path_limit = max(1, min(int(max_paths), 100))
    immediate_parents = _active_parents(db, resolved.id)
    completed: list[list[SubjectType]] = []
    stack: list[tuple[SubjectType, list[SubjectType], set[uuid.UUID]]] = [
        (resolved, [resolved], {resolved.id})
    ]
    truncated = False

    while stack and len(completed) < path_limit:
        current, leaf_to_current, visited = stack.pop()
        if len(leaf_to_current) >= depth_limit:
            completed.append(list(reversed(leaf_to_current)))
            truncated = True
            continue
        parents = _active_parents(db, current.id)
        if not parents:
            completed.append(list(reversed(leaf_to_current)))
            continue
        valid_parents = [parent for parent in parents if parent.id not in visited]
        if len(valid_parents) != len(parents):
            truncated = True
        for parent in reversed(valid_parents):
            stack.append((parent, leaf_to_current + [parent], visited | {parent.id}))

    if stack:
        truncated = True
    completed.sort(key=lambda path: tuple(item.canonical_name.casefold() for item in path))
    summaries: dict[uuid.UUID, dict] = {}
    all_types = []
    for item in [resolved, *immediate_parents, *(node for path in completed for node in path)]:
        if item.id not in summaries:
            all_types.append(item)
            summaries[item.id] = {}
    serialized = _type_summaries(db, all_types)
    summaries = {item.id: body for item, body in zip(all_types, serialized)}
    return {
        "subject_type": summaries[resolved.id],
        "immediate_parents": [summaries[parent.id] for parent in immediate_parents],
        "paths": [[summaries[node.id] for node in path] for path in completed],
        "path_count": len(completed),
        "truncated": truncated,
    }
