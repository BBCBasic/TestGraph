import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.v2 import SubjectClassificationDecision, V2Subject
from app.services.classification import affirm_classification, propose_reclassification, reopen_classification
from app.services.semantic import resolve_subject_hierarchy
from app.services.v2 import resolve_subject_type


def _new_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _zoe(db):
    resolve_subject_hierarchy(db, ["transportation", "vehicle", "car", "electric car"], created_by="seed")
    vehicle = resolve_subject_type(db, "vehicle")
    subject = V2Subject(
        subject_type_id=vehicle.id,
        name="Renault Zoe WO68 LCJ",
        canonical_key="vehicle-wo68-lcj",
        identifiers_json={"registration": "WO68 LCJ"},
        attributes_json={"make": "Renault", "model": "Zoe", "badge": "ZE40"},
        provenance_json={},
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return subject


def _propose(db, subject, model, target="electric car"):
    return propose_reclassification(
        db, subject,
        target_subject_type=target,
        source_model=model,
        source_client=f"{model}-client",
        reason="Renault Zoe ZE40 is electrically powered",
        evidence={"make": "Renault", "model": "Zoe", "badge": "ZE40"},
        evidence_fingerprint="zoe-ze40",
    )


def _affirm(db, subject, model):
    return affirm_classification(
        db, subject,
        source_model=model,
        source_client=f"{model}-client",
        reason="The existing vehicle classification is supported by the evidence",
        evidence={"make": "Renault", "model": "Zoe"},
        evidence_fingerprint="zoe-is-vehicle",
    )


def test_first_ai_creates_candidate_without_moving_subject():
    with _new_session() as db:
        subject = _zoe(db)
        vehicle_id = subject.subject_type_id
        state = _propose(db, subject, "gpt-5")
        assert state["status"] == "candidate"
        assert subject.subject_type_id == vehicle_id
        assert state["active_decisions"][0]["outcome"] == "candidate"


def test_two_distinct_models_confirm_move_and_lock():
    with _new_session() as db:
        subject = _zoe(db)
        _propose(db, subject, "gpt-5")
        state = _propose(db, subject, "claude-sonnet")
        assert state["status"] == "confirmed"
        assert state["subject_type"] == "electric car"
        assert state["locked_at"] is not None
        assert {d["source_model"] for d in state["active_decisions"]} == {"gpt-5", "claude-sonnet"}
        assert {d["outcome"] for d in state["active_decisions"]} == {"confirmed"}


def test_same_model_cannot_supply_both_votes():
    with _new_session() as db:
        subject = _zoe(db)
        first = _propose(db, subject, "gpt-5")
        second = _propose(db, subject, "gpt-5")
        assert first["status"] == second["status"] == "candidate"
        decisions = list(db.scalars(select(SubjectClassificationDecision)).all())
        assert len(decisions) == 1


def test_broad_current_type_cannot_be_affirmed_without_specificity_review():
    with _new_session() as db:
        subject = _zoe(db)
        with pytest.raises(ValueError, match="direct child classification review"):
            _affirm(db, subject, "gpt-5")
        state = _propose(db, subject, "gpt-5", target="car")
        assert state["status"] == "candidate"
        assert state["subject_type"] == "vehicle"


def test_reclassification_tool_still_rejects_same_type():
    with _new_session() as db:
        subject = _zoe(db)
        with pytest.raises(ValueError, match="affirm_subject_classification"):
            _propose(db, subject, "gpt-5", target="vehicle")


def test_later_disagreement_is_audited_but_does_not_reopen_lock():
    with _new_session() as db:
        subject = _zoe(db)
        _propose(db, subject, "gpt-5")
        _propose(db, subject, "claude-sonnet")
        state = _propose(db, subject, "gemini", target="car")
        assert state["status"] == "confirmed"
        assert state["subject_type"] == "electric car"
        assert state["active_decisions"][-1]["outcome"] == "ignored_locked"


def test_conflicting_candidates_do_not_lock():
    with _new_session() as db:
        subject = _zoe(db)
        _propose(db, subject, "gpt-5", target="electric car")
        state = _propose(db, subject, "claude-sonnet", target="car")
        assert state["status"] == "disputed"
        assert state["subject_type"] == "vehicle"


def test_reopening_requires_an_allowed_trigger_and_starts_new_round():
    with _new_session() as db:
        subject = _zoe(db)
        _propose(db, subject, "gpt-5")
        _propose(db, subject, "claude-sonnet")
        with pytest.raises(ValueError, match="Contradictory evidence is required"):
            reopen_classification(
                db, subject, trigger="contradictory_evidence", reason="new fact",
                evidence={}, requested_by="test",
            )
        state = reopen_classification(
            db, subject, trigger="contradictory_evidence", reason="Battery removed permanently",
            evidence={"powertrain": "combustion conversion"}, requested_by="test",
        )
        assert state["status"] == "provisional"
        assert state["subject_type"] == "vehicle"
        assert state["version"] == 2
        assert state["active_decisions"] == []
        assert len(state["audit_history"]) == 2
