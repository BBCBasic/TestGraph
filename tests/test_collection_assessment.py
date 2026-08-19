import json

import pytest

from app.api.mcp_v2 import _validate_collection_assessment
from app.schemas.v2 import SubjectEnrichmentCheck


DIRECTORY = "https://example.test/locations"


def _payload(error):
    return json.loads(error["content"][0]["text"])


def _completed_enrichment(*sources):
    return SubjectEnrichmentCheck(status="completed", sources=list(sources))


def _member_args():
    return {
        "subject_context": {
            "subjects": [
                {
                    "ref": "brand",
                    "subject_type": "organization",
                    "name": "Example Group",
                    "canonical_key": "example-group",
                    "identifiers": {"branch_directory": DIRECTORY},
                    "attributes": {"reported_member_count": 44},
                    "provenance": {"source_url": DIRECTORY},
                }
            ],
            "relationships": [
                {
                    "source_ref": "reviewed_subject",
                    "relationship": "branch_of",
                    "target_ref": "brand",
                    "provenance": {"source_url": DIRECTORY},
                }
            ],
        }
    }


def test_missing_collection_assessment_blocks_with_retry_instruction():
    assessment, error = _validate_collection_assessment(
        None, {}, _completed_enrichment("https://example.test/about")
    )

    assert assessment is None
    payload = _payload(error)
    assert payload["details"]["status"] == "action_required"
    assert payload["details"]["code"] == "collection_assessment_required"
    assert payload["details"]["retry_tool"] == "save_experience"


def test_collection_member_requires_identity_directory_and_count():
    assessment, error = _validate_collection_assessment(
        {"status": "member"}, {}, _completed_enrichment()
    )

    assert assessment is None
    payload = _payload(error)
    assert payload["details"]["code"] == "collection_member_details_required"
    assert set(payload["details"]["missing_fields"]) == {
        "collection_name", "collection_type", "directory_url", "reported_member_count"
    }


def test_collection_member_requires_directory_to_be_reconciled():
    raw = {
        "status": "member",
        "collection_name": "Example Group",
        "collection_type": "business_chain",
        "directory_url": DIRECTORY,
        "reported_member_count": 44,
    }

    assessment, error = _validate_collection_assessment(
        raw, _member_args(), _completed_enrichment("https://example.test/about")
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_directory_source_required"


def test_valid_member_saves_collection_lazily():
    raw = {
        "status": "member",
        "collection_name": "Example Group",
        "collection_type": "business_chain",
        "directory_url": DIRECTORY,
        "reported_member_count": 44,
        "evidence_sources": [DIRECTORY],
    }

    assessment, error = _validate_collection_assessment(
        raw, _member_args(), _completed_enrichment(DIRECTORY)
    )

    assert error is None
    assert assessment.status == "member"
    assert assessment.reported_member_count == 44
    assert len(_member_args()["subject_context"]["subjects"]) == 1


@pytest.mark.parametrize(
    ("args", "expected_code"),
    [
        (
            {
                "subject_context": {
                    "subjects": [{
                        "ref": "brand", "subject_type": "organization",
                        "name": "Example Group", "canonical_key": "example-group",
                        "identifiers": {},
                    }],
                    "relationships": [{
                        "source_ref": "reviewed_subject",
                        "relationship": "branch_of",
                        "target_ref": "brand",
                    }],
                }
            },
            "collection_directory_not_stored",
        ),
        (
            {
                "subject_context": {
                    "subjects": [{
                        "ref": "brand", "subject_type": "organization",
                        "name": "Example Group", "canonical_key": "example-group",
                        "identifiers": {"branch_directory": DIRECTORY},
                        "attributes": {"reported_member_count": 44},
                    }],
                    "relationships": [],
                }
            },
            "collection_relationship_required",
        ),
        (
            {
                "subject_context": {
                    "subjects": [{
                        "ref": "brand", "subject_type": "organization",
                        "name": "Example Group", "canonical_key": "example-group",
                        "identifiers": {"branch_directory": DIRECTORY},
                        "attributes": {},
                    }],
                    "relationships": [{
                        "source_ref": "reviewed_subject",
                        "relationship": "branch_of",
                        "target_ref": "brand",
                    }],
                }
            },
            "collection_member_count_not_stored",
        ),
    ],
)
def test_member_context_must_store_directory_and_relationship(args, expected_code):
    raw = {
        "status": "member",
        "collection_name": "Example Group",
        "collection_type": "business_chain",
        "directory_url": DIRECTORY,
        "reported_member_count": 44,
    }

    assessment, error = _validate_collection_assessment(
        raw, args, _completed_enrichment(DIRECTORY)
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == expected_code


def test_independent_requires_evidence_or_search_attempts():
    assessment, error = _validate_collection_assessment(
        {"status": "independent"}, {}, _completed_enrichment()
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_independent_evidence_required"


def test_independent_rejects_conflicting_collection_signals():
    assessment, error = _validate_collection_assessment(
        {
            "status": "independent",
            "attempts": ["Checked the official website for group membership"],
        },
        {"identifiers": {"brand": "Example Group"}},
        _completed_enrichment(),
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_assessment_inconsistent"


def test_independent_with_search_record_can_continue():
    assessment, error = _validate_collection_assessment(
        {
            "status": "independent",
            "attempts": ["Checked the official website and publisher directory"],
        },
        {},
        _completed_enrichment(),
    )

    assert error is None
    assert assessment.status == "independent"


def test_unavailable_requires_reason_and_attempts():
    assessment, error = _validate_collection_assessment(
        {"status": "unavailable", "reason": "No reliable source was available"},
        {},
        SubjectEnrichmentCheck(
            status="unavailable",
            reason="No reliable source was available",
            attempts=["Searched the subject name"],
        ),
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_unavailable_details_required"


def test_ambiguous_collection_stops_for_clarification():
    assessment, error = _validate_collection_assessment(
        {
            "status": "ambiguous",
            "reason": "Two groups use the same trading name",
            "candidate_collections": ["Example Group UK", "Example Group Europe"],
        },
        {},
        _completed_enrichment(),
    )

    assert assessment is None
    payload = _payload(error)
    assert payload["details"]["code"] == "collection_membership_ambiguous"
    assert payload["details"]["status"] == "action_required"
