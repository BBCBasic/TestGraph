import uuid

import pytest

from app.db.session import SessionLocal
from app.models.entities import User
from app.models.v2 import SubjectType, TypeRelationship, V2Subject
from app.services.classification import affirm_classification, classification_state


def _type(db, stem):
    token = uuid.uuid4().hex
    name = f"{stem} {token}"
    item = SubjectType(
        canonical_name=name,
        normalized_name=name,
        status="provisional",
        created_by="pytest",
    )
    db.add(item)
    db.flush()
    return item


def test_classification_state_exposes_immediate_stricter_children():
    with SessionLocal() as db:
        user = User(display_name=f"specificity-{uuid.uuid4()}", profile_data={})
        db.add(user); db.flush()
        parent = _type(db, "parent")
        child = _type(db, "child")
        grandchild = _type(db, "grandchild")
        db.add(TypeRelationship(source_type_id=child.id, relationship="belongs_to", target_type_id=parent.id, source="pytest"))
        db.add(TypeRelationship(source_type_id=grandchild.id, relationship="belongs_to", target_type_id=child.id, source="pytest"))
        subject = V2Subject(
            subject_type_id=parent.id,
            owner_id=user.id,
            name="Generic subject",
            canonical_key=f"subject-{uuid.uuid4()}",
            identifiers_json={}, attributes_json={}, provenance_json={},
        )
        db.add(subject); db.commit(); db.refresh(subject)

        state = classification_state(db, subject)
        assert state["direct_child_types"] == [child.canonical_name]


def test_affirmation_requires_assessment_of_every_direct_child():
    with SessionLocal() as db:
        user = User(display_name=f"specificity-{uuid.uuid4()}", profile_data={})
        db.add(user); db.flush()
        parent = _type(db, "parent")
        child = _type(db, "child")
        db.add(TypeRelationship(source_type_id=child.id, relationship="belongs_to", target_type_id=parent.id, source="pytest"))
        subject = V2Subject(
            subject_type_id=parent.id,
            owner_id=user.id,
            name="Generic subject",
            canonical_key=f"subject-{uuid.uuid4()}",
            identifiers_json={}, attributes_json={}, provenance_json={},
        )
        db.add(subject); db.commit(); db.refresh(subject)

        with pytest.raises(ValueError, match="direct child"):
            affirm_classification(
                db, subject,
                source_model="model-a", source_client="pytest",
                reason="parent is correct", evidence={},
            )

        with pytest.raises(ValueError, match="propose_subject_reclassification"):
            affirm_classification(
                db, subject,
                source_model="model-a", source_client="pytest",
                reason="parent is correct",
                evidence={"direct_child_assessment": {
                    child.canonical_name: {"applicable": True, "reason": "The child type fits."}
                }},
            )

        state = affirm_classification(
            db, subject,
            source_model="model-a", source_client="pytest",
            reason="parent is the most specific justified type",
            evidence={"direct_child_assessment": {
                child.canonical_name: {"applicable": False, "reason": "Evidence does not support this child type."}
            }},
        )
        assert state["status"] == "candidate"
