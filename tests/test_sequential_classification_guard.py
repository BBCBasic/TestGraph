import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.mcp_v2 import _affirm_subject_classification
from app.core.security import Principal
from app.db.base import Base
from app.models.v2 import SubjectType, V2Subject
from app.schemas.v2 import ExperienceCreate
from app.services.deliberation import DeliberationError
from app.services.v2 import create_experience
from app.services.workflows import start_or_resume_enrichment_workflow


CLIENT = "chatgpt:v3"
OWNER = uuid.uuid4()
PRINCIPAL = Principal(
    subject="test-user",
    client_id="chatgpt",
    scopes={"reviews:write"},
    user_id=OWNER,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _type(db, name):
    item = SubjectType(
        canonical_name=name,
        normalized_name=name,
        status="provisional",
        created_by="pytest",
    )
    db.add(item)
    db.flush()
    return item


def _subject(db, subject_type, name, key, *, status="provisional", provenance=None):
    item = V2Subject(
        subject_type_id=subject_type.id,
        owner_id=OWNER,
        name=name,
        canonical_key=key,
        identifiers_json={},
        attributes_json={},
        provenance_json=provenance or {},
        classification_status=status,
    )
    db.add(item)
    db.flush()
    return item


def _experience(db, subject, headline="Review"):
    return create_experience(
        db,
        ExperienceCreate(
            owner_id=OWNER,
            subject_id=subject.id,
            headline=headline,
            summary="Summary",
            raw_text="Raw review",
            user_approved=True,
        ),
        CLIENT,
        commit=False,
    )


def _record_first_decision_through_mcp(db, subject):
    result = _affirm_subject_classification(
        db,
        PRINCIPAL,
        {
            "subject_id": str(subject.id),
            "source_model": "model-a",
            "reason": "This subject independently supports its current classification.",
            "evidence": {},
        },
    )
    assert result.get("isError") is not True


def test_new_subject_is_blocked_until_previous_first_classification_decision():
    with _session() as db:
        first_type = _type(db, "kettle")
        second_type = _type(db, "book")
        first = _subject(db, first_type, "First kettle", "first-kettle")
        _experience(db, first)
        start_or_resume_enrichment_workflow(
            db, first, owner_id=OWNER, actor_client=CLIENT
        )
        second = _subject(db, second_type, "Second book", "second-book")

        with pytest.raises(DeliberationError) as exc:
            _experience(db, second)

        assert exc.value.code == "CLASSIFICATION_WORKFLOW_PENDING"
        assert exc.value.details["pending_subject_id"] == str(first.id)
        assert exc.value.details["required_action"] == "get_subject_classification"


def test_client_can_continue_after_mcp_first_decision_without_waiting_for_second_model():
    with _session() as db:
        first_type = _type(db, "kettle")
        second_type = _type(db, "book")
        first = _subject(db, first_type, "First kettle", "first-kettle")
        _experience(db, first)
        start_or_resume_enrichment_workflow(
            db, first, owner_id=OWNER, actor_client=CLIENT
        )
        _record_first_decision_through_mcp(db, first)
        second = _subject(db, second_type, "Second book", "second-book")

        saved = _experience(db, second)

        assert saved.id is not None


def test_existing_subject_with_historical_experience_is_not_batch_gated():
    with _session() as db:
        subject_type = _type(db, "kettle")
        subject = _subject(db, subject_type, "First kettle", "first-kettle")
        _experience(db, subject, "First review")
        start_or_resume_enrichment_workflow(
            db, subject, owner_id=OWNER, actor_client=CLIENT
        )

        second_review = _experience(db, subject, "Second review")

        assert second_review.id is not None


def test_reusing_last_type_for_new_subject_requires_specific_confirmation():
    with _session() as db:
        subject_type = _type(db, "kettle")
        first = _subject(
            db, subject_type, "First kettle", "first-kettle", status="confirmed"
        )
        _experience(db, first)
        start_or_resume_enrichment_workflow(
            db, first, owner_id=OWNER, actor_client=CLIENT
        )
        second = _subject(db, subject_type, "Second kettle", "second-kettle")

        with pytest.raises(DeliberationError) as exc:
            _experience(db, second)

        assert exc.value.code == "CLASSIFICATION_REUSE_CONFIRMATION_REQUIRED"
        assert exc.value.details["previous_subject_id"] == str(first.id)
        assert exc.value.details["subject_type"] == "kettle"


def test_specific_reuse_confirmation_is_preserved_in_experience_audit():
    with _session() as db:
        subject_type = _type(db, "kettle")
        first = _subject(
            db, subject_type, "First kettle", "first-kettle", status="confirmed"
        )
        _experience(db, first)
        start_or_resume_enrichment_workflow(
            db, first, owner_id=OWNER, actor_client=CLIENT
        )
        confirmation = {
            "subject_ref": "second-kettle",
            "independently_assessed": True,
            "evidence": "Second kettle is identified by its own product evidence as a kettle.",
            "specificity_checked": True,
            "specificity_reason": "No more specific confirmed descendant type is justified for Second kettle.",
        }
        second = _subject(
            db,
            subject_type,
            "Second kettle",
            "second-kettle",
            provenance={"classification_confirmation": confirmation},
        )

        saved = _experience(db, second)

        assert saved.provenance["classification_confirmation"] == confirmation


def test_generic_or_wrong_subject_confirmation_is_rejected():
    with _session() as db:
        subject_type = _type(db, "kettle")
        first = _subject(
            db, subject_type, "First kettle", "first-kettle", status="confirmed"
        )
        _experience(db, first)
        start_or_resume_enrichment_workflow(
            db, first, owner_id=OWNER, actor_client=CLIENT
        )
        second = _subject(
            db,
            subject_type,
            "Second kettle",
            "second-kettle",
            provenance={
                "classification_confirmation": {
                    "subject_ref": "some-other-item",
                    "independently_assessed": True,
                    "evidence": "looks right",
                    "specificity_checked": True,
                    "specificity_reason": "same as before",
                }
            },
        )

        with pytest.raises(DeliberationError) as exc:
            _experience(db, second)

        assert exc.value.code == "CLASSIFICATION_REUSE_CONFIRMATION_INVALID"
