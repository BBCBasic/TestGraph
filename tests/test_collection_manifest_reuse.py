import json
import uuid
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.mcp_v2 import _save_experience, _search
from app.core.security import Principal
from app.db.base import Base
from app.models.v2 import V2Subject
from app.services.v2 import ensure_subject_type


DIRECTORY = "https://example.test/locations"


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ensure_subject_type(session, "cafe", created_by="test")
        ensure_subject_type(session, "organization", created_by="test")
        yield session


@pytest.fixture()
def principal():
    return Principal(
        subject="test-user", client_id="test-client",
        scopes={"reviews:write"}, user_id=uuid.uuid4(),
    )


def _payload(result):
    return json.loads(result["content"][0]["text"])


def _base_save(subject_name, canonical_key, idempotency_key):
    source = f"https://example.test/{canonical_key}"
    return {
        "subject_type": "cafe",
        "subject_name": subject_name,
        "canonical_key": canonical_key,
        "identifiers": {},
        "subject_attributes": {},
        "subject_provenance": {},
        "subject_enrichment_check": {
            "status": "completed",
            "sources": [source],
            "unapplied_sources": {
                source: "No additional reusable subject fact was found."
            },
        },
        "headline": "Test review",
        "summary": "Test summary",
        "raw_text": "Test review text",
        "structured_data": {},
        "visibility": "private",
        "user_approved": True,
        "idempotency_key": idempotency_key,
    }


def _initial_collection_save():
    args = _base_save(
        "Example Group Central", "example-group-central", "initial-manifest-1"
    )
    args["subject_enrichment_check"] = {
        "status": "completed",
        "sources": [DIRECTORY],
        "unapplied_sources": {
            DIRECTORY: "Used as the authoritative collection evidence."
        },
    }
    args["subject_context"] = {
        "subjects": [
            {
                "ref": "group", "subject_type": "organization",
                "name": "Example Group", "canonical_key": "example-group",
                "identifiers": {"branch_directory": DIRECTORY},
                "attributes": {"discovered_count": 3},
                "provenance": {"source_url": DIRECTORY},
            },
            {
                "ref": "north", "subject_type": "cafe",
                "name": "Example Group North", "canonical_key": "example-group-north",
                "provenance": {"source_url": DIRECTORY},
            },
            {
                "ref": "south", "subject_type": "cafe",
                "name": "Example Group South", "canonical_key": "example-group-south",
                "provenance": {"source_url": DIRECTORY},
            },
        ],
        "relationships": [
            {
                "source_ref": ref, "relationship": "branch_of",
                "target_ref": "group", "provenance": {"source_url": DIRECTORY},
            }
            for ref in ("reviewed_subject", "north", "south")
        ],
    }
    member_refs = ["reviewed_subject", "north", "south"]
    args["collection_assessment"] = {
        "status": "member",
        "collection_name": "Example Group",
        "collection_type": "business_chain",
        "directory_url": DIRECTORY,
        "discovered_count": 3,
        "submitted_member_refs": member_refs,
        "source_manifest": {
            "coverage_status": "complete",
            "coverage_method": "single_page",
            "declared_source_count": 1,
            "source_pages": [{
                "url": DIRECTORY,
                "source_kind": "directory_page",
                "member_refs": member_refs,
                "terminal": True,
            }],
            "discovery_queries": ["Example Group official locations"],
            "exhaustion_evidence": "The authoritative directory is a finite list.",
        },
        "evidence_sources": [DIRECTORY],
    }
    return args


def test_later_review_reuses_verified_manifest_without_resubmitting_members(db, principal):
    first = _payload(_save_experience(db, principal, _initial_collection_save()))
    reference = first["collection_reference"]

    assert reference["manifest_revision"] == 1
    assert reference["discovered_count"] == 3
    collection = db.get(V2Subject, uuid.UUID(reference["collection_id"]))
    stored = collection.provenance_json["testgraph_collection_manifest"]
    assert stored["status"] == "verified"
    assert len(stored["member_subject_ids"]) == 3

    later = _base_save(
        "Example Group North", "example-group-north", "reuse-manifest-2"
    )
    later["subject_context"] = {"subjects": [], "relationships": []}
    later["collection_assessment"] = {
        "status": "member",
        "collection_id": reference["collection_id"],
        "manifest_revision": reference["manifest_revision"],
    }

    result = _save_experience(db, principal, later)
    body = _payload(result)

    assert result.get("isError") is not True
    assert body["saved"] is True
    assert body["collection_reference"] == reference
    assert body["collection_assessment"]["submitted_member_refs"] == []
    assert body["collection_assessment"]["source_manifest"] is None


