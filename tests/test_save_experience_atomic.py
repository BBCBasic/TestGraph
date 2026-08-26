import json
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.mcp_v2 import _save_experience
from app.core.security import Principal
from app.db.base import Base
from app.models.entities import IdempotencyRecord
from app.models.v2 import SubjectRelationship, V2Experience, V2Subject
from app.services.v2 import ensure_subject_type


SOURCE = "https://example.test/rockwealth/locations"


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


def _args(structured_data):
    return {
        "subject_type": "financial service",
        "subject_name": "Rockwealth Cotswolds",
        "canonical_key": "rockwealth-cotswolds-cirencester",
        "identifiers": {"website": "https://example.test/rockwealth/cotswolds"},
        "subject_attributes": {},
        "subject_provenance": {},
        "subject_enrichment_check": {
            "status": "completed",
            "sources": [SOURCE],
            "applied_fields": {
                SOURCE: [
                    "subject_context.subjects[0].identifiers.directory_url",
                    "subject_context.subjects[0].attributes.discovered_count",
                    "subject_context.relationships[0]",
                    "subject_context.relationships[1]",
                ],
            },
            "retrieval_uses": {
                "subject_context.subjects[0].identifiers.directory_url": {
                    "roles": ["relationship"],
                    "likely_queries": ["Rockwealth locations"],
                    "reason": "Supports future collection discovery.",
                },
                "subject_context.subjects[0].attributes.discovered_count": {
                    "roles": ["verification"],
                    "reason": "Supports collection completeness verification.",
                },
                "subject_context.relationships[0]": {
                    "roles": ["relationship"],
                    "likely_queries": ["Rockwealth Cotswolds"],
                    "reason": "Connects the reviewed branch to the collection.",
                },
                "subject_context.relationships[1]": {
                    "roles": ["relationship"],
                    "likely_queries": ["Rockwealth Cheltenham"],
                    "reason": "Connects the sibling branch to the collection.",
                },
            },
        },
        "collection_assessment": {
            "status": "member",
            "collection_name": "Rockwealth",
            "collection_type": "financial service",
            "directory_url": SOURCE,
            "discovered_count": 2,
            "submitted_member_refs": ["reviewed_subject", "cheltenham"],
            "source_manifest": {
                "coverage_status": "complete",
                "coverage_method": "single_page",
                "declared_source_count": 1,
                "source_pages": [{
                    "url": SOURCE,
                    "source_kind": "directory_page",
                    "member_refs": ["reviewed_subject", "cheltenham"],
                    "terminal": True,
                }],
                "discovery_queries": ["Rockwealth official locations"],
                "exhaustion_evidence": "The authoritative directory is a finite single-page list of all locations.",
                "unresolved_source_urls": [],
            },
            "evidence_sources": [SOURCE],
        },
        "headline": "Interesting first meeting",
        "summary": "Initial free consultation; minded to take this further.",
        "raw_text": (
            "We had a chat with Rock Wealth in Cirencester. Our initial free consultation "
            "with Nicola and Andy. Interesting first meeting. We are minded to take this further."
        ),
        "structured_data": structured_data,
        "subject_context": {
            "subjects": [
                {
                    "ref": "rockwealth",
                    "subject_type": "financial service",
                    "name": "Rockwealth",
                    "canonical_key": "rockwealth",
                    "identifiers": {"directory_url": SOURCE},
                    "attributes": {"discovered_count": 2},
                    "provenance": {"source": SOURCE},
                },
                {
                    "ref": "cheltenham",
                    "subject_type": "financial service",
                    "name": "Rockwealth Cheltenham",
                    "canonical_key": "rockwealth-cheltenham",
                    "identifiers": {},
                    "attributes": {},
                    "provenance": {"source": SOURCE},
                },
            ],
            "relationships": [
                {
                    "source_ref": "reviewed_subject",
                    "relationship": "belongs_to",
                    "target_ref": "rockwealth",
                    "provenance": {"source": SOURCE},
                },
                {
                    "source_ref": "cheltenham",
                    "relationship": "belongs_to",
                    "target_ref": "rockwealth",
                    "provenance": {"source": SOURCE},
                },
            ],
        },
        "visibility": "private",
        "user_approved": True,
        "idempotency_key": "rockwealth-regression-atomic-save",
    }


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_failed_save_experience_rolls_back_graph_and_retry_is_idempotent(db, principal):
    ensure_subject_type(db, "financial service", created_by="test")

    with pytest.raises(ValueError, match="not registered globally"):
        _save_experience(db, principal, _args({"consultation_type": "initial free consultation"}))

    # Emulate request/session failure cleanup. Before the fix, subjects and relationships
    # had already been committed and therefore survived this rollback.
    db.rollback()
    assert _count(db, V2Subject) == 0
    assert _count(db, SubjectRelationship) == 0
    assert _count(db, V2Experience) == 0
    assert _count(db, IdempotencyRecord) == 0

    valid_args = _args({})
    first = _save_experience(db, principal, valid_args)
    first_body = json.loads(first["content"][0]["text"])
    assert first_body["saved"] is True
    assert _count(db, V2Subject) == 3
    assert _count(db, SubjectRelationship) == 2
    assert _count(db, V2Experience) == 1
    assert _count(db, IdempotencyRecord) == 1

    replay = _save_experience(db, principal, valid_args)
    replay_body = json.loads(replay["content"][0]["text"])
    assert replay_body["experience_id"] == first_body["experience_id"]
    assert _count(db, V2Subject) == 3
    assert _count(db, SubjectRelationship) == 2
    assert _count(db, V2Experience) == 1
    assert _count(db, IdempotencyRecord) == 1
