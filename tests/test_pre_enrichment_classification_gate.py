import json
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.mcp_v2 import (
    _assert_location,
    _correct_subject_fact,
    _enrich_subject,
    _save_experience,
)
from app.core.security import Principal
from app.db.base import Base
from app.models.entities import IdempotencyRecord
from app.models.v2 import LocationAssertion, SubjectRelationship, V2Experience, V2Subject, now_utc
from app.schemas.v2 import ExperienceCreate, SubjectEnsure
from app.services.classification import (
    affirm_classification,
    apply_resolver_arbitration,
    propose_reclassification,
)
from app.services.semantic import resolve_subject_hierarchy
from app.services.tg_ai_resolver import ResolverDecision
from app.services.v2 import create_experience, ensure_subject, ensure_subject_type, resolve_subject_type


SOURCE = "https://example.test/renault-zoe"


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def principal():
    return Principal(
        subject="reviewer-user",
        client_id="reviewer-oauth-client",
        scopes={"reviews:write"},
        user_id=uuid.uuid4(),
    )


def _body(result):
    return json.loads(result["content"][0]["text"])


def _subject(db, principal, *, confirmed=False, hierarchy=("car",), owned=False):
    if len(hierarchy) == 1:
        ensure_subject_type(db, hierarchy[0], created_by="test")
    else:
        resolve_subject_hierarchy(db, list(hierarchy), created_by="test")
    subject = ensure_subject(
        db,
        SubjectEnsure(
            subject_type=hierarchy[0],
            name="Renault Zoe",
            canonical_key="renault-zoe",
            identifiers={"registration": "WO68 LCJ"},
            attributes={"colour": "silver/grey"},
            provenance={},
        ),
        client_id="creator:v3",
        owner_id=principal.user_id if owned else uuid.uuid4(),
        source_model="creator-model",
        classification_source_client="creator-oauth-client",
    )
    if confirmed:
        subject.classification_status = "confirmed"
        subject.classification_locked_at = now_utc()
        resolve_subject_type(db, hierarchy[0]).status = "confirmed"
        db.commit()
        db.refresh(subject)
    return subject


def _enrichment_args(subject, *, key="zoe-colour-enrichment"):
    return {
        "subject_id": str(subject.id),
        "attributes": {"battery_capacity_kwh": 41},
        "identifiers": {},
        "provenance": {},
        "subject_context": {"subjects": [], "relationships": []},
        "subject_enrichment_check": {
            "status": "completed",
            "sources": [SOURCE],
            "applied_fields": {SOURCE: ["attributes.battery_capacity_kwh"]},
            "retrieval_uses": {
                "attributes.battery_capacity_kwh": {
                    "roles": ["comparison"],
                    "likely_queries": ["Renault Zoe battery capacity"],
                    "reason": "Supports comparison between vehicle variants.",
                },
            },
        },
        "collection_assessment": {
            "status": "independent",
            "evidence_sources": [SOURCE],
        },
        "source_model": "reviewer-model",
        "idempotency_key": key,
    }


def _save_args(*, key="zoe-experience-save"):
    return {
        "subject_type": "car",
        "subject_name": "Renault Zoe",
        "canonical_key": "renault-zoe",
        "identifiers": {"registration": "WO68 LCJ"},
        "subject_attributes": {"colour": "silver/grey"},
        "subject_provenance": {},
        "subject_enrichment_check": {
            "status": "not_applicable",
            "reason": "Controlled workflow-ordering regression fixture.",
        },
        "collection_assessment": {
            "status": "independent",
            "attempts": ["The fixture represents one independently identified vehicle."],
        },
        "headline": "Reliable electric car",
        "summary": "A test experience used to verify workflow ordering.",
        "raw_text": "The Renault Zoe has been reliable.",
        "structured_data": {},
        "subject_context": {"subjects": [], "relationships": []},
        "visibility": "private",
        "user_approved": True,
        "source_model": "reviewer-model",
        "idempotency_key": key,
    }


def _assert_classification_block(result, subject, *, code="classification_review_required"):
    payload = _body(result)
    assert result["isError"] is True
    assert payload["error_code"] == code
    assert payload["details"]["code"] == code
    assert payload["details"]["subject_id"] == str(subject.id)
    assert payload["details"]["classification_status"] == subject.classification_status
    workflow = payload["details"]["workflow"]
    assert payload["workflow"] == workflow
    assert workflow["workflow_action_required"] is True
    assert workflow["workflow_run_id"]
    return workflow


def _affirm(db, subject, principal):
    return affirm_classification(
        db,
        subject,
        source_model="reviewer-model",
        source_client=principal.client_id,
        reason="The existing type is supported.",
        evidence={},
    )