def test_stored_manifest_rejects_subject_that_is_not_a_member(db, principal):
    first = _payload(_save_experience(db, principal, _initial_collection_save()))
    reference = first["collection_reference"]

    outsider = _base_save("Outside Cafe", "outside-cafe", "reuse-manifest-3")
    outsider["subject_context"] = {"subjects": [], "relationships": []}
    outsider["collection_assessment"] = {
        "status": "member",
        "collection_id": reference["collection_id"],
        "manifest_revision": reference["manifest_revision"],
    }

    result = _save_experience(db, principal, outsider)
    body = _payload(result)

    assert result["isError"] is True
    assert body["details"]["code"] == "subject_not_in_collection_manifest"
    assert db.scalar(select(V2Subject).where(
        V2Subject.canonical_key == "outside-cafe"
    )) is None


def test_existing_full_save_is_backfilled_and_reused_without_resubmission(db, principal):
    first = _payload(_save_experience(db, principal, _initial_collection_save()))
    reference = first["collection_reference"]
    collection = db.get(V2Subject, uuid.UUID(reference["collection_id"]))

    provenance = deepcopy(collection.provenance_json)
    provenance.pop("testgraph_collection_manifest")
    collection.provenance_json = provenance
    db.commit()

    later = _base_save(
        "Example Group South", "example-group-south", "legacy-manifest-4"
    )
    later["subject_context"] = {"subjects": [], "relationships": []}
    later["collection_assessment"] = {
        "status": "member",
        "collection_name": "Example Group",
        "directory_url": DIRECTORY,
    }

    result = _save_experience(db, principal, later)
    body = _payload(result)

    assert result.get("isError") is not True
    assert body["saved"] is True
    assert body["collection_reference"]["collection_id"] == reference["collection_id"]
    db.refresh(collection)
    backfilled = collection.provenance_json["testgraph_collection_manifest"]
    assert backfilled["legacy_backfill"] is True
    assert backfilled["discovered_count"] == 3


def test_legacy_partial_manifest_cannot_support_tetbury_absence_or_reuse(db, principal):
    first = _payload(_save_experience(db, principal, _initial_collection_save()))
    reference = first["collection_reference"]
    collection = db.get(V2Subject, uuid.UUID(reference["collection_id"]))

    provenance = deepcopy(collection.provenance_json)
    stored = provenance["testgraph_collection_manifest"]
    stored.pop("verification_status", None)
    stored.pop("coverage_status", None)
    stored.pop("absence_claim_allowed", None)
    stored["source_manifest"].pop("coverage_status", None)
    stored["source_manifest"]["exhaustion_evidence"] = (
        "This is a known-partial manifest and is not exhaustive."
    )
    collection.provenance_json = provenance
    db.commit()

    search = _payload(_search(
        db, principal,
        {"query": "Example Group", "subject_type": "organization", "limit": 10},
    ))
    matched = next(
        item for item in search["known_subjects"]
        if item["id"] == reference["collection_id"]
    )
    assert matched["collection_coverage"] == {
        "verification_status": "verified",
        "coverage_status": "partial",
        "absence_claim_allowed": False,
        "warning": (
            "Collection coverage is partial; stored absence cannot establish that a "
            "location or member does not exist in the real world."
        ),
    }
    public_state = matched["provenance"]["testgraph_collection_manifest"]
    assert public_state["coverage_status"] == "partial"
    assert public_state["absence_claim_allowed"] is False

    later = _base_save(
        "Example Group North", "example-group-north", "partial-manifest-reuse-5"
    )
    later["subject_context"] = {"subjects": [], "relationships": []}
    later["collection_assessment"] = {
        "status": "member",
        "collection_id": reference["collection_id"],
        "manifest_revision": reference["manifest_revision"],
    }
    result = _save_experience(db, principal, later)
    body = _payload(result)

    assert result["isError"] is True
    assert body["details"]["code"] == "collection_manifest_coverage_incomplete"
    assert body["details"]["coverage_status"] == "partial"
    assert body["details"]["absence_claim_allowed"] is False

