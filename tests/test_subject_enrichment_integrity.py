import json
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.mcp_v2 import _correct_subject_fact, _enrich_subject
from app.core.security import Principal
from app.db.base import Base
from app.models.v2 import V2Subject
from app.schemas.v2 import SubjectEnsure
from app.services.v2 import _deep_fill_missing, ensure_subject, ensure_subject_type


SOURCE = "https://example.test/subject"


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def principal():
    return Principal(
        subject="test-user", client_id="test-client",
        scopes={"reviews:write"}, user_id=uuid.uuid4(),
    )


def _payload(result):
    return json.loads(result["content"][0]["text"])


def _subject(db, principal):
    ensure_subject_type(db, "cafe", created_by="test")
    return ensure_subject(
        db,
        SubjectEnsure(
            subject_type="cafe", name="Example Cafe", canonical_key="example-cafe",
            identifiers={"address": {"postcode": "GL5"}},
            attributes={}, provenance={},
        ),
        owner_id=principal.user_id,
    )


def _enrichment_args(subject, key, identifiers):
    return {
        "subject_id": str(subject.id),
        "identifiers": identifiers,
        "attributes": {},
        "provenance": {},
        "subject_context": {"subjects": [], "relationships": []},
        "subject_enrichment_check": {
            "status": "completed",
            "sources": [SOURCE],
            "applied_fields": {SOURCE: ["identifiers.address"]},
            "retrieval_uses": {
                "identifiers.address": {
                    "roles": ["location"],
                    "likely_queries": ["Example Cafe in Stroud"],
                    "reason": "Supports likely place-and-location searches.",
                }
            },
        },
        "collection_assessment": {
            "status": "independent",
            "evidence_sources": [SOURCE],
        },
        "idempotency_key": key,
    }


def test_deep_fill_adds_nested_leaves_and_preserves_conflicts():
    merged, added, conflicts = _deep_fill_missing(
        {"address": {"postcode": "GL5"}},
        {"address": {"postcode": "GL6", "town": "Stroud"}},
        prefix="identifiers",
    )

    assert merged == {"address": {"postcode": "GL5", "town": "Stroud"}}
    assert added == ["identifiers.address.town"]
    assert conflicts == [{
        "path": "identifiers.address.postcode",
        "existing": "GL5", "incoming": "GL6",
    }]


def test_enrichment_uses_subject_id_deep_merges_and_reports_real_changes(db, principal):
    subject = _subject(db, principal)
    result = _enrich_subject(
        db, principal,
        _enrichment_args(
            subject, "nested-enrichment-1",
            {"address": {"postcode": "GL6", "town": "Stroud"}},
        ),
    )
    body = _payload(result)

    assert body["changed"] is True
    assert body["enriched"] is True
    assert body["fields_added"] == ["identifiers.address.town"]
    assert body["conflicts_preserved"] == [{
        "path": "identifiers.address.postcode",
        "existing": "GL5", "incoming": "GL6",
    }]
    db.refresh(subject)
    assert subject.identifiers_json["address"] == {
        "postcode": "GL5", "town": "Stroud",
    }


def test_noop_enrichment_reports_changed_false(db, principal):
    subject = _subject(db, principal)
    result = _enrich_subject(
        db, principal,
        _enrichment_args(
            subject, "noop-enrichment-1",
            {"address": {"postcode": "GL5"}},
        ),
    )
    body = _payload(result)

    assert body["changed"] is False
    assert body["enriched"] is False
    assert body["fields_added"] == []
    assert body["conflicts_preserved"] == []


def test_exact_key_miss_returns_candidates_without_creating_subject(db, principal):
    subject = _subject(db, principal)
    args = _enrichment_args(subject, "candidate-enrichment-1", {"website": SOURCE})
    args.pop("subject_id")
    args["subject_type"] = "cafe"
    args["canonical_key"] = "example"
    result = _enrich_subject(db, principal, args)
    body = _payload(result)

    assert result["isError"] is True
    assert body["details"]["code"] == "subject_not_found"
    assert body["details"]["candidates"][0]["subject_id"] == str(subject.id)
    assert len(list(db.scalars(select(V2Subject)).all())) == 1


