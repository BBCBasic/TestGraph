import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.models.entities import User
from app.models.v2 import FieldDefinition, SubjectType, TypeRelationship, V2Subject
from app.schemas.deliberation import DeliberationCreate
from app.schemas.v2 import FieldEnsure, SubjectEnsure
from app.services.classification import propose_reclassification
from app.services.deliberation import DeliberationError, create_deliberation
from app.services.synthetic_root import (
    SYNTHETIC_ROOT_ID,
    assert_semantic_type,
    check_synthetic_root_integrity,
    ensure_synthetic_root,
)
from app.services.semantic import (
    add_semantic_relationship,
    resolve_subject_hierarchy,
    retire_semantic_relationship,
)
from app.services.v2 import (
    add_subject_type_alias,
    ensure_subject,
    ensure_subject_type,
    ensure_field,
    resolve_subject_type,
    vocabulary_index,
)
from app.services.vocabulary_navigation import (
    list_child_subject_types,
    list_root_subject_types,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


@pytest.fixture(autouse=True)
def typed_mode(monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_MODE", "typed")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_synthetic_root_has_one_stable_reserved_identity():
    with _session() as db:
        first = ensure_synthetic_root(db)
        second = ensure_synthetic_root(db)

        assert first.id == second.id == SYNTHETIC_ROOT_ID
        assert first.canonical_name == first.normalized_name == "."
        assert first.is_synthetic is True
        assert first.status == "system"
        assert db.scalar(select(func.count()).select_from(SubjectType).where(
            SubjectType.is_synthetic.is_(True)
        )) == 1


def test_database_rejects_a_second_synthetic_root():
    with _session() as db:
        ensure_synthetic_root(db)
        db.add(SubjectType(
            canonical_name="second synthetic",
            normalized_name="second synthetic",
            status="system",
            is_synthetic=True,
            created_by="invalid",
        ))

        with pytest.raises(IntegrityError):
            db.commit()


def test_synthetic_root_is_not_ordinary_vocabulary_or_a_subject_classification():
    with _session() as db:
        root = ensure_synthetic_root(db)

        with pytest.raises(ValueError, match="synthetic root"):
            assert_semantic_type(root)
        with pytest.raises(ValueError, match="synthetic root"):
            ensure_subject_type(db, ".", created_by="pytest")
        with pytest.raises(ValueError, match="synthetic root"):
            ensure_subject(db, SubjectEnsure(
                subject_type=".", name="Not a semantic subject", canonical_key="not-semantic"
            ))
        with pytest.raises(ValueError, match="synthetic root"):
            add_subject_type_alias(db, root, "universal thing", source="pytest")
        with pytest.raises(ValueError, match="synthetic root"):
            ensure_field(db, FieldEnsure(
                canonical_name="invalid root field",
                json_schema={"type": "string"},
                subject_types=["."],
            ), source="pytest")


def test_classification_decision_cannot_target_synthetic_root():
    with _session() as db:
        ensure_synthetic_root(db)
        current = ensure_subject_type(db, "ordinary type", created_by="pytest")[0]
        subject = ensure_subject(db, SubjectEnsure(
            subject_type=current.canonical_name,
            name="Ordinary subject",
            canonical_key="ordinary-subject",
        ))

        with pytest.raises(ValueError, match="synthetic root"):
            propose_reclassification(
                db,
                subject,
                target_subject_type=".",
                source_model="model-a",
                source_client="pytest",
                reason="Invalid infrastructure classification",
                evidence={},
            )


def test_synthetic_root_cannot_be_deleted_or_renamed():
    with _session() as db:
        root = ensure_synthetic_root(db)
        root.canonical_name = "renamed"
        with pytest.raises(ValueError, match="cannot be renamed"):
            db.flush()
        db.rollback()

        root = db.get(SubjectType, SYNTHETIC_ROOT_ID)
        db.delete(root)
        with pytest.raises(ValueError, match="cannot be deleted"):
            db.flush()


def test_synthetic_root_marker_cannot_be_removed():
    with _session() as db:
        root = ensure_synthetic_root(db)
        root.is_synthetic = False

        with pytest.raises(ValueError, match="cannot be renamed or modified"):
            db.flush()


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("description", "Changed infrastructure meaning"),
        ("public_location_eligible", True),
        ("created_by", "ordinary-user"),
    ],
)
def test_all_synthetic_root_metadata_is_immutable(attribute, value):
    with _session() as db:
        root = ensure_synthetic_root(db)
        setattr(root, attribute, value)

        with pytest.raises(ValueError, match="cannot be renamed or modified"):
            db.flush()


