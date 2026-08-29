import uuid

import pytest

from app.db.session import SessionLocal
from app.models.entities import User
from app.models.v2 import SubjectType, TypeRelationship, V2Subject
from app.services.classification import propose_reclassification
from app.services.semantic import add_semantic_relationship, resolve_subject_hierarchy


def _type(db, name: str) -> SubjectType:
    item = SubjectType(
        canonical_name=name,
        normalized_name=name.casefold(),
        status="provisional",
        created_by="pytest",
    )
    db.add(item)
    db.flush()
    return item


def _root(db) -> SubjectType:
    token = uuid.uuid4().hex
    return _type(db, f"semantic guard root {token}")


@pytest.mark.parametrize(
    ("phrase", "head", "category"),
    [
        ("bale stack", "bale", "arrangement"),
        ("straw bale", "bale", "material"),
        ("group of tyres", "tyre", "arrangement"),
        ("red car", "car", "colour"),
        ("broken server", "server", "condition"),
        ("crate stack", "crate", "arrangement"),
    ],
)
def test_hierarchy_rejects_descriptor_derived_subject_types(phrase, head, category):
    with SessionLocal() as db:
        root = _root(db)
        db.commit()
        with pytest.raises(ValueError) as exc:
            resolve_subject_hierarchy(db, [root.canonical_name, phrase], created_by="pytest")
        message = str(exc.value).casefold()
        assert "semantic head" in message
        assert head in message
        assert category in message
        assert "attribute" in message


@pytest.mark.parametrize("phrase", ["sports car", "fire engine", "operating system"])
def test_legitimate_compound_types_are_allowed(phrase):
    with SessionLocal() as db:
        root = _root(db)
        db.commit()
        result = resolve_subject_hierarchy(db, [root.canonical_name, phrase], created_by="pytest")
        assert result["leaf"].canonical_name == phrase


def test_existing_valid_hierarchy_creation_still_works():
    with SessionLocal() as db:
        root = _root(db)
        db.commit()
        result = resolve_subject_hierarchy(db, [root.canonical_name, "hydraulic pump"], created_by="pytest")
        assert result["leaf"].canonical_name == "hydraulic pump"
        assert result["relationships"][-1]["relationship"] == "belongs_to"


def test_set_type_relationship_rejects_descriptor_derived_type():
    with SessionLocal() as db:
        source = _type(db, "red machine")
        target = _root(db)
        db.commit()
        with pytest.raises(ValueError, match="semantic head"):
            add_semantic_relationship(db, source, "belongs_to", target, source="pytest")


def test_reclassification_rejects_descriptor_derived_target():
    with SessionLocal() as db:
        user = User(display_name=f"semantic-head-{uuid.uuid4()}", profile_data={})
        db.add(user)
        parent = _root(db)
        child = _type(db, "broken appliance")
        db.add(TypeRelationship(
            source_type_id=child.id,
            relationship="belongs_to",
            target_type_id=parent.id,
            source="pytest-legacy",
        ))
        subject = V2Subject(
            subject_type_id=parent.id,
            owner_id=user.id,
            name="Test appliance",
            canonical_key=f"semantic-head-{uuid.uuid4()}",
            identifiers_json={},
            attributes_json={},
            provenance_json={},
        )
        db.add(subject)
        db.commit()
        db.refresh(subject)

        with pytest.raises(ValueError, match="semantic head"):
            propose_reclassification(
                db,
                subject,
                target_subject_type="broken appliance",
                source_model="model-a",
                source_client="pytest",
                reason="legacy child exists",
                evidence={},
            )


def test_cycle_protection_still_applies_after_semantic_guard():
    with SessionLocal() as db:
        parent = _type(db, f"machine {uuid.uuid4().hex}")
        child = _type(db, f"pump {uuid.uuid4().hex}")
        add_semantic_relationship(db, child, "belongs_to", parent, source="pytest")
        with pytest.raises(ValueError, match="cycle"):
            add_semantic_relationship(db, parent, "belongs_to", child, source="pytest")
