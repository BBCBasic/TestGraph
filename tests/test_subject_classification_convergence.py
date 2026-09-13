import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.v2 import SubjectClassificationDecision, SubjectType, V2Subject
from app.schemas.v2 import SubjectEnsure
from app.services.classification import (
    affirm_classification, classification_state, propose_reclassification, reopen_classification,
)
from app.services.classification_proposals import record_classification_proposal
from app.services.semantic import resolve_subject_hierarchy
from app.services.v2 import ensure_subject, resolve_subject_type


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


def _propose(db, subject, model, target="electric car", *, source_client=None):
    return propose_reclassification(
        db, subject,
        target_subject_type=target,
        source_model=model,
        source_client=source_client or f"{model}-client",
        reason="Renault Zoe ZE40 is electrically powered",
        evidence={"make": "Renault", "model": "Zoe", "badge": "ZE40"},
        evidence_fingerprint="zoe-ze40",
    )


def _affirm(db, subject, model, *, source_client=None):
    return affirm_classification(
        db, subject,
        source_model=model,
        source_client=source_client or f"{model}-client",
        reason="The existing vehicle classification is supported by the evidence",
        evidence={"make": "Renault", "model": "Zoe"},
        evidence_fingerprint="zoe-is-vehicle",
    )


def _luggage(db):
    subject_type = SubjectType(
        canonical_name="luggage",
        normalized_name="luggage",
        status="provisional",
        created_by="seed",
    )
    db.add(subject_type)
    db.flush()
    subject = V2Subject(
        subject_type_id=subject_type.id,
        name="Osprey Sojourn 80L",
        canonical_key="osprey-sojourn-80l",
        identifiers_json={},
        attributes_json={"brand": "Osprey", "capacity": "80L"},
        provenance_json={},
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)
    db.refresh(subject_type)
    return subject, subject_type


def _record_creation_proposal(subject, *, source_client="creator-oauth-client", source_model="creator-model"):
    return record_classification_proposal(
        subject,
        source_client=source_client,
        source_model=source_model,
    )


def test_subject_creation_records_proposal_without_formal_decision():
    with _new_session() as db:
        resolve_subject_hierarchy(db, ["transportation", "vehicle"], created_by="seed")
        subject = ensure_subject(
            db,
            SubjectEnsure(
                subject_type="vehicle",
                name="Renault Zoe WO68 LCJ",
                canonical_key="vehicle-wo68-lcj",
                identifiers={"registration": "WO68 LCJ"},
                attributes={"make": "Renault", "model": "Zoe"},
                provenance={},
            ),
            "creator-oauth-client",
            source_model="gpt-5.6",
        )

        proposal = subject.provenance_json["classification_proposal"]
        assert proposal["proposed_type_id"] == str(subject.subject_type_id)
        assert proposal["source_client"] == "creator-oauth-client"
        assert proposal["source_model"] == "gpt-5.6"
        assert proposal["identity_basis"] == "authenticated_client"
        assert proposal["proposed_at"]
        assert list(db.scalars(select(SubjectClassificationDecision)).all()) == []

        state = classification_state(db, subject)
        assert state["creation_proposal"] == proposal
        assert state["active_decisions"] == []


def test_subject_creation_overwrites_caller_supplied_proposal_provenance():
    with _new_session() as db:
        resolve_subject_hierarchy(db, ["transportation", "vehicle"], created_by="seed")
        subject = ensure_subject(
            db,
            SubjectEnsure(
                subject_type="vehicle",
                name="Renault Zoe WO68 LCJ",
                canonical_key="vehicle-wo68-lcj",
                identifiers={}, attributes={},
                provenance={
                    "classification_proposal": {
                        "proposed_type_id": "forged-type",
                        "source_client": "forged-client",
                        "source_model": "forged-model",
                        "identity_basis": "forged-basis",
                        "proposed_at": "2000-01-01T00:00:00+00:00",
                    },
                },
            ),
            "authenticated-client",
            source_model="reported-model",
        )

        proposal = subject.provenance_json["classification_proposal"]
        assert proposal["proposed_type_id"] == str(subject.subject_type_id)
        assert proposal["source_client"] == "authenticated-client"
        assert proposal["source_model"] == "reported-model"
        assert proposal["identity_basis"] == "authenticated_client"
        assert proposal["proposed_at"] != "2000-01-01T00:00:00+00:00"