def test_existing_provisional_subject_blocks_enrichment_before_any_mutation(db, principal):
    subject = _subject(db, principal)

    result = _enrich_subject(db, principal, _enrichment_args(subject))

    workflow = _assert_classification_block(result, subject)
    assert workflow["next_action"] == "get_subject_classification"
    db.refresh(subject)
    assert subject.attributes_json == {"colour": "silver/grey"}
    assert subject.provenance_json.get("enrichment_checks") is None
    assert db.scalar(select(func.count()).select_from(SubjectRelationship)) == 0
    assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


def test_independent_agreement_then_same_key_retry_enriches_once(db, principal):
    subject = _subject(db, principal)
    args = _enrichment_args(subject)
    _assert_classification_block(_enrich_subject(db, principal, args), subject)

    state = _affirm(db, subject, principal)
    assert state["status"] == "confirmed"
    assert state["locked_at"] is not None

    first = _body(_enrich_subject(db, principal, args))
    replay = _body(_enrich_subject(db, principal, args))

    assert first["changed"] is True
    assert first["attributes"]["battery_capacity_kwh"] == 41
    assert first["workflow"]["completed"] is True
    assert replay == first
    assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


def test_different_type_dispute_blocks_enrichment_until_resolved(db, principal):
    subject = _subject(db, principal, hierarchy=("vehicle", "car"))
    args = _enrichment_args(subject, key="zoe-disputed-enrichment")
    _assert_classification_block(_enrich_subject(db, principal, args), subject)

    disputed = propose_reclassification(
        db,
        subject,
        target_subject_type="car",
        source_model="reviewer-model",
        source_client=principal.client_id,
        reason="The evidence supports the stricter car type.",
        evidence={"vehicle_kind": "passenger car"},
    )
    assert disputed["status"] == "disputed"

    workflow = _assert_classification_block(
        _enrich_subject(db, principal, args),
        subject,
        code="classification_resolution_required",
    )
    assert workflow["state"] == "disputed"
    db.refresh(subject)
    assert subject.attributes_json == {"colour": "silver/grey"}

    resolved = apply_resolver_arbitration(
        db,
        subject,
        resolver_decision=ResolverDecision(
            target_subject_type="car",
            confidence=0.9,
            reason="The stricter candidate is supported.",
            action="select_candidate",
        ),
    )
    assert resolved["status"] == "confirmed"

    completed = _body(_enrich_subject(db, principal, args))
    assert completed["changed"] is True
    assert completed["attributes"]["battery_capacity_kwh"] == 41
    assert completed["workflow"]["completed"] is True


def test_confirmed_existing_subject_enriches_without_review(db, principal):
    subject = _subject(db, principal, confirmed=True)

    result = _enrich_subject(db, principal, _enrichment_args(subject))
    payload = _body(result)

    assert "isError" not in result
    assert payload["changed"] is True
    assert payload["workflow"]["completed"] is True


def test_new_subject_save_can_include_classification_evidence(db, principal):
    ensure_subject_type(db, "car", created_by="test")

    result = _save_experience(db, principal, _save_args(key="new-zoe-experience"))
    payload = _body(result)

    assert "isError" not in result
    assert payload["saved"] is True
    subject = db.get(V2Subject, uuid.UUID(payload["subject_id"]))
    assert subject.attributes_json["colour"] == "silver/grey"
    assert subject.classification_status == "provisional"
    assert payload["workflow"]["workflow_action_required"] is True


def test_colour_correction_waits_for_classification_then_retries_once(db, principal):
    subject = _subject(db, principal)
    args = {
        "subject_id": str(subject.id),
        "field_root": "attributes",
        "field_path": "colour",
        "expected_value": "silver/grey",
        "corrected_value": "red",
        "evidence_sources": [SOURCE],
        "reason": "The owner reported the repaint.",
        "idempotency_key": "zoe-colour-correction",
    }

    _assert_classification_block(_correct_subject_fact(db, principal, args), subject)
    db.refresh(subject)
    assert subject.attributes_json["colour"] == "silver/grey"

    _affirm(db, subject, principal)
    first = _body(_correct_subject_fact(db, principal, args))
    replay = _body(_correct_subject_fact(db, principal, args))

    db.refresh(subject)
    assert subject.attributes_json["colour"] == "red"
    assert len(subject.provenance_json["subject_corrections"]) == 1
    assert first["workflow"]["completed"] is True
    assert replay == first


def test_existing_subject_save_blocks_before_experience_or_idempotency_write(db, principal):
    subject = _subject(db, principal)

    result = _save_experience(db, principal, _save_args())

    _assert_classification_block(result, subject)
    assert db.scalar(select(func.count()).select_from(V2Experience)) == 0
    assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


def test_existing_subject_location_assertion_blocks_before_assertion_write(db, principal):
    subject = _subject(db, principal)
    resolve_subject_type(db, "car").public_location_eligible = True
    db.commit()

    result = _assert_location(
        db,
        principal,
        {
            "subject_id": str(subject.id),
            "predicate": "postcode",
            "value": "GL5 4AQ",
            "source": {"reference": SOURCE, "kind": "authoritative"},
            "idempotency_key": "zoe-location-assertion",
        },
    )

    _assert_classification_block(result, subject)
    assert db.scalar(select(func.count()).select_from(LocationAssertion)) == 0
    assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0


