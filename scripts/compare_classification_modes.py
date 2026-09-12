from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.models.v2 import SubjectType, TypeRelationship
from app.schemas.v2 import SubjectEnsure
from app.services.semantic import add_semantic_relationship
from app.services.v2 import ensure_subject, ensure_subject_type
from app.services.vocabulary_navigation import get_subject_type_paths


PATTERNS = [
    ("model railway component", "component", "model railway"),
    ("model railway track", "model railway component", "model railway"),
    ("model train", "toy", "model railway"),
    ("car tyre", "vehicle component", "car"),
    ("camera lens", "optical component", "camera system"),
    ("chess piece", "game component", "chess"),
    ("pipe organ key", "keyboard component", "pipe organ"),
]


@contextmanager
def _selected_mode(mode: str):
    previous = os.environ.get("CLASSIFICATION_MODE")
    os.environ["CLASSIFICATION_MODE"] = mode
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CLASSIFICATION_MODE", None)
        else:
            os.environ["CLASSIFICATION_MODE"] = previous
        get_settings.cache_clear()


def _type(db: Session, name: str) -> SubjectType:
    return ensure_subject_type(db, name, created_by="classification-mode-comparison")[0]


def _serialize_edges(db: Session) -> list[dict]:
    types = {item.id: item.canonical_name for item in db.scalars(select(SubjectType)).all()}
    edges = db.scalars(select(TypeRelationship).where(
        TypeRelationship.status == "active",
    )).all()
    return sorted(
        ({
            "source_id": str(edge.source_type_id),
            "source": types[edge.source_type_id],
            "relationship": edge.relationship,
            "target_id": str(edge.target_type_id),
            "target": types[edge.target_type_id],
        } for edge in edges),
        key=lambda edge: (edge["source"], edge["relationship"], edge["target"]),
    )


def _run_mode(mode: str) -> dict:
    with _selected_mode(mode):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            node_ids: dict[str, str] = {}
            for leaf_name, parent_name, system_name in PATTERNS:
                leaf = _type(db, leaf_name)
                parent = _type(db, parent_name)
                system = _type(db, system_name)
                node_ids[leaf_name] = str(leaf.id)
                node_ids[parent_name] = str(parent.id)
                node_ids[system_name] = str(system.id)
                if mode == "typed":
                    add_semantic_relationship(db, leaf, "is_a", parent, source="comparison")
                    add_semantic_relationship(db, leaf, "part_of", system, source="comparison")
                else:
                    add_semantic_relationship(db, leaf, "belongs_to", parent, source="comparison")

            if mode == "typed":
                add_semantic_relationship(
                    db, _type(db, "model railway"), "is_a", _type(db, "toy"), source="comparison",
                )

            needle = _type(db, "needle")
            track = _type(db, "model railway track")
            blunt_subject = ensure_subject(db, SubjectEnsure(
                subject_type=needle.canonical_name,
                name="Blunt needle",
                canonical_key="comparison-blunt-needle",
                attributes={"condition": "blunt"},
            ))
            pack_subject = ensure_subject(db, SubjectEnsure(
                subject_type=track.canonical_name,
                name="20 pieces of Trix track",
                canonical_key="comparison-trix-track",
                attributes={"brand": "Trix", "quantity": 20},
            ))

            edges = _serialize_edges(db)
            taxonomy_paths = {
                leaf_name: [
                    [node["canonical_name"] for node in path]
                    for path in get_subject_type_paths(db, leaf_name)["paths"]
                ]
                for leaf_name, _, _ in PATTERNS
            }
            names = set(db.scalars(select(SubjectType.normalized_name)).all())
            membership_edges = [edge for edge in edges if edge["relationship"] == "part_of"]
            stable_identity_reused = all(
                edge["source_id"] == node_ids[edge["source"]]
                for edge in edges
                if edge["source"] in node_ids
            )
            return {
                "classification_mode": mode,
                "node_count": len(names),
                "node_ids": node_ids,
                "active_edge_count": len(edges),
                "active_edge_types": sorted({edge["relationship"] for edge in edges}),
                "active_edges": edges,
                "taxonomy_paths": taxonomy_paths,
                "membership_edges": membership_edges,
                "stable_identity_reused": stable_identity_reused,
                "modifier_nodes_created": sorted(names & {
                    "blunt needle", "20 pieces of trix track", "trix",
                }),
                "modifier_attributes": {
                    blunt_subject.name: blunt_subject.attributes_json,
                    pack_subject.name: pack_subject.attributes_json,
                },
            }


def build_comparison() -> dict:
    return {mode: _run_mode(mode) for mode in ("legacy", "typed")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare legacy and typed classification semantics.")
    parser.add_argument("--format", choices=("json",), default="json")
    parser.parse_args()
    print(json.dumps(build_comparison(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
