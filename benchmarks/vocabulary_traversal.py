from __future__ import annotations

import json
import math
import uuid
from time import perf_counter

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from app.api.mcp_v2 import _search
from app.core.security import Principal
from app.db.base import Base
from app.models.v2 import SubjectType, TypeRelationship, V2Experience, V2Subject, now_utc
from app.services.v2 import resolve_subject_type, vocabulary_index
from app.services.vocabulary_navigation import (
    list_child_subject_types,
    list_root_subject_types,
)


BENCHMARK_NAMESPACE = uuid.UUID("7e865e38-c723-4f09-b25a-5ea784bf8a81")


def _type_id(index: int) -> uuid.UUID:
    return uuid.uuid5(BENCHMARK_NAMESPACE, f"type-{index}")


def _type_name(index: int) -> str:
    return f"benchmark type {index:06d}"


def _payload_bytes(payload: dict) -> int:
    return len(json.dumps(payload, default=str, separators=(",", ":")).encode())


def _parent_indices(node_count: int, branching_factor: int) -> list[int | None]:
    parents: list[int | None] = [None] * branching_factor
    parents.extend(
        (index - branching_factor) // branching_factor
        for index in range(branching_factor, node_count)
    )
    return parents


def _path_indices(target_index: int, parents: list[int | None]) -> list[int]:
    path = []
    current: int | None = target_index
    while current is not None:
        path.append(current)
        current = parents[current]
    return list(reversed(path))


