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
                    "attributes": {"discovered_count": 3},
                    "provenance": {"source_url": DIRECTORY},
                },
                {
                    "ref": "branch_two",
                    "subject_type": "cafe",
                    "name": "Example Group North",
                    "canonical_key": "example-group-north",
                    "provenance": {"source_url": DIRECTORY},
                },
                {
                    "ref": "branch_three",
                    "subject_type": "cafe",
                    "name": "Example Group South",
                    "canonical_key": "example-group-south",
                    "provenance": {"source_url": DIRECTORY},
                },
            ],
            "relationships": [
                {
                    "source_ref": member_ref,
                    "relationship": "branch_of",
                    "target_ref": "brand",
                    "provenance": {"source_url": DIRECTORY},
                }
                for member_ref in ("reviewed_subject", "branch_two", "branch_three")
            ],
        }
    }


def _member_assessment(**updates):
    value = {
        "status": "member",
        "collection_name": "Example Group",
        "collection_type": "business_chain",
        "directory_url": DIRECTORY,
        "discovered_count": 3,
        "submitted_member_refs": ["reviewed_subject", "branch_two", "branch_three"],
        "source_manifest": {
            "coverage_method": "single_page",
            "declared_source_count": 1,
            "source_pages": [{
                "url": DIRECTORY,
                "source_kind": "directory_page",
                "member_refs": ["reviewed_subject", "branch_two", "branch_three"],
                "terminal": True,
            }],
            "discovery_queries": ["Example Group official locations"],
            "exhaustion_evidence": "The authoritative directory is a finite single-page list.",
        },
        "evidence_sources": [DIRECTORY],
    }
    value.update(updates)
    return value


def test_missing_collection_assessment_blocks_with_retry_instruction():
    assessment, error = _validate_collection_assessment(
        None, {}, _completed_enrichment("https://example.test/about")
    )

    assert assessment is None
    payload = _payload(error)
    assert payload["details"]["status"] == "action_required"
    assert payload["details"]["code"] == "collection_assessment_required"
    assert payload["details"]["retry_tool"] == "save_experience"


def test_collection_member_requires_identity_directory_counts_and_refs():
    assessment, error = _validate_collection_assessment(
        {"status": "member"}, {}, _completed_enrichment()
    )

    assert assessment is None
    payload = _payload(error)
    assert payload["details"]["code"] == "collection_member_details_required"
    assert set(payload["details"]["missing_fields"]) == {
        "collection_name", "collection_type", "directory_url",
        "discovered_count", "submitted_member_refs", "source_manifest",
    }


