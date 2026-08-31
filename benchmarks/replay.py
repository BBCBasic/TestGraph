from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.v2 import SubjectType, TypeRelationship, V2Subject
from app.services.classification import classification_state, propose_reclassification
from app.services.v2 import normalise_term
from benchmarks.kill_test import BenchmarkCase


ROOT_TYPE = "benchmark entity"


def _ensure_type(db: Session, name: str) -> SubjectType:
    key = normalise_term(name)
    existing = db.scalar(select(SubjectType).where(SubjectType.normalized_name == key))
    if existing is not None:
        return existing
    item = SubjectType(
        canonical_name=name.strip().lower(),
        normalized_name=key,
        status="provisional",
        created_by="kill-test-replay",
    )
    db.add(item)
    db.flush()
    return item


def _ensure_child(db: Session, child: SubjectType, parent: SubjectType) -> None:
    existing = db.scalar(select(TypeRelationship).where(
        TypeRelationship.source_type_id == child.id,
        TypeRelationship.relationship == "belongs_to",
        TypeRelationship.target_type_id == parent.id,
    ))
    if existing is None:
        db.add(TypeRelationship(
            source_type_id=child.id,
            relationship="belongs_to",
            target_type_id=parent.id,
            source="kill-test-replay",
            status="active",
        ))
        db.flush()


def _materialise_case(db: Session, case: BenchmarkCase, collected: dict) -> V2Subject:
    root = _ensure_type(db, ROOT_TYPE)
    for candidate_name in {str(collected["first_type"]), str(collected["second_type"])}:
        candidate = _ensure_type(db, candidate_name)
        _ensure_child(db, candidate, root)
    subject = V2Subject(
        subject_type_id=root.id,
        name=f"[kill-test:{case.id}] {case.observation}",
        canonical_key=f"kill-test:{case.id}",
        identifiers_json={},
        attributes_json={"benchmark_case_id": case.id, "benchmark_category": case.category},
        provenance_json={"benchmark": "kill-test", "case_id": case.id},
        classification_status="provisional",
        classification_version=1,
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return subject


def replay_case(db: Session, case: BenchmarkCase, collected: dict) -> tuple[dict, dict]:
    audit = {"case_id": case.id, "regime": "testgraph"}
    base_model_calls = int(collected.get("model_calls", 2))
    base_cost = float(collected.get("cost_usd", 0.0))
    try:
        if not collected.get("pending_replay"):
            raise ValueError("collected decision is not replayable")
        subject = _materialise_case(db, case, collected)
        first_state = propose_reclassification(
            db,
            subject,
            target_subject_type=str(collected["first_type"]),
            source_model=str(collected["first_model"]),
            source_client="kill-test-replay:first",
            reason=str(collected["first_reason"]),
            evidence={"observation": case.observation, "benchmark_case_id": case.id},
            evidence_fingerprint=f"kill-test:{case.id}:first",
        )
        audit["after_first"] = first_state
        final_state = propose_reclassification(
            db,
            subject,
            target_subject_type=str(collected["second_type"]),
            source_model=str(collected["second_model"]),
            source_client="kill-test-replay:second",
            reason=str(collected["second_reason"]),
            evidence={"observation": case.observation, "benchmark_case_id": case.id},
            evidence_fingerprint=f"kill-test:{case.id}:second",
        )
        audit["final_state"] = final_state
        resolver_calls = sum(
            1 for decision in final_state.get("active_decisions", [])
            if str(decision.get("source_model", "")).startswith("tg-ai:")
        )
        confirmed = final_state.get("status") == "confirmed"
        consensus = normalise_term(str(collected["first_type"])) == normalise_term(str(collected["second_type"]))
        return ({
            "case_id": case.id,
            "regime": "testgraph",
            "type": final_state.get("subject_type"),
            "canonical": confirmed,
            "consensus": consensus,
            "operational_failure": not confirmed,
            "model_calls": base_model_calls + resolver_calls,
            "resolver_calls": resolver_calls,
            "cost_usd": base_cost,
        }, audit)
    except Exception as exc:
        audit["error"] = f"{type(exc).__name__}: {exc}"
        try:
            if "subject" in locals():
                audit["final_state"] = classification_state(db, subject)
                final_type = audit["final_state"].get("subject_type")
            else:
                final_type = None
        except Exception:
            final_type = None
        return ({
            "case_id": case.id,
            "regime": "testgraph",
            "type": final_type,
            "canonical": False,
            "consensus": False,
            "operational_failure": True,
            "model_calls": base_model_calls,
            "resolver_calls": 0,
            "cost_usd": base_cost,
        }, audit)


def replay_records(
    cases: Iterable[BenchmarkCase],
    collected_rows: Iterable[dict],
    database_url: str,
) -> tuple[list[dict], list[dict]]:
    case_list = list(cases)
    collected_map = {str(row["case_id"]): row for row in collected_rows}
    missing = [case.id for case in case_list if case.id not in collected_map]
    if missing:
        raise ValueError("Missing collected decision for benchmark case(s): " + ", ".join(missing))

    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    records: list[dict] = []
    audits: list[dict] = []
    with Session(engine) as db:
        for case in case_list:
            record, audit = replay_case(db, case, collected_map[case.id])
            records.append(record)
            audits.append(audit)
    engine.dispose()
    return records, audits
