import random

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.v2 import SubjectType, TypeRelationship
from app.services.semantic import add_semantic_relationship, resolve_subject_hierarchy, retire_semantic_relationship
from app.services.v2 import resolve_subject_type, vocabulary_index


def _new_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _graph_signature(db: Session):
    index = vocabulary_index(db)
    types = tuple(sorted(item["canonical_name"] for item in index["subject_types"]))
    edges = tuple(sorted(
        (item["source"], item["relationship"], item["target"])
        for item in index["relationships"]
    ))
    return types, edges


def test_unknown_type_cannot_be_created_as_isolated_root():
    with _new_session() as db:
        with pytest.raises(ValueError, match="cannot be created as an isolated root"):
            resolve_subject_hierarchy(db, ["recipe"], created_by="test")
        assert db.scalars(select(SubjectType)).all() == []


def test_first_recipe_creates_food_then_recipe_relationship():
    with _new_session() as db:
        result = resolve_subject_hierarchy(db, ["food", "recipe"], created_by="test")
        assert result["leaf"].canonical_name == "recipe"
        assert result["created_terms"] == ["food", "recipe"]
        assert result["relationships"] == [
            {"source": "recipe", "relationship": "belongs_to", "target": "food"}
        ]
        assert resolve_subject_type(db, "recipes").id == result["leaf"].id


def test_existing_dictionary_terms_are_reused_when_new_leaf_arrives():
    with _new_session() as db:
        resolve_subject_hierarchy(db, ["food", "recipe"], created_by="first")
        second = resolve_subject_hierarchy(db, ["food", "restaurant"], created_by="second")
        food = resolve_subject_type(db, "food")
        assert second["path"][0]["id"] == str(food.id)
        assert second["path"][0]["created"] is False
        assert second["created_terms"] == ["restaurant"]


def test_belongs_to_cycle_is_rejected():
    with _new_session() as db:
        resolve_subject_hierarchy(db, ["transportation", "ferry"], created_by="test")
        ferry = resolve_subject_type(db, "ferry")
        transportation = resolve_subject_type(db, "transportation")
        with pytest.raises(ValueError, match="would create a cycle"):
            add_semantic_relationship(
                db,
                transportation,
                "belongs_to",
                ferry,
                source="test",
            )


def test_random_submission_order_produces_same_semantic_graph():
    paths = [
        ["food", "recipe"],
        ["services", "healthcare", "dentist"],
        ["transportation", "ferry"],
        ["travel", "ski resort"],
        ["food and drink", "brewery"],
        ["transportation", "rail", "train station"],
        ["hospitality", "hotel"],
        ["fitness", "gym"],
        ["media", "book"],
        ["technology", "software", "software application"],
    ]

    signatures = []
    for seed in range(12):
        shuffled = list(paths)
        random.Random(seed).shuffle(shuffled)
        with _new_session() as db:
            for path in shuffled:
                resolve_subject_hierarchy(db, path, created_by=f"seed-{seed}")
            signatures.append(_graph_signature(db))

    assert all(signature == signatures[0] for signature in signatures[1:])


def test_hundred_diverse_leaf_insertions_are_order_independent():
    roots = ["food", "transportation", "hospitality", "fitness", "technology"]
    paths = [
        [roots[index % len(roots)], f"test subject type {index}"]
        for index in range(100)
    ]

    signatures = []
    for seed in (7, 41, 99):
        shuffled = list(paths)
        random.Random(seed).shuffle(shuffled)
        with _new_session() as db:
            for path in shuffled:
                resolve_subject_hierarchy(db, path, created_by=f"stress-{seed}")
            signatures.append(_graph_signature(db))

    assert all(signature == signatures[0] for signature in signatures[1:])
    assert len(signatures[0][0]) == 105
    assert len(signatures[0][1]) == 100


def test_retired_relationship_disappears_without_changing_type_ids():
    with _new_session() as db:
        resolve_subject_hierarchy(db, ["transportation", "makerspace"], created_by="first-ai")
        makerspace = resolve_subject_type(db, "makerspace")
        transportation = resolve_subject_type(db, "transportation")
        makerspace_id = makerspace.id
        transportation_id = transportation.id

        retired = retire_semantic_relationship(
            db, makerspace, "belongs_to", transportation,
            reason="Makerspaces are not a kind of transportation",
            retired_by="human-admin",
        )

        assert retired.status == "retired"
        assert retired.retired_reason == "Makerspaces are not a kind of transportation"
        assert resolve_subject_type(db, "makerspace").id == makerspace_id
        assert resolve_subject_type(db, "transportation").id == transportation_id
        assert vocabulary_index(db)["relationships"] == []


def test_another_ai_cannot_recreate_a_retired_relationship():
    with _new_session() as db:
        resolve_subject_hierarchy(db, ["transportation", "makerspace"], created_by="first-ai")
        makerspace = resolve_subject_type(db, "makerspace")
        transportation = resolve_subject_type(db, "transportation")
        retire_semantic_relationship(
            db, makerspace, "belongs_to", transportation,
            reason="Incorrect classification",
            retired_by="human-admin",
        )

        with pytest.raises(ValueError, match="previously rejected"):
            add_semantic_relationship(
                db, makerspace, "belongs_to", transportation, source="second-ai"
            )

        rows = list(db.scalars(select(TypeRelationship)).all())
        assert len(rows) == 1
        assert rows[0].status == "retired"


def test_new_belongs_to_target_automatically_reclassifies_existing_edge():
    with _new_session() as db:
        resolve_subject_hierarchy(db, ["transportation", "makerspace"], created_by="first-ai")
        resolve_subject_hierarchy(db, ["services", "community space"], created_by="seed")
        makerspace = resolve_subject_type(db, "makerspace")
        services = resolve_subject_type(db, "services")

        replacement = add_semantic_relationship(
            db, makerspace, "belongs_to", services, source="second-ai"
        )

        rows = list(db.scalars(select(TypeRelationship).where(
            TypeRelationship.source_type_id == makerspace.id,
            TypeRelationship.relationship == "belongs_to",
        )).all())
        assert len(rows) == 2
        assert replacement.status == "active"
        assert replacement.target_type_id == services.id
        retired = next(row for row in rows if row.status == "retired")
        assert retired.retired_by == "second-ai"
        assert "Automatically reclassified" in retired.retired_reason
        assert vocabulary_index(db)["relationships"] == [
            {"source": "community space", "relationship": "belongs_to", "target": "services"},
            {"source": "makerspace", "relationship": "belongs_to", "target": "services"},
        ]


def test_automatic_reclassification_refuses_ambiguous_multiple_active_parents():
    with _new_session() as db:
        resolve_subject_hierarchy(db, ["root one", "thing"], created_by="seed")
        root_two = SubjectType(
            canonical_name="root two", normalized_name="root two", status="provisional", created_by="seed"
        )
        root_three = SubjectType(
            canonical_name="root three", normalized_name="root three", status="provisional", created_by="seed"
        )
        db.add_all([root_two, root_three])
        db.flush()
        thing = resolve_subject_type(db, "thing")
        db.add(TypeRelationship(
            source_type_id=thing.id,
            relationship="belongs_to",
            target_type_id=root_two.id,
            source="legacy",
            status="active",
        ))
        db.commit()

        with pytest.raises(ValueError, match="multiple active belongs_to relationships"):
            add_semantic_relationship(
                db, thing, "belongs_to", root_three, source="new-ai"
            )
