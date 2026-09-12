import json
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.models.v2 import SubjectType, TypeRelationship
from app.schemas.v2 import SubjectEnsure
from app.services.semantic import add_semantic_relationship
from app.services.v2 import ensure_subject, ensure_subject_type, resolve_subject_type
from scripts.compare_classification_modes import build_comparison


REPRESENTATIVE_PATTERNS = [
    ("model railway component", "component", "model railway"),
    ("model railway track", "model railway component", "model railway"),
    ("model train", "toy", "model railway"),
    ("car tyre", "vehicle component", "car"),
    ("camera lens", "optical component", "camera system"),
    ("chess piece", "game component", "chess"),
    ("pipe organ key", "keyboard component", "pipe organ"),
]


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _type(db: Session, name: str) -> SubjectType:
    return ensure_subject_type(db, name, created_by="pytest")[0]


@pytest.fixture()
def typed_mode(monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_MODE", "typed")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(("leaf_name", "parent_name", "system_name"), REPRESENTATIVE_PATTERNS)
def test_one_stable_type_participates_in_taxonomy_and_membership(
    typed_mode, leaf_name, parent_name, system_name,
):
    with _session() as db:
        leaf = _type(db, leaf_name)
        parent = _type(db, parent_name)
        system = _type(db, system_name)

        taxonomy = add_semantic_relationship(db, leaf, "is_a", parent, source="pytest")
        membership = add_semantic_relationship(db, leaf, "part_of", system, source="pytest")

        assert leaf.canonical_name == leaf_name
        assert taxonomy.source_type_id == membership.source_type_id == leaf.id
        assert taxonomy.relationship == "is_a"
        assert membership.relationship == "part_of"
        assert db.scalar(select(func.count()).select_from(SubjectType).where(
            SubjectType.normalized_name == leaf.normalized_name,
        )) == 1


def test_descriptive_modifiers_remain_subject_attributes_not_type_nodes(typed_mode):
    with _session() as db:
        needle = _type(db, "needle")
        track = _type(db, "model railway track")
        blunt = ensure_subject(db, SubjectEnsure(
            subject_type="needle",
            name="Blunt needle",
            canonical_key="blunt-needle-fixture",
            attributes={"condition": "blunt"},
        ))
        pack = ensure_subject(db, SubjectEnsure(
            subject_type="model railway track",
            name="20 pieces of Trix track",
            canonical_key="trix-track-fixture",
            attributes={"brand": "Trix", "quantity": 20},
        ))

        names = set(db.scalars(select(SubjectType.normalized_name)).all())
        assert blunt.subject_type_id == needle.id
        assert pack.subject_type_id == track.id
        assert blunt.attributes_json == {"condition": "blunt"}
        assert pack.attributes_json == {"brand": "Trix", "quantity": 20}
        assert "blunt needle" not in names
        assert "20 pieces of trix track" not in names
        assert "trix" not in names


def test_rest_relationship_write_uses_typed_semantic_safeguards(client, auth, typed_mode):
    with _session() as isolated:
        assert isolated.scalar(select(func.count()).select_from(TypeRelationship)) == 0

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        token = str(db.scalar(select(func.count()).select_from(SubjectType)))
        source = _type(db, f"typed rest source {token}")
        target = _type(db, f"typed rest target {token}")
        source_name = source.canonical_name
        target_name = target.canonical_name

    ambiguous = client.post("/api/v2/relationships", headers=auth, json={
        "source_type": source_name,
        "relationship": "belongs_to",
        "target_type": target_name,
    })
    explicit = client.post("/api/v2/relationships", headers=auth, json={
        "source_type": source_name,
        "relationship": "is_a",
        "target_type": target_name,
    })

    assert ambiguous.status_code == 422
    assert "choose 'is_a' or 'part_of'" in ambiguous.json()["detail"]
    assert explicit.status_code == 201
    assert explicit.json()["relationship"] == "is_a"


def test_controlled_comparison_reports_legacy_loss_and_typed_semantic_paths():
    comparison = build_comparison()

    assert comparison["legacy"]["classification_mode"] == "legacy"
    assert comparison["typed"]["classification_mode"] == "typed"
    assert set(comparison["legacy"]["active_edge_types"]) == {"belongs_to"}
    assert set(comparison["typed"]["active_edge_types"]) == {"is_a", "part_of"}
    assert comparison["legacy"]["membership_edges"] == []
    assert comparison["typed"]["membership_edges"]
    assert comparison["typed"]["stable_identity_reused"] is True
    assert comparison["typed"]["modifier_nodes_created"] == []


def test_comparison_script_runs_from_the_documented_command():
    completed = subprocess.run(
        [sys.executable, "scripts/compare_classification_modes.py", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert set(json.loads(completed.stdout)) == {"legacy", "typed"}
