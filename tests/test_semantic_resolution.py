import random

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.v2 import SubjectType, TypeRelationship
from app.services.semantic import add_semantic_relationship, resolve_subject_hierarchy
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