def test_existing_subject_proposal_ignores_later_provenance_merges():
    with _new_session() as db:
        resolve_subject_hierarchy(db, ["transportation", "vehicle"], created_by="seed")
        original = ensure_subject(
            db,
            SubjectEnsure(
                subject_type="vehicle", name="Renault Zoe WO68 LCJ",
                canonical_key="vehicle-wo68-lcj", identifiers={}, attributes={}, provenance={},
            ),
            "authenticated-client",
        )
        original_proposal = dict(original.provenance_json["classification_proposal"])

        updated = ensure_subject(
            db,
            SubjectEnsure(
                subject_type="vehicle", name="Renault Zoe WO68 LCJ",
                canonical_key="vehicle-wo68-lcj", identifiers={}, attributes={},
                provenance={
                    "classification_proposal": {
                        "source_client": "forged-client",
                        "source_model": "forged-model",
                        "forged_field": "forged-value",
                    },
                },
            ),
            "another-authenticated-client",
            source_model="another-model",
        )

        assert updated.id == original.id
        assert updated.provenance_json["classification_proposal"] == original_proposal


def test_first_independent_agreement_confirms_creation_proposal():
    with _new_session() as db:
        subject, subject_type = _luggage(db)
        _record_creation_proposal(subject)

        state = _affirm(
            db,
            subject,
            "reviewer-model",
            source_client="reviewer-oauth-client",
        )

        db.refresh(subject_type)
        assert state["status"] == "confirmed"
        assert state["subject_type"] == "luggage"
        assert state["locked_at"] is not None
        assert subject_type.status == "confirmed"
        assert len(state["active_decisions"]) == 1
        assert state["active_decisions"][0]["source_client"] == "reviewer-oauth-client"
        assert state["active_decisions"][0]["outcome"] == "confirmed"


def test_first_independent_disagreement_disputes_creation_proposal():
    with _new_session() as db:
        subject = _zoe(db)
        _record_creation_proposal(subject)

        state = _propose(
            db,
            subject,
            "reviewer-model",
            target="electric car",
            source_client="reviewer-oauth-client",
        )

        assert state["status"] == "disputed"
        assert state["subject_type"] == "vehicle"
        assert state["locked_at"] is None
        assert len(state["active_decisions"]) == 1
        assert state["active_decisions"][0]["outcome"] == "candidate"


def test_later_agreement_does_not_bypass_dispute_resolution():
    with _new_session() as db:
        subject = _zoe(db)
        _record_creation_proposal(subject)
        _propose(
            db,
            subject,
            "disagreeing-reviewer",
            target="electric car",
            source_client="reviewer-oauth-client",
        )

        state = affirm_classification(
            db,
            subject,
            source_model="agreeing-reviewer",
            source_client="another-reviewer-oauth-client",
            reason="The original vehicle proposal remains supportable",
            evidence={
                "direct_child_assessment": {
                    "car": {"applicable": False, "reason": "Evidence is insufficient for the narrower type"},
                },
            },
        )

        assert state["status"] == "disputed"
        assert state["locked_at"] is None
        assert {decision["outcome"] for decision in state["active_decisions"]} == {"candidate"}


def test_creator_cannot_self_confirm_by_changing_model_label():
    with _new_session() as db:
        subject, subject_type = _luggage(db)
        _record_creation_proposal(subject, source_model="creator-model-a")

        _affirm(db, subject, "creator-model-b", source_client="creator-oauth-client")
        state = _affirm(db, subject, "creator-model-c", source_client="creator-oauth-client")

        db.refresh(subject_type)
        assert state["status"] == "candidate"
        assert state["locked_at"] is None
        assert subject_type.status == "candidate"
        assert len(state["active_decisions"]) == 1
        assert state["active_decisions"][0]["source_model"] == "creator-model-b"
        assert {decision["outcome"] for decision in state["active_decisions"]} == {"candidate"}


def test_distinct_authenticated_ids_are_not_collapsed_by_suffix_shape():
    with _new_session() as db:
        subject, subject_type = _luggage(db)
        _record_creation_proposal(subject, source_client="creator-oauth-client")

        state = _affirm(
            db,
            subject,
            "reviewer-label",
            source_client="creator-oauth-client:v3",
        )

        db.refresh(subject_type)
        assert state["status"] == "confirmed"
        assert state["locked_at"] is not None
        assert subject_type.status == "confirmed"
        assert state["creation_proposal"]["source_client"] == "creator-oauth-client"
        assert state["active_decisions"][0]["source_client"] == "creator-oauth-client:v3"


