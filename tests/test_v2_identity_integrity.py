import json
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.mcp_v2 import _save_experience
from app.core.security import Principal
from app.db.base import Base
from app.models.v2 import V2Experience, V2Subject
from app.services.deliberation import DeliberationError
from app.services.v2 import (
    add_type_relationship,
    delete_owned_experience,
    ensure_subject_type,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def principal():
    return Principal(
        subject="test-user",
        client_id="identity-test-client",
        scopes={"reviews:write"},
        user_id=uuid.uuid4(),
    )


def _save_args(subject_type, canonical_key, *, name="Stress EV Alpha", identifier="STRESS-EV-ALPHA-20260827", suffix="a"):
    return {
        "subject_type": subject_type,
        "subject_name": name,
        "canonical_key": canonical_key,
        "identifiers": {"stress_test_id": identifier},
        "subject_attributes": {},
        "subject_provenance": {},
        "subject_enrichment_check": {
            "status": "not_applicable",
            "reason": "Synthetic identity-integrity regression fixture.",
        },
        "collection_assessment": {
            "status": "independent",
            "attempts": ["Synthetic fixture has no wider collection."],
        },
        "headline": "Synthetic identity regression",
        "summary": "Synthetic review used only to validate V2 identity handling.",
        "raw_text": "Synthetic identity-integrity test record.",
        "structured_data": {},
        "subject_context": {"subjects": [], "relationships": []},
        "visibility": "private",
        "user_approved": True,
        "idempotency_key": f"identity-integrity-{suffix}",
    }


def _body(result):
    return json.loads(result["content"][0]["text"])


def test_related_type_with_same_identity_requires_reclassification(db, principal):
    vehicle = ensure_subject_type(db, "vehicle", created_by="test")[0]
    car = ensure_subject_type(db, "car", created_by="test")[0]
    add_type_relationship(db, car, "belongs_to", vehicle, source="test")

    first = _body(_save_experience(
        db, principal,
        _save_args("vehicle", "v2-stress-ev-alpha-20260827", suffix="vehicle"),
    ))
    original_subject_id = first["subject_id"]

    with pytest.raises(DeliberationError) as exc_info:
        _save_experience(
            db, principal,
            _save_args("car", "v2-stress-ev-alpha-20260827", suffix="car"),
        )
    db.rollback()

    assert exc_info.value.code == "RECLASSIFICATION_REQUIRED"
    assert exc_info.value.details["existing_subject_id"] == original_subject_id
    assert exc_info.value.details["existing_subject_type"] == "vehicle"
    assert exc_info.value.details["requested_subject_type"] == "car"
    assert db.scalar(select(func.count()).select_from(V2Subject)) == 1
    assert db.scalar(select(func.count()).select_from(V2Experience)) == 1


def test_new_review_subject_is_owned_and_deleted_when_orphaned(db, principal):
    ensure_subject_type(db, "vehicle", created_by="test")

    saved = _body(_save_experience(
        db, principal,
        _save_args("vehicle", "v2-stress-orphan-20260827", identifier="STRESS-ORPHAN-20260827", suffix="orphan"),
    ))
    subject = db.get(V2Subject, uuid.UUID(saved["subject_id"]))
    assert subject.owner_id == principal.user_id

    result = delete_owned_experience(
        db,
        uuid.UUID(saved["experience_id"]),
        principal.user_id,
        delete_orphan_subject=True,
    )

    assert result["subject_deleted"] is True
    assert db.get(V2Subject, uuid.UUID(saved["subject_id"])) is None


def test_same_name_different_identity_warns_without_merging(db, principal):
    ensure_subject_type(db, "vehicle", created_by="test")

    first = _body(_save_experience(
        db, principal,
        _save_args("vehicle", "same-name-one", name="Same Name", identifier="ONE", suffix="same-name-one"),
    ))
    second = _body(_save_experience(
        db, principal,
        _save_args("vehicle", "same-name-two", name="Same Name", identifier="TWO", suffix="same-name-two"),
    ))

    assert first["subject_id"] != second["subject_id"]
    assert any(
        item.get("code") == "SAME_NAME_TYPE_COLLISION"
        for item in second["normalization_log"]
    )
    assert db.scalar(select(func.count()).select_from(V2Subject)) == 2