def test_new_semantic_type_is_attached_to_root_and_progressive_traversal_starts_there():
    with _session() as db:
        equipment = ensure_subject_type(db, "equipment", created_by="pytest")[0]
        roots = list_root_subject_types(db)
        children = list_child_subject_types(db, ".", limit=1)

        assert [item["canonical_name"] for item in roots["items"]] == ["."]
        assert roots["items"][0]["is_synthetic"] is True
        assert [item["canonical_name"] for item in children["items"]] == ["equipment"]
        assert children["parent"]["id"] == str(SYNTHETIC_ROOT_ID)
        assert children["has_more"] is False
        assert db.scalar(select(TypeRelationship).where(
            TypeRelationship.source_type_id == equipment.id,
            TypeRelationship.relationship == "is_a",
            TypeRelationship.target_type_id == SYNTHETIC_ROOT_ID,
            TypeRelationship.status == "active",
        )) is not None


def test_one_term_hierarchy_can_create_a_new_semantic_root_beneath_dot():
    with _session() as db:
        result = resolve_subject_hierarchy(db, ["equipment"], created_by="pytest")

        assert result["leaf"].canonical_name == "equipment"
        assert result["created_terms"] == ["equipment"]
        assert result["relationships"] == []
        assert [
            item["canonical_name"]
            for item in list_child_subject_types(db, ".")["items"]
        ] == ["equipment"]


def test_semantic_parent_replaces_only_infrastructure_edge_and_preserves_descendants():
    with _session() as db:
        result = resolve_subject_hierarchy(
            db, ["equipment", "power tool", "impact driver"], created_by="pytest"
        )
        equipment = resolve_subject_type(db, "equipment")
        power_tool = resolve_subject_type(db, "power tool")
        impact_driver = resolve_subject_type(db, "impact driver")

        assert result["relationships"] == [
            {"source": "power tool", "relationship": "is_a", "target": "equipment"},
            {"source": "impact driver", "relationship": "is_a", "target": "power tool"},
        ]
        assert db.scalar(select(TypeRelationship).where(
            TypeRelationship.source_type_id == equipment.id,
            TypeRelationship.target_type_id == SYNTHETIC_ROOT_ID,
            TypeRelationship.relationship == "is_a",
            TypeRelationship.status == "active",
        )) is not None
        assert db.scalar(select(TypeRelationship).where(
            TypeRelationship.source_type_id == power_tool.id,
            TypeRelationship.target_type_id == SYNTHETIC_ROOT_ID,
            TypeRelationship.relationship == "is_a",
            TypeRelationship.status == "active",
        )) is None
        assert db.scalar(select(TypeRelationship).where(
            TypeRelationship.source_type_id == impact_driver.id,
            TypeRelationship.target_type_id == power_tool.id,
            TypeRelationship.relationship == "is_a",
            TypeRelationship.status == "active",
        )) is not None


def test_multi_parent_and_part_of_edges_remain_independent():
    with _session() as db:
        leaf = ensure_subject_type(db, "shared component", created_by="pytest")[0]
        component = ensure_subject_type(db, "component", created_by="pytest")[0]
        product = ensure_subject_type(db, "product", created_by="pytest")[0]
        system = ensure_subject_type(db, "system", created_by="pytest")[0]

        add_semantic_relationship(db, leaf, "is_a", component, source="first-model")
        add_semantic_relationship(db, leaf, "is_a", product, source="second-model")
        add_semantic_relationship(db, leaf, "part_of", system, source="first-model")

        edges = set(db.execute(select(
            TypeRelationship.relationship, TypeRelationship.target_type_id
        ).where(
            TypeRelationship.source_type_id == leaf.id,
            TypeRelationship.status == "active",
        )).all())
        assert edges == {("is_a", component.id), ("is_a", product.id), ("part_of", system.id)}


