import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.mcp_v2 import _validate_subject_context
from app.db.base import Base
from app.models.v2 import V2Subject
from app.services.v2 import ensure_subject_type


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _payload(error):
    return json.loads(error["content"][0]["text"])


def test_unknown_context_type_is_rejected_before_any_subject_write(db):
    ensure_subject_type(db, "cafe", created_by="test")
    context, error = _validate_subject_context(
        db,
        {
            "subjects": [
                {
                    "ref": "companion",
                    "subject_type": "person",
                    "name": "Stacy",
                    "canonical_key": "stacy",
                }
            ],
            "relationships": [
                {
                    "source_ref": "companion",
                    "relationship": "accompanied",
                    "target_ref": "reviewed_subject",
                }
            ],
        },
    )

    payload = _payload(error)
    assert context is None
    assert payload["details"]["code"] == "subject_context_types_unresolved"
    assert payload["details"]["unknown_subject_types"] == ["person"]
    assert "resolve_subject_hierarchy" in payload["details"]["instruction"]
    assert list(db.scalars(select(V2Subject)).all()) == []


def test_context_rejects_reserved_duplicate_and_unknown_relationship_refs(db):
    ensure_subject_type(db, "organization", created_by="test")
    context, error = _validate_subject_context(
        db,
        {
            "subjects": [
                {
                    "ref": "subject",
                    "subject_type": "organization",
                    "name": "Example Group",
                    "canonical_key": "example-group",
                }
            ],
            "relationships": [
                {
                    "source_ref": "missing",
                    "relationship": "branch_of",
                    "target_ref": "subject",
                }
            ],
        },
    )

    payload = _payload(error)
    assert context is None
    assert payload["details"]["code"] == "subject_context_references_invalid"
    assert payload["details"]["reserved_or_duplicate_refs"] == ["subject"]
    assert payload["details"]["invalid_relationships"][0]["missing_refs"] == ["missing"]


def test_valid_resolved_context_is_returned_for_the_write_phase(db):
    ensure_subject_type(db, "organization", created_by="test")
    context, error = _validate_subject_context(
        db,
        {
            "subjects": [
                {
                    "ref": "brand",
                    "subject_type": "organization",
                    "name": "Example Group",
                    "canonical_key": "example-group",
                }
            ],
            "relationships": [
                {
                    "source_ref": "reviewed_subject",
                    "relationship": "branch_of",
                    "target_ref": "brand",
                }
            ],
        },
    )

    assert error is None
    assert context.subjects[0].ref == "brand"
    assert context.relationships[0].target_ref == "brand"


def test_invalid_context_schema_returns_a_targeted_error(db):
    context, error = _validate_subject_context(db, {"subjects": "not-a-list"})

    payload = _payload(error)
    assert context is None
    assert payload["details"]["code"] == "subject_context_invalid"