def test_location_preflight_preserves_shared_subject_write_access(db, principal):
    subject = _subject(db, principal)
    subject.owner_id = uuid.uuid4()
    resolve_subject_type(db, "car").public_location_eligible = True
    db.commit()

    result = _assert_location(
        db,
        principal,
        {
            "subject_id": str(subject.id),
            "predicate": "postcode",
            "value": "GL5 4AQ",
            "source": {"reference": SOURCE, "kind": "authoritative"},
            "idempotency_key": "shared-location-assertion",
        },
    )

    _assert_classification_block(result, subject)


@pytest.mark.parametrize("status", ["provisional", "candidate", "disputed"])
@pytest.mark.parametrize("client_id", ["creator-oauth-client", "another-oauth-client"])
def test_owner_enriches_without_independent_classification(db, principal, status, client_id):
    subject = _subject(db, principal, owned=True)
    subject.classification_status = status
    db.commit()
    principal.client_id = client_id
    original_proposal = dict(subject.provenance_json["classification_proposal"])
    args = _enrichment_args(subject)

    result = _enrich_subject(db, principal, args)

    assert not result.get("isError"), _body(result)
    payload = _body(result)
    db.refresh(subject)
    assert subject.attributes_json["battery_capacity_kwh"] == 41
    assert subject.classification_status == status
    assert subject.classification_locked_at is None
    assert subject.provenance_json["classification_proposal"] == original_proposal
    assert payload["workflow"]["completed"] is False
    assert payload["workflow"]["workflow_action_required"] is True
    assert _body(_enrich_subject(db, principal, args)) == payload
    assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


def test_new_review_creator_can_enrich_and_self_review_still_cannot_confirm(db, principal):
    ensure_subject_type(db, "car", created_by="test")
    saved = _body(_save_experience(db, principal, _save_args()))
    subject = db.get(V2Subject, uuid.UUID(saved["subject_id"]))

    enriched = _enrich_subject(db, principal, _enrichment_args(subject))

    assert not enriched.get("isError"), _body(enriched)
    state = _affirm(db, subject, principal)
    assert state["status"] == "candidate"
    assert state["locked_at"] is None
    principal.client_id = "independent-review-client"
    assert _affirm(db, subject, principal)["status"] == "confirmed"


@pytest.mark.parametrize("deleted", [False, True])
def test_review_ownership_allows_enrichment_only_while_review_exists(db, principal, deleted):
    subject = _subject(db, principal)
    review = create_experience(db, ExperienceCreate(
        owner_id=principal.user_id, subject_id=subject.id,
        headline="My review", summary="My review", raw_text="My review",
        visibility="private", user_approved=True,
    ), principal.client_id)
    if deleted:
        review.deleted_at = now_utc()
        db.commit()

    result = _enrich_subject(db, principal, _enrichment_args(subject))

    if deleted:
        _assert_classification_block(result, subject)
        assert "battery_capacity_kwh" not in subject.attributes_json
    else:
        assert not result.get("isError"), _body(result)
        db.refresh(subject)
        assert subject.attributes_json["battery_capacity_kwh"] == 41
        assert subject.classification_status == "provisional"


def test_ownerless_subject_does_not_grant_anonymous_enrichment(db, principal):
    subject = _subject(db, principal)
    subject.owner_id = None
    principal.user_id = None
    db.commit()

    _assert_classification_block(_enrich_subject(db, principal, _enrichment_args(subject)), subject)


@pytest.mark.parametrize("operation", ["correction", "save", "location"])
def test_owner_can_complete_review_updates_while_classification_pending(db, principal, operation):
    subject = _subject(db, principal, owned=True)
    if operation == "correction":
        result = _correct_subject_fact(db, principal, {
            "subject_id": str(subject.id), "field_root": "attributes", "field_path": "colour",
            "expected_value": "silver/grey", "corrected_value": "red",
            "evidence_sources": [SOURCE], "reason": "The owner reported the repaint.",
            "idempotency_key": "owner-colour-correction",
        })
    elif operation == "save":
        result = _save_experience(db, principal, _save_args())
    else:
        resolve_subject_type(db, "car").public_location_eligible = True
        db.commit()
        result = _assert_location(db, principal, {
            "subject_id": str(subject.id), "predicate": "postcode", "value": "GL5 4AQ",
            "source": {"reference": SOURCE, "kind": "authoritative"},
            "idempotency_key": "owner-location-assertion",
        })

    assert not result.get("isError"), _body(result)
    db.refresh(subject)
    assert subject.classification_status == "provisional"
    assert subject.classification_locked_at is None
    if operation == "correction":
        assert subject.attributes_json["colour"] == "red"
    elif operation == "save":
        assert db.scalar(select(func.count()).select_from(V2Experience)) == 1
    else:
        assert db.scalar(select(func.count()).select_from(LocationAssertion)) == 1