def test_retiring_last_semantic_parent_restores_root_attachment():
    with _session() as db:
        child = ensure_subject_type(db, "child type", created_by="pytest")[0]
        parent = ensure_subject_type(db, "parent type", created_by="pytest")[0]
        add_semantic_relationship(db, child, "is_a", parent, source="pytest")

        retire_semantic_relationship(
            db, child, "is_a", parent, reason="test correction", retired_by="pytest"
        )

        assert db.scalar(select(TypeRelationship).where(
            TypeRelationship.source_type_id == child.id,
            TypeRelationship.relationship == "is_a",
            TypeRelationship.target_type_id == SYNTHETIC_ROOT_ID,
            TypeRelationship.status == "active",
        )) is not None


def test_infrastructure_root_edge_cannot_be_retired():
    with _session() as db:
        child = ensure_subject_type(db, "root child", created_by="pytest")[0]
        root = ensure_synthetic_root(db)

        with pytest.raises(ValueError, match="synthetic root"):
            retire_semantic_relationship(
                db, child, "is_a", root, reason="invalid", retired_by="pytest"
            )

        edge = db.scalar(select(TypeRelationship).where(
            TypeRelationship.source_type_id == child.id,
            TypeRelationship.target_type_id == root.id,
            TypeRelationship.relationship == "is_a",
        ))
        assert edge.status == "active"


def test_non_taxonomy_root_navigation_never_exposes_synthetic_root():
    with _session() as db:
        ensure_subject_type(db, "equipment", created_by="pytest")

        membership_roots = list_root_subject_types(db, relationship="part_of")

        assert [item["canonical_name"] for item in membership_roots["items"]] == ["equipment"]


def test_legacy_root_navigation_never_exposes_synthetic_root(monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_MODE", "legacy")
    get_settings.cache_clear()
    with _session() as db:
        ensure_synthetic_root(db)
        ensure_subject_type(db, "equipment", created_by="pytest")

        roots = list_root_subject_types(db)

        assert [item["canonical_name"] for item in roots["items"]] == ["equipment"]


def test_existing_type_lookup_does_not_commit_unrelated_pending_work():
    with _session() as db:
        ensure_subject_type(db, "equipment", created_by="pytest")
        pending = FieldDefinition(
            canonical_name="pending field",
            normalized_name="pending field",
            json_schema={"type": "string"},
            created_by="pytest",
        )
        db.add(pending)

        ensure_subject_type(db, "equipment", created_by="pytest")
        db.rollback()

        assert db.scalar(select(func.count()).select_from(FieldDefinition).where(
            FieldDefinition.normalized_name == "pending field"
        )) == 0


def test_deliberation_cannot_target_synthetic_root_in_structured_context():
    with _session() as db:
        ensure_synthetic_root(db)
        owner = User(display_name="Root deliberation test", profile_data={})
        db.add(owner)
        db.commit()
        db.refresh(owner)

        with pytest.raises(DeliberationError, match="synthetic root"):
            create_deliberation(
                db,
                DeliberationCreate(
                    canonical_key="invalid-root-deliberation",
                    title="Invalid root classification",
                    question="Should infrastructure be a classification?",
                    context={"classification": {"subject_type": "."}},
                ),
                owner_id=owner.id,
                client_id="pytest",
            )


def test_integrity_check_detects_orphan_semantic_types_and_illegal_subjects():
    with _session() as db:
        root = ensure_synthetic_root(db)
        orphan = SubjectType(
            id=uuid.uuid4(), canonical_name="orphan", normalized_name="orphan",
            status="provisional", created_by="broken-fixture",
        )
        db.add(orphan)
        db.flush()
        db.add(V2Subject(
            subject_type_id=root.id, owner_id=None, name="Illegal", canonical_key="illegal-root"
        ))
        db.commit()

        report = check_synthetic_root_integrity(db)

        assert report["valid"] is False
        assert report["orphan_semantic_roots"] == [
            {"id": str(orphan.id), "canonical_name": "orphan"}
        ]
        assert report["subjects_classified_as_root"] == 1


def test_administrative_vocabulary_export_surfaces_root_integrity():
    with _session() as db:
        ensure_subject_type(db, "equipment", created_by="pytest")

        report = vocabulary_index(db)["synthetic_root_integrity"]

        assert report["valid"] is True
        assert report["root_count"] == 1
        assert report["orphan_semantic_roots"] == []