def test_authenticated_creation_proposal_suffix_is_preserved_exactly():
    with _new_session() as db:
        subject, _ = _luggage(db)
        _record_creation_proposal(subject, source_client="creator-oauth-client:v3")

        state = _affirm(
            db,
            subject,
            "creator-review-label",
            source_client="creator-oauth-client",
        )

        assert state["status"] == "confirmed"
        assert state["locked_at"] is not None


def test_different_clients_can_use_same_reported_model_label():
    with _new_session() as db:
        subject, _ = _luggage(db)
        _record_creation_proposal(subject, source_model="creator-model")
        _affirm(db, subject, "shared-model-label", source_client="creator-oauth-client")

        state = _affirm(
            db,
            subject,
            "shared-model-label",
            source_client="reviewer-oauth-client",
        )

        assert state["status"] == "confirmed"
        assert len(state["active_decisions"]) == 2
        assert {decision["source_client"] for decision in state["active_decisions"]} == {
            "creator-oauth-client",
            "reviewer-oauth-client",
        }


def test_one_client_cannot_change_decision_by_changing_model_label():
    with _new_session() as db:
        subject = _zoe(db)
        _record_creation_proposal(subject)
        _propose(
            db,
            subject,
            "creator-model-b",
            target="car",
            source_client="creator-oauth-client",
        )

        with pytest.raises(ValueError, match="OAuth client already made a different decision"):
            _propose(
                db,
                subject,
                "creator-model-c",
                target="electric car",
                source_client="creator-oauth-client",
            )


def test_replay_reconciles_existing_independent_candidate_after_lifecycle_upgrade():
    with _new_session() as db:
        subject, subject_type = _luggage(db)
        _record_creation_proposal(subject)
        existing = SubjectClassificationDecision(
            subject_id=subject.id,
            classification_version=subject.classification_version,
            from_type_id=subject.subject_type_id,
            target_type_id=subject.subject_type_id,
            source_model="reviewer-model",
            source_client="reviewer-oauth-client",
            reason="Existing type is supported",
            evidence_json={},
            outcome="candidate",
        )
        db.add(existing)
        subject.classification_status = "candidate"
        db.commit()

        state = _affirm(
            db,
            subject,
            "reviewer-model",
            source_client="reviewer-oauth-client",
        )

        db.refresh(subject_type)
        assert state["status"] == "confirmed"
        assert state["locked_at"] is not None
        assert subject_type.status == "confirmed"
        assert len(state["active_decisions"]) == 1
        assert state["active_decisions"][0]["outcome"] == "confirmed"


def test_type_status_follows_two_model_affirmation_convergence():
    with _new_session() as db:
        subject, subject_type = _luggage(db)

        first = _affirm(db, subject, "gpt-5")
        db.refresh(subject_type)
        assert first["status"] == "candidate"
        assert subject_type.status == "candidate"

        second = _affirm(db, subject, "claude-sonnet")
        db.refresh(subject_type)
        assert second["status"] == "confirmed"
        assert subject_type.status == "confirmed"


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


def test_reopened_round_records_new_proposer_and_requires_another_client():
    with _new_session() as db:
        subject, _ = _luggage(db)
        _record_creation_proposal(subject)
        _affirm(
            db,
            subject,
            "first-reviewer-model",
            source_client="first-reviewer-oauth-client",
        )

        reopened = reopen_classification(
            db,
            subject,
            trigger="contradictory_evidence",
            reason="A new classification round is required",
            evidence={"new_evidence": True},
            requested_by="reopening-oauth-client",
        )

        assert reopened["version"] == 2
        assert reopened["creation_proposal"]["proposed_type_id"] == str(subject.subject_type_id)
        assert reopened["creation_proposal"]["source_client"] == "reopening-oauth-client"
        assert reopened["creation_proposal"]["source_model"] is None
        assert reopened["creation_proposal"]["identity_basis"] == "reopening_action"

        self_review = _affirm(
            db,
            subject,
            "reopening-review-model",
            source_client="reopening-oauth-client",
        )
        assert self_review["status"] == "candidate"
        assert self_review["locked_at"] is None

        independent_review = _affirm(
            db,
            subject,
            "second-reviewer-model",
            source_client="second-reviewer-oauth-client",
        )
        assert independent_review["status"] == "confirmed"
        assert independent_review["locked_at"] is not None
