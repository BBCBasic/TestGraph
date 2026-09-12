import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.mcp_v2 import _resolve, _search
from app.core.security import Principal
from app.db.base import Base
from app.models.v2 import SubjectTypeAlias, V2Experience, V2Subject
from app.services.semantic import resolve_subject_hierarchy
from app.services.v2 import resolve_subject_type
from app.services.vocabulary_navigation import (
    list_child_subject_types,
    list_root_subject_types,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_branch(db, path):
    return resolve_subject_hierarchy(db, path, created_by="pytest")


def _save_deep_review(db, subject_type, owner_id):
    subject = V2Subject(
        subject_type_id=subject_type.id,
        owner_id=owner_id,
        name="Deep target",
        canonical_key="deep-target",
        identifiers_json={},
        attributes_json={},
        provenance_json={},
    )
    db.add(subject)
    db.flush()
    db.add(V2Experience(
        owner_id=owner_id,
        subject_id=subject.id,
        headline="Deep target review",
        summary="Found only in the correct deep branch",
        raw_text="The controlled retrieval target.",
        structured_data={},
        submitted_data={},
        normalization_log=[],
        visibility="private",
        publication_status="published",
        provenance={},
        created_by_client="pytest",
    ))
    db.commit()
    return subject


def _controlled_progressive_retrieval(db, principal, ranked_roots):
    root_page = list_root_subject_types(db, limit=100)
    roots = {item["canonical_name"]: item for item in root_page["items"]}
    calls = 1
    visited = len(root_page["items"])
    backtracks = 0
    for root_name in ranked_roots:
        current = roots[root_name]
        while current["child_count"]:
            child_page = list_child_subject_types(db, current["canonical_name"], limit=100)
            calls += 1
            visited += len(child_page["items"])
            assert len(child_page["items"]) == 1
            current = child_page["items"][0]
        search = _search(db, principal, {
            "query": "Deep target",
            "subject_type": current["canonical_name"],
            "include_related": True,
            "limit": 10,
        })["structuredContent"]
        calls += 1
        if search["results"]:
            return {
                "result": search["results"][0],
                "backtracks": backtracks,
                "calls": calls,
                "taxonomy_nodes_returned": visited,
            }
        backtracks += 1
    return {"result": None, "backtracks": backtracks, "calls": calls, "taxonomy_nodes_returned": visited}


def test_first_ranked_branch_miss_backtracks_to_successful_deep_branch(monkeypatch):
    with _session() as db:
        wrong = _seed_branch(db, ["wrong root", "wrong branch", "wrong leaf"])
        correct = _seed_branch(db, ["correct root", "correct branch", "correct leaf"])
        owner_id = uuid.uuid4()
        _save_deep_review(db, correct["leaf"], owner_id)
        principal = Principal(
            subject="pytest", client_id="pytest", scopes={"reviews:read"}, user_id=owner_id,
        )
        monkeypatch.setattr(
            "app.api.mcp_v2.vocabulary_index",
            lambda _db: (_ for _ in ()).throw(AssertionError("full vocabulary must not be fetched")),
        )

        trace = _controlled_progressive_retrieval(
            db, principal, [wrong["path"][0]["canonical_name"], correct["path"][0]["canonical_name"]],
        )

        assert trace["result"]["subject_name"] == "Deep target"
        assert trace["backtracks"] == 1
        assert trace["taxonomy_nodes_returned"] == 6


def test_existing_alias_resolves_without_any_vocabulary_download(monkeypatch):
    with _session() as db:
        hierarchy = _seed_branch(db, ["entity", "object", "camera"])
        camera = hierarchy["leaf"]
        db.add(SubjectTypeAlias(
            subject_type_id=camera.id,
            alias="photographic camera",
            normalized_alias="photographic camera",
            source="pytest",
        ))
        db.commit()
        monkeypatch.setattr(
            "app.api.mcp_v2.vocabulary_index",
            lambda _db: (_ for _ in ()).throw(AssertionError("full vocabulary must not be fetched")),
        )

        result = _resolve(db, {"term": "photographic cameras"})["structuredContent"]

        assert result["found"] is True
        assert result["id"] == str(camera.id)
        assert result["canonical_name"] == "camera"


def test_genuinely_new_leaf_is_created_below_verified_existing_path():
    with _session() as db:
        existing = _seed_branch(db, ["entity", "object"])
        roots = list_root_subject_types(db)
        entity = next(item for item in roots["items"] if item["canonical_name"] == "entity")
        children = list_child_subject_types(db, entity["canonical_name"])
        assert [item["canonical_name"] for item in children["items"]] == ["object"]

        result = resolve_subject_hierarchy(
            db, ["entity", "object", "test navigation instrument"], created_by="model-a",
        )

        assert result["path"][0]["id"] == existing["path"][0]["id"]
        assert result["path"][1]["id"] == existing["path"][1]["id"]
        assert result["created_terms"] == ["test navigation instrument"]
        assert resolve_subject_type(db, "test navigation instrument").id == result["leaf"].id