def _seed_taxonomy(db: Session, *, node_count: int, branching_factor: int) -> list[int | None]:
    parents = _parent_indices(node_count, branching_factor)
    timestamp = now_utc()
    db.execute(insert(SubjectType), [
        {
            "id": _type_id(index),
            "canonical_name": _type_name(index),
            "normalized_name": _type_name(index),
            "description": None,
            "status": "confirmed",
            "public_location_eligible": False,
            "created_by": "vocabulary-benchmark",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        for index in range(node_count)
    ])
    db.execute(insert(TypeRelationship), [
        {
            "id": uuid.uuid5(BENCHMARK_NAMESPACE, f"edge-{index}"),
            "source_type_id": _type_id(index),
            "relationship": "belongs_to",
            "target_type_id": _type_id(parent),
            "source": "vocabulary-benchmark",
            "status": "active",
            "created_at": timestamp,
        }
        for index, parent in enumerate(parents)
        if parent is not None
    ])
    db.commit()
    return parents


def _seed_target_review(db: Session, *, target_index: int, owner_id: uuid.UUID) -> uuid.UUID:
    subject = V2Subject(
        subject_type_id=_type_id(target_index),
        owner_id=owner_id,
        name="Benchmark target",
        canonical_key="benchmark-target",
        identifiers_json={},
        attributes_json={},
        provenance_json={},
        classification_status="confirmed",
        classification_locked_at=now_utc(),
    )
    db.add(subject)
    db.flush()
    db.add(V2Experience(
        owner_id=owner_id,
        subject_id=subject.id,
        headline="Benchmark target review",
        summary="Known review stored at a deep taxonomy leaf.",
        raw_text="Synthetic deterministic benchmark evidence.",
        structured_data={},
        submitted_data={},
        normalization_log=[],
        visibility="private",
        publication_status="published",
        provenance={},
        created_by_client="vocabulary-benchmark",
    ))
    db.commit()
    return subject.id


def _search_payload(db: Session, principal: Principal, subject_type: str) -> dict:
    return _search(db, principal, {
        "query": "Benchmark target",
        "subject_type": subject_type,
        "include_related": True,
        "limit": 10,
    })["structuredContent"]


def _full_vocabulary_run(
    db: Session, principal: Principal, *, target_name: str, target_subject_id: uuid.UUID,
) -> dict:
    started = perf_counter()
    vocabulary = vocabulary_index(db)
    resolved = next(
        (item for item in vocabulary["subject_types"] if item["canonical_name"] == target_name),
        None,
    )
    search = _search_payload(db, principal, target_name)
    elapsed_ms = (perf_counter() - started) * 1000
    payload_bytes = _payload_bytes(vocabulary) + _payload_bytes(search)
    return {
        "classification_success": resolved is not None,
        "retrieval_success": any(item["subject_id"] == str(target_subject_id) for item in search["results"]),
        "taxonomy_nodes_returned": len(vocabulary["subject_types"]),
        "percentage_taxonomy_returned": 100.0,
        "tool_calls": 2,
        "payload_bytes": payload_bytes,
        "approximate_context_tokens": math.ceil(payload_bytes / 4),
        "latency_ms": round(elapsed_ms, 3),
        "backtracks": 0,
    }


def _progressive_run(
    db: Session,
    principal: Principal,
    *,
    node_count: int,
    target_path: list[int],
    target_subject_id: uuid.UUID,
) -> dict:
    started = perf_counter()
    payloads = []
    taxonomy_nodes_returned = 0
    tool_calls = 0

    roots = list_root_subject_types(db, limit=100)
    payloads.append(roots)
    taxonomy_nodes_returned += len(roots["items"])
    tool_calls += 1
    roots_by_name = {item["canonical_name"]: item for item in roots["items"]}
    target_root_name = _type_name(target_path[0])
    wrong_root = next(
        item for item in reversed(roots["items"])
        if item["canonical_name"] != target_root_name
    )

    wrong_current = wrong_root
    wrong_branch_descents = 0
    while wrong_current["child_count"]:
        wrong_children = list_child_subject_types(
            db, wrong_current["canonical_name"], limit=100,
        )
        payloads.append(wrong_children)
        taxonomy_nodes_returned += len(wrong_children["items"])
        tool_calls += 1
        wrong_branch_descents += 1
        wrong_current = wrong_children["items"][-1]

    wrong_search = _search_payload(db, principal, wrong_current["canonical_name"])
    payloads.append(wrong_search)
    tool_calls += 1
    if wrong_search["results"]:
        raise AssertionError("Synthetic fallback branch unexpectedly contained the target")
    backtracks = 1

    current = roots_by_name[target_root_name]
    for expected_index in target_path[1:]:
        children = list_child_subject_types(db, current["canonical_name"], limit=100)
        payloads.append(children)
        taxonomy_nodes_returned += len(children["items"])
        tool_calls += 1
        expected_name = _type_name(expected_index)
        current = next(item for item in children["items"] if item["canonical_name"] == expected_name)

    search = _search_payload(db, principal, current["canonical_name"])
    payloads.append(search)
    tool_calls += 1
    payload_bytes = sum(_payload_bytes(payload) for payload in payloads)
    elapsed_ms = (perf_counter() - started) * 1000
    return {
        "classification_success": current["canonical_name"] == _type_name(target_path[-1]),
        "retrieval_success": any(item["subject_id"] == str(target_subject_id) for item in search["results"]),
        "taxonomy_nodes_returned": taxonomy_nodes_returned,
        "percentage_taxonomy_returned": round(taxonomy_nodes_returned * 100 / node_count, 6),
        "tool_calls": tool_calls,
        "payload_bytes": payload_bytes,
        "approximate_context_tokens": math.ceil(payload_bytes / 4),
        "latency_ms": round(elapsed_ms, 3),
        "backtracks": backtracks,
        "wrong_branch_descents": wrong_branch_descents,
    }


def run_vocabulary_traversal_benchmark(
    node_count: int = 10_000, branching_factor: int = 10,
) -> dict:
    if branching_factor < 2 or branching_factor > 100:
        raise ValueError("branching_factor must be between 2 and 100")
    if node_count < branching_factor * 2:
        raise ValueError("node_count must contain at least two levels across all root branches")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        parents = _seed_taxonomy(
            db, node_count=node_count, branching_factor=branching_factor,
        )
        target_index = node_count - 1
        target_path = _path_indices(target_index, parents)
        owner_id = uuid.uuid5(BENCHMARK_NAMESPACE, "owner")
        target_subject_id = _seed_target_review(
            db, target_index=target_index, owner_id=owner_id,
        )
        principal = Principal(
            subject="vocabulary-benchmark",
            client_id="vocabulary-benchmark",
            scopes={"reviews:read"},
            user_id=owner_id,
        )
        target_name = _type_name(target_index)
        assert resolve_subject_type(db, target_name) is not None
        full = _full_vocabulary_run(
            db, principal, target_name=target_name, target_subject_id=target_subject_id,
        )
        progressive = _progressive_run(
            db,
            principal,
            node_count=node_count,
            target_path=target_path,
            target_subject_id=target_subject_id,
        )
    return {
        "configuration": {
            "node_count": node_count,
            "branching_factor": branching_factor,
            "target_depth": len(target_path),
            "token_estimate": "ceil(serialized UTF-8 payload bytes / 4)",
        },
        "total_taxonomy_nodes": node_count,
        "full_vocabulary": full,
        "progressive": progressive,
    }
