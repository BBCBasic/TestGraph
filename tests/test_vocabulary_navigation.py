import base64

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.models.v2 import SubjectType, SubjectTypeAlias, TypeRelationship
from app.services.vocabulary_navigation import (
    get_subject_type_paths,
    list_child_subject_types,
    list_root_subject_types,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _type(db, name, *, status="confirmed", description=None):
    item = SubjectType(
        canonical_name=name,
        normalized_name=name,
        status=status,
        description=description,
        created_by="pytest",
    )
    db.add(item)
    db.flush()
    return item


def _edge(db, child, parent, *, status="active", relationship="belongs_to"):
    item = TypeRelationship(
        source_type_id=child.id,
        relationship=relationship,
        target_type_id=parent.id,
        source="pytest",
        status=status,
    )
    db.add(item)
    db.flush()
    return item


@pytest.fixture()
def typed_mode(monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_MODE", "typed")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_roots_exclude_types_with_active_parents_but_not_retired_parents():
    with _session() as db:
        entity = _type(db, "entity")
        vehicle = _type(db, "vehicle")
        archive = _type(db, "archive")
        _edge(db, vehicle, entity)
        _edge(db, archive, entity, status="retired")

        result = list_root_subject_types(db)

        assert [item["canonical_name"] for item in result["items"]] == ["archive", "entity"]
        assert result["count"] == 2
        assert result["has_more"] is False
        assert result["next_cursor"] is None


def test_root_summary_contains_aliases_description_and_immediate_child_count():
    with _session() as db:
        entity = _type(db, "entity", description="Anything with stable identity")
        vehicle = _type(db, "vehicle")
        _edge(db, vehicle, entity)
        db.add(SubjectTypeAlias(
            subject_type_id=entity.id,
            alias="thing",
            normalized_alias="thing",
            source="pytest",
        ))
        db.flush()

        item = list_root_subject_types(db)["items"][0]

        assert item == {
            "id": str(entity.id),
            "canonical_name": "entity",
            "status": "confirmed",
            "description": "Anything with stable identity",
            "aliases": ["thing"],
            "child_count": 1,
        }


def test_children_are_immediate_and_paginated_without_returning_grandchildren():
    with _session() as db:
        entity = _type(db, "entity")
        vehicle = _type(db, "vehicle")
        bicycle = _type(db, "bicycle")
        car = _type(db, "car")
        electric_car = _type(db, "electric car")
        _edge(db, vehicle, entity)
        _edge(db, bicycle, vehicle)
        _edge(db, car, vehicle)
        _edge(db, electric_car, car)

        first = list_child_subject_types(db, "vehicle", limit=1)
        second = list_child_subject_types(db, "vehicle", limit=1, cursor=first["next_cursor"])

        assert [item["canonical_name"] for item in first["items"]] == ["bicycle"]
        assert [item["canonical_name"] for item in second["items"]] == ["car"]
        assert first["has_more"] is True
        assert second["has_more"] is False
        assert first["items"][0]["child_count"] == 0
        assert second["items"][0]["child_count"] == 1


def test_child_cursor_cannot_be_reused_for_another_parent():
    with _session() as db:
        first_parent = _type(db, "first parent")
        second_parent = _type(db, "second parent")
        _edge(db, _type(db, "first child a"), first_parent)
        _edge(db, _type(db, "first child b"), first_parent)
        _edge(db, _type(db, "second child"), second_parent)
        page = list_child_subject_types(db, first_parent, limit=1)

        with pytest.raises(ValueError, match="cursor does not match"):
            list_child_subject_types(db, second_parent, limit=1, cursor=page["next_cursor"])


def test_malformed_cursor_is_rejected_as_a_navigation_error():
    with _session() as db:
        _type(db, "entity")

        with pytest.raises(ValueError, match="Invalid vocabulary cursor"):
            list_root_subject_types(db, cursor="not-valid-base64!!!")


def test_well_encoded_non_object_cursor_is_rejected_as_a_navigation_error():
    with _session() as db:
        _type(db, "entity")
        cursor = base64.urlsafe_b64encode(b"[]").decode().rstrip("=")

        with pytest.raises(ValueError, match="Invalid vocabulary cursor"):
            list_root_subject_types(db, cursor=cursor)


def test_path_is_returned_root_to_leaf_with_stable_type_summaries():
    with _session() as db:
        vehicle = _type(db, "vehicle")
        car = _type(db, "car")
        electric_car = _type(db, "electric car")
        _edge(db, car, vehicle)
        _edge(db, electric_car, car)

        result = get_subject_type_paths(db, "electric car")

        assert result["subject_type"]["id"] == str(electric_car.id)
        assert [[node["canonical_name"] for node in path] for path in result["paths"]] == [
            ["vehicle", "car", "electric car"]
        ]
        assert [item["canonical_name"] for item in result["immediate_parents"]] == ["car"]
        assert result["truncated"] is False


def test_path_returns_all_legacy_multiple_parent_paths_without_guessing():
    with _session() as db:
        first_root = _type(db, "first root")
        second_root = _type(db, "second root")
        leaf = _type(db, "shared leaf")
        _edge(db, leaf, first_root)
        _edge(db, leaf, second_root)

        result = get_subject_type_paths(db, leaf)

        assert [[node["canonical_name"] for node in path] for path in result["paths"]] == [
            ["first root", "shared leaf"],
            ["second root", "shared leaf"],
        ]
        assert [item["canonical_name"] for item in result["immediate_parents"]] == [
            "first root",
            "second root",
        ]


def test_typed_navigation_keeps_taxonomy_and_membership_separate(typed_mode):
    with _session() as db:
        category = _type(db, "typed navigation category")
        system = _type(db, "typed navigation system")
        leaf = _type(db, "typed navigation leaf")
        _edge(db, leaf, category, relationship="is_a")
        _edge(db, leaf, system, relationship="part_of")

        taxonomy_children = list_child_subject_types(db, category)
        system_taxonomy_children = list_child_subject_types(db, system)
        membership_children = list_child_subject_types(db, system, relationship="part_of")

        assert [item["canonical_name"] for item in taxonomy_children["items"]] == [leaf.canonical_name]
        assert system_taxonomy_children["items"] == []
        assert [item["canonical_name"] for item in membership_children["items"]] == [leaf.canonical_name]
        assert taxonomy_children["relationship"] == "is_a"
        assert membership_children["relationship"] == "part_of"
        assert taxonomy_children["classification_mode"] == "typed"


def test_typed_cursor_cannot_be_reused_for_another_relationship(typed_mode):
    with _session() as db:
        parent = _type(db, "typed cursor parent")
        _edge(db, _type(db, "typed cursor child a"), parent, relationship="is_a")
        _edge(db, _type(db, "typed cursor child b"), parent, relationship="is_a")
        _edge(db, _type(db, "typed cursor member"), parent, relationship="part_of")
        page = list_child_subject_types(db, parent, relationship="is_a", limit=1)

        with pytest.raises(ValueError, match="cursor does not match"):
            list_child_subject_types(
                db,
                parent,
                relationship="part_of",
                limit=1,
                cursor=page["next_cursor"],
            )


def test_typed_paths_follow_only_requested_relationship(typed_mode):
    with _session() as db:
        category = _type(db, "typed path category")
        system = _type(db, "typed path system")
        leaf = _type(db, "typed path leaf")
        _edge(db, leaf, category, relationship="is_a")
        _edge(db, leaf, system, relationship="part_of")

        taxonomy = get_subject_type_paths(db, leaf)
        membership = get_subject_type_paths(db, leaf, relationship="part_of")

        assert [[node["canonical_name"] for node in path] for path in taxonomy["paths"]] == [
            [category.canonical_name, leaf.canonical_name]
        ]
        assert [[node["canonical_name"] for node in path] for path in membership["paths"]] == [
            [system.canonical_name, leaf.canonical_name]
        ]


def test_typed_multiple_paths_remain_bounded(typed_mode):
    with _session() as db:
        leaf = _type(db, "typed bounded leaf")
        first_parent = _type(db, "typed bounded parent a")
        second_parent = _type(db, "typed bounded parent b")
        _edge(db, leaf, first_parent, relationship="is_a")
        _edge(db, leaf, second_parent, relationship="is_a")

        result = get_subject_type_paths(db, leaf, max_paths=1)

        assert result["path_count"] == 1
        assert result["truncated"] is True
