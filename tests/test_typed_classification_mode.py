import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.models.v2 import SubjectType, TypeRelationship, V2Subject
from app.services.classification import classification_state
from app.services.semantic import add_semantic_relationship, resolve_subject_hierarchy
from app.services.v2 import descendant_type_ids, resolve_subject_type, vocabulary_index


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _type(db: Session, name: str) -> SubjectType:
    item = SubjectType(
        canonical_name=name,
        normalized_name=name,
        status="confirmed",
        created_by="pytest",
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


def test_classification_mode_defaults_to_legacy_and_rejects_unknown_values(monkeypatch):
    monkeypatch.delenv("CLASSIFICATION_MODE", raising=False)
    get_settings.cache_clear()
    try:
        assert Settings(_env_file=None).classification_mode == "legacy"
        assert Settings(_env_file=None, classification_mode="typed").classification_mode == "typed"
        with pytest.raises(ValidationError):
            Settings(_env_file=None, classification_mode="other")
    finally:
        get_settings.cache_clear()


def test_typed_mode_keeps_distinct_edge_meanings_and_multiple_taxonomic_parents(typed_mode):
    with _session() as db:
        leaf = _type(db, "shared typed leaf")
        first_parent = _type(db, "first typed category")
        second_parent = _type(db, "second typed category")
        system = _type(db, "larger typed system")

        first = add_semantic_relationship(db, leaf, "is_a", first_parent, source="pytest")
        second = add_semantic_relationship(db, leaf, "is_a", second_parent, source="pytest")
        membership = add_semantic_relationship(db, leaf, "part_of", system, source="pytest")

        assert first.status == second.status == membership.status == "active"
        assert set(db.scalars(select(TypeRelationship.relationship).where(
            TypeRelationship.source_type_id == leaf.id,
            TypeRelationship.status == "active",
        )).all()) == {"is_a", "part_of"}
        assert db.scalar(select(func.count()).select_from(SubjectType).where(
            SubjectType.id == leaf.id,
        )) == 1


def test_typed_mode_rejects_ambiguous_belongs_to(typed_mode):
    with _session() as db:
        leaf = _type(db, "ambiguous typed leaf")
        parent = _type(db, "ambiguous typed parent")

        with pytest.raises(ValueError, match="choose 'is_a' or 'part_of'"):
            add_semantic_relationship(db, leaf, "belongs_to", parent, source="pytest")


def test_typed_duplicate_edges_are_idempotent_and_retired_edges_stay_tombstoned(typed_mode):
    with _session() as db:
        leaf = _type(db, "typed tombstone leaf")
        parent = _type(db, "typed tombstone parent")
        first = add_semantic_relationship(db, leaf, "is_a", parent, source="first-model")
        duplicate = add_semantic_relationship(db, leaf, "is_a", parent, source="second-model")
        first.status = "retired"
        first.retired_reason = "Rejected during test review"
        db.commit()

        assert duplicate.id == first.id
        with pytest.raises(ValueError, match="previously rejected"):
            add_semantic_relationship(db, leaf, "is_a", parent, source="third-model")


@pytest.mark.parametrize("relationship", ["is_a", "part_of"])
def test_typed_mode_rejects_cycles_within_each_edge_meaning(typed_mode, relationship):
    with _session() as db:
        first = _type(db, f"{relationship} first")
        second = _type(db, f"{relationship} second")
        add_semantic_relationship(db, first, relationship, second, source="pytest")

        with pytest.raises(ValueError, match="cycle"):
            add_semantic_relationship(db, second, relationship, first, source="pytest")


def test_typed_hierarchy_uses_is_a_and_reuses_one_stable_leaf(typed_mode):
    with _session() as db:
        result = resolve_subject_hierarchy(
            db,
            ["typed fixture root", "typed fixture category", "typed fixture leaf"],
            created_by="pytest",
        )
        leaf = resolve_subject_type(db, "typed fixture leaf")

        assert result["taxonomy_relationship"] == "is_a"
        assert [edge["relationship"] for edge in result["relationships"]] == ["is_a", "is_a"]
        assert result["leaf"].id == leaf.id


def test_legacy_hierarchy_still_uses_belongs_to(monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_MODE", "legacy")
    get_settings.cache_clear()
    try:
        with _session() as db:
            result = resolve_subject_hierarchy(
                db,
                ["legacy fixture root", "legacy fixture leaf"],
                created_by="pytest",
            )
            assert result["taxonomy_relationship"] == "belongs_to"
            assert result["relationships"][0]["relationship"] == "belongs_to"
    finally:
        get_settings.cache_clear()


def test_typed_descendant_search_and_specificity_use_is_a_only(typed_mode):
    with _session() as db:
        category = _type(db, "typed search category")
        system = _type(db, "typed search system")
        subtype = _type(db, "typed search subtype")
        member = _type(db, "typed search member")
        add_semantic_relationship(db, subtype, "is_a", category, source="pytest")
        add_semantic_relationship(db, member, "part_of", system, source="pytest")
        subject = V2Subject(
            subject_type_id=category.id,
            name="Typed classification subject",
            canonical_key="typed-classification-subject",
            owner_id=None,
        )
        db.add(subject)
        db.flush()

        assert descendant_type_ids(db, category) == {category.id, subtype.id}
        assert descendant_type_ids(db, system) == {system.id}
        assert classification_state(db, subject)["direct_child_types"] == [subtype.canonical_name]


def test_vocabulary_index_explains_active_classification_contract(typed_mode):
    with _session() as db:
        index = vocabulary_index(db)

        assert index["classification_mode"] == "typed"
        assert index["taxonomy_relationship"] == "is_a"
        assert index["supported_classification_relationships"] == ["is_a", "part_of"]