def test_collection_mismatch_is_rejected_before_related_subject_write(db, principal):
    subject = _subject(db, principal)
    ensure_subject_type(db, "cafe group", created_by="test")
    args = {
        "subject_id": str(subject.id),
        "identifiers": {"website": SOURCE},
        "attributes": {},
        "provenance": {},
        "subject_context": {
            "subjects": [{
                "ref": "group", "subject_type": "cafe group",
                "name": "Example Group", "canonical_key": "example-group",
                "identifiers": {"directory_url": SOURCE},
                "attributes": {"discovered_count": 2},
                "provenance": {"source_url": SOURCE},
            }],
            "relationships": [{
                "source_ref": "subject", "relationship": "branch_of",
                "target_ref": "group", "provenance": {"source_url": SOURCE},
            }],
        },
        "subject_enrichment_check": {
            "status": "completed", "sources": [SOURCE],
            "applied_fields": {
                SOURCE: [
                    "identifiers.website",
                    "subject_context.subjects[0].identifiers.directory_url",
                    "subject_context.subjects[0].attributes.discovered_count",
                    "subject_context.relationships[0]",
                ],
            },
            "retrieval_uses": {
                "identifiers.website": {
                    "roles": ["identity"],
                    "likely_queries": ["Example Cafe official website"],
                    "reason": "Provides the canonical subject locator.",
                },
                "subject_context.subjects[0].identifiers.directory_url": {
                    "roles": ["relationship"],
                    "likely_queries": ["Example Group locations"],
                    "reason": "Enables later collection expansion.",
                },
                "subject_context.subjects[0].attributes.discovered_count": {
                    "roles": ["verification"],
                    "reason": "Supports server verification of collection completeness.",
                },
                "subject_context.relationships[0]": {
                    "roles": ["relationship"],
                    "likely_queries": ["Example Group cafe in Stroud"],
                    "reason": "Connects the target to its searchable collection.",
                },
            },
        },
        "collection_assessment": {
            "status": "member", "collection_name": "Example Group",
            "collection_type": "cafe group", "directory_url": SOURCE,
            "discovered_count": 2, "submitted_member_refs": ["subject"],
            "source_manifest": {
                "coverage_method": "single_page",
                "declared_source_count": 1,
                "source_pages": [{
                    "url": SOURCE, "source_kind": "directory_page",
                    "member_refs": ["subject"], "terminal": True,
                }],
                "discovery_queries": ["Example Group official locations"],
                "exhaustion_evidence": "The authoritative list is finite and single-page.",
            },
            "evidence_sources": [SOURCE],
        },
        "idempotency_key": "collection-enrichment-1",
    }
    result = _enrich_subject(db, principal, args)
    body = _payload(result)

    assert result["isError"] is True
    assert body["details"]["code"] == "collection_member_count_mismatch"
    assert body["details"]["retry_tool"] == "enrich_subject"
    assert db.scalar(select(V2Subject).where(
        V2Subject.canonical_key == "example-group"
    )) is None


def test_subject_correction_requires_expected_value_and_records_evidence(db, principal):
    subject = _subject(db, principal)
    result = _correct_subject_fact(
        db, principal,
        {
            "subject_id": str(subject.id),
            "field_root": "identifiers",
            "field_path": "address.postcode",
            "expected_value": "GL5",
            "corrected_value": "GL6",
            "evidence_sources": [SOURCE],
            "reason": "The authoritative listing was updated.",
            "idempotency_key": "subject-correction-1",
        },
    )
    body = _payload(result)

    assert body["corrected"] is True
    db.refresh(subject)
    assert subject.identifiers_json["address"]["postcode"] == "GL6"
    record = subject.provenance_json["subject_corrections"]["subject-correction-1"]
    assert record["previous_value"] == "GL5"
    assert record["corrected_value"] == "GL6"
    assert record["evidence_sources"] == [SOURCE]


def test_subject_correction_conflict_does_not_overwrite(db, principal):
    subject = _subject(db, principal)
    result = _correct_subject_fact(
        db, principal,
        {
            "subject_id": str(subject.id),
            "field_root": "identifiers",
            "field_path": "address.postcode",
            "expected_value": "WRONG",
            "corrected_value": "GL6",
            "evidence_sources": [SOURCE],
            "reason": "Test conflict.",
            "idempotency_key": "subject-correction-2",
        },
    )
    body = _payload(result)

    assert result["isError"] is True
    assert body["details"]["code"] == "subject_correction_conflict"
    db.refresh(subject)
    assert subject.identifiers_json["address"]["postcode"] == "GL5"