def test_collection_member_requires_directory_to_be_reconciled():
    assessment, error = _validate_collection_assessment(
        _member_assessment(evidence_sources=[]),
        _member_args(),
        _completed_enrichment("https://example.test/about"),
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_directory_source_required"


def test_valid_member_submits_complete_collection():
    assessment, error = _validate_collection_assessment(
        _member_assessment(), _member_args(), _completed_enrichment(DIRECTORY)
    )

    assert error is None
    assert assessment.status == "member"
    assert assessment.discovered_count == 3
    assert len(assessment.submitted_member_refs) == 3


def test_member_requires_exhaustive_source_manifest():
    assessment, error = _validate_collection_assessment(
        _member_assessment(source_manifest=None),
        _member_args(),
        _completed_enrichment(DIRECTORY),
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_member_details_required"


def test_source_manifest_must_cover_every_submitted_member():
    manifest = _member_assessment()["source_manifest"]
    manifest["source_pages"][0]["member_refs"] = ["reviewed_subject", "branch_two"]
    assessment, error = _validate_collection_assessment(
        _member_assessment(source_manifest=manifest),
        _member_args(),
        _completed_enrichment(DIRECTORY),
    )

    assert assessment is None
    payload = _payload(error)
    assert payload["details"]["code"] == "collection_source_member_coverage_mismatch"
    assert payload["details"]["uncovered_member_refs"] == ["branch_three"]


def test_paginated_manifest_requires_continuous_pages_and_terminal_link():
    second = "https://example.test/locations?page=2"
    manifest = {
        "coverage_method": "pagination",
        "declared_source_count": 2,
        "source_pages": [
            {
                "url": DIRECTORY, "source_kind": "directory_page", "sequence": 1,
                "member_refs": ["reviewed_subject"], "next_url": second,
            },
            {
                "url": second, "source_kind": "directory_page", "sequence": 3,
                "member_refs": ["branch_two", "branch_three"], "terminal": True,
            },
        ],
        "discovery_queries": ["Example Group official locations"],
        "exhaustion_evidence": "Followed next links until the directory exposed no next page.",
    }
    assessment, error = _validate_collection_assessment(
        _member_assessment(
            source_manifest=manifest,
            evidence_sources=[DIRECTORY, second],
        ),
        _member_args(),
        _completed_enrichment(DIRECTORY, second),
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_pagination_incomplete"


def test_paginated_manifest_accepts_complete_source_chain():
    second = "https://example.test/locations?page=2"
    manifest = {
        "coverage_method": "pagination",
        "declared_source_count": 2,
        "source_pages": [
            {
                "url": DIRECTORY, "source_kind": "directory_page", "sequence": 1,
                "member_refs": ["reviewed_subject"], "next_url": second,
            },
            {
                "url": second, "source_kind": "directory_page", "sequence": 2,
                "member_refs": ["branch_two", "branch_three"], "terminal": True,
            },
        ],
        "discovery_queries": ["Example Group official locations"],
        "exhaustion_evidence": "Followed next links until the directory exposed no next page.",
    }
    assessment, error = _validate_collection_assessment(
        _member_assessment(
            source_manifest=manifest,
            evidence_sources=[DIRECTORY, second],
        ),
        _member_args(),
        _completed_enrichment(DIRECTORY, second),
    )

    assert error is None
    assert assessment.source_manifest.declared_source_count == 2


@pytest.mark.parametrize(
    ("submitted_refs", "expected_code"),
    [
        (["reviewed_subject", "branch_two"], "collection_member_count_mismatch"),
        (["reviewed_subject", "branch_two", "missing"], "collection_member_refs_unknown"),
        (["reviewed_subject", "branch_two", "branch_two"], "collection_member_refs_not_unique"),
        (["branch_two", "branch_three"], "reviewed_subject_not_counted"),
        (["reviewed_subject", "branch_two", "brand"], "collection_subject_is_not_member"),
    ],
)
def test_incomplete_or_false_member_submission_is_rejected(submitted_refs, expected_code):
    assessment, error = _validate_collection_assessment(
        _member_assessment(submitted_member_refs=submitted_refs),
        _member_args(),
        _completed_enrichment(DIRECTORY),
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == expected_code


def test_every_submitted_member_must_be_linked_to_collection():
    args = _member_args()
    args["subject_context"]["relationships"] = [
        relationship
        for relationship in args["subject_context"]["relationships"]
        if relationship["source_ref"] != "branch_three"
    ]

    assessment, error = _validate_collection_assessment(
        _member_assessment(), args, _completed_enrichment(DIRECTORY)
    )

    assert assessment is None
    payload = _payload(error)
    assert payload["details"]["code"] == "collection_members_not_linked"
    assert payload["details"]["unlinked_refs"] == ["branch_three"]


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
                        "attributes": {"discovered_count": 3},
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
                        "attributes": {"discovered_count": 3},
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
def test_member_context_must_store_directory_count_and_relationship(args, expected_code):
    assessment, error = _validate_collection_assessment(
        _member_assessment(), args, _completed_enrichment(DIRECTORY)
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



def test_genuine_collection_unavailability_can_continue():
    assessment, error = _validate_collection_assessment(
        {
            "status": "unavailable",
            "unavailability_kind": "authoritative_source_inaccessible",
            "evidence_sources": ["https://example.test/locations"],
            "reason": "The official directory returned an access-denied response",
            "attempts": [
                "Opened the official directory",
                "Searched for an official sitemap and locations API",
            ],
        },
        {},
        _completed_enrichment("https://example.test/locations"),
    )

    assert error is None
    assert assessment.status == "unavailable"
    assert assessment.unavailability_kind == "authoritative_source_inaccessible"


@pytest.mark.parametrize(
    "reason",
    [
        "Full enumeration is disproportionate for a quick review",
        "There are too many branches",
        "Completing this would require too much effort",
        "The directory is inconvenient and has high latency",
        "Deferred until later",
        "This is too time-consuming",
    ],
)
def test_unavailable_rejects_operational_excuses(reason):
    assessment, error = _validate_collection_assessment(
        {
            "status": "unavailable",
            "unavailability_kind": "authoritative_source_not_found",
            "reason": reason,
            "attempts": ["Searched the subject name"],
        },
        {},
        _completed_enrichment(),
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_unavailable_operational_excuse"


def test_unavailable_rejects_known_collection_signals():
    assessment, error = _validate_collection_assessment(
        {
            "status": "unavailable",
            "unavailability_kind": "authoritative_source_not_found",
            "reason": "No authoritative directory was found",
            "attempts": ["Searched the official website and sitemap"],
        },
        {"identifiers": {"brand": "Example Group"}},
        _completed_enrichment(),
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_unavailable_inconsistent"


def test_unavailable_rejects_candidate_collections():
    assessment, error = _validate_collection_assessment(
        {
            "status": "unavailable",
            "unavailability_kind": "collection_identity_not_found",
            "reason": "The collection identity could not be resolved",
            "attempts": ["Compared the official identities"],
            "candidate_collections": ["Example Group UK", "Example Group Europe"],
        },
        {},
        _completed_enrichment(),
    )

    assert assessment is None
    assert _payload(error)["details"]["code"] == "collection_unavailable_inconsistent"


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
