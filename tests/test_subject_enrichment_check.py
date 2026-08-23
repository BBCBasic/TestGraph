import json

import pytest

from app.api.mcp_v2 import _validate_subject_enrichment_check


def _payload(error):
    return json.loads(error["content"][0]["text"])


def test_missing_check_prompts_calling_ai_to_check_and_retry():
    check,error=_validate_subject_enrichment_check(None)
    assert check is None
    payload=_payload(error)
    assert payload["details"]["code"]=="subject_enrichment_check_required"
    assert payload["details"]["question"].startswith("Have you checked")
    assert "Do not ask the user" in payload["details"]["instruction"]


def test_completed_check_requires_a_source():
    check,error=_validate_subject_enrichment_check({"status":"completed"})
    assert check is None
    assert _payload(error)["details"]["code"]=="subject_enrichment_sources_required"


def test_unavailable_check_requires_reason_and_attempts():
    check,error=_validate_subject_enrichment_check({
        "status":"unavailable","reason":"No primary source was discoverable"
    })
    assert check is None
    assert _payload(error)["details"]["code"]=="subject_enrichment_unavailable_details_required"


@pytest.mark.parametrize("status",["completed","unavailable","not_applicable"])
def test_valid_non_ambiguous_checks_can_continue(status):
    raw={"status":status}
    if status=="completed":
        raw["sources"]=["https://example.test/about"]
        raw["applied_fields"]={"https://example.test/about":["identifiers.website"]}
        raw["retrieval_uses"]={
            "identifiers.website":{
                "roles":["identity"],
                "likely_queries":["Example official website"],
                "reason":"Distinguishes the exact subject and provides its canonical locator.",
            }
        }
    elif status=="unavailable":
        raw.update(reason="No reliable source found",attempts=["Searched subject name and publisher"])
    else:
        raw["reason"]="The reviewed subject is a private, user-defined item"
    args={"identifiers":{"website":"https://example.test/about"}} if status=="completed" else {}
    check,error=_validate_subject_enrichment_check(raw,args)
    assert error is None
    assert check.status==status


def test_ambiguous_check_stops_for_targeted_user_clarification():
    check,error=_validate_subject_enrichment_check({
        "status":"ambiguous","reason":"Two subjects share the same name",
        "candidate_identities":["Example in Stroud","Example in Gloucester"],
    })
    assert check is None
    payload=_payload(error)
    assert payload["details"]["code"]=="subject_identity_ambiguous"
    assert "Ask the user only" in payload["details"]["instruction"]



def test_completed_check_rejects_a_source_that_was_not_reconciled():
    check,error=_validate_subject_enrichment_check(
        {"status":"completed","sources":["https://example.test/about"]},
        {"identifiers":{}},
    )
    assert check is None
    assert _payload(error)["details"]["code"]=="subject_enrichment_sources_unreconciled"


def test_applied_source_must_point_to_data_present_in_save_request():
    source="https://example.test/about"
    check,error=_validate_subject_enrichment_check(
        {
            "status":"completed","sources":[source],
            "applied_fields":{source:["identifiers.website"]},
            "retrieval_uses":{
                "identifiers.website":{
                    "roles":["identity"],"likely_queries":["Example official website"],
                    "reason":"Canonical subject identity.",
                }
            },
        },
        {"identifiers":{}},
    )
    assert check is None
    assert _payload(error)["details"]["code"]=="subject_enrichment_reconciliation_invalid"


def test_source_can_be_explicitly_unapplied_with_a_reason():
    source="https://directory.example/listing"
    check,error=_validate_subject_enrichment_check(
        {
            "status":"completed","sources":[source],
            "unapplied_sources":{source:"Only corroborated the already-known subject name"},
        },
        {},
    )
    assert error is None
    assert check.unapplied_sources[source].startswith("Only corroborated")

def test_applied_source_can_point_to_nested_subject_context_data():
    source = "https://example.test/locations"
    path = "subject_context.subjects[0].identifiers.branch_directory"
    check, error = _validate_subject_enrichment_check(
        {
            "status": "completed",
            "sources": [source],
            "applied_fields": {source: [path]},
            "retrieval_uses": {
                path: {
                    "roles": ["relationship"],
                    "likely_queries": ["Example Restaurants in Stroud"],
                    "reason": "Enables location-based expansion through the parent collection.",
                }
            },
        },
        {
            "subject_context": {
                "subjects": [
                    {
                        "ref": "chain",
                        "subject_type": "restaurant chain",
                        "name": "Example Restaurants",
                        "canonical_key": "example-restaurants",
                        "identifiers": {"branch_directory": source},
                    }
                ],
                "relationships": [],
            }
        },
    )

    assert error is None
    assert check.applied_fields[source] == [path]


def test_nested_subject_context_path_must_point_to_existing_data():
    source = "https://example.test/locations"
    check, error = _validate_subject_enrichment_check(
        {
            "status": "completed",
            "sources": [source],
            "applied_fields": {
                source: ["subject_context.subjects[1].identifiers.branch_directory"]
            },
            "retrieval_uses": {
                "subject_context.subjects[1].identifiers.branch_directory": {
                    "roles": ["relationship"],
                    "likely_queries": ["Example locations"],
                    "reason": "Would support collection expansion if the path existed.",
                }
            },
        },
        {
            "subject_context": {
                "subjects": [
                    {
                        "identifiers": {"branch_directory": source},
                    }
                ]
            }
        },
    )

    assert check is None
    payload = _payload(error)
    assert payload["details"]["code"] == "subject_enrichment_reconciliation_invalid"
    assert payload["details"]["invalid_paths"][source] == [
        "subject_context.subjects[1].identifiers.branch_directory"
    ]



def test_applied_fact_requires_generic_retrieval_use():
    source = "https://example.test/about"
    check, error = _validate_subject_enrichment_check(
        {
            "status": "completed",
            "sources": [source],
            "applied_fields": {source: ["identifiers.website"]},
        },
        {"identifiers": {"website": source}},
    )

    assert check is None
    assert _payload(error)["details"]["code"] == "subject_enrichment_retrieval_use_invalid"
    assert _payload(error)["details"]["missing_retrieval_uses"] == ["identifiers.website"]


def test_non_verification_use_requires_likely_query_example():
    source = "https://example.test/about"
    check, error = _validate_subject_enrichment_check(
        {
            "status": "completed",
            "sources": [source],
            "applied_fields": {source: ["subject_attributes.service"]},
            "retrieval_uses": {
                "subject_attributes.service": {
                    "roles": ["classification"],
                    "reason": "Describes the kind of service available.",
                }
            },
        },
        {"subject_attributes": {"service": "example"}},
    )

    assert check is None
    payload = _payload(error)
    assert payload["details"]["code"] == "subject_enrichment_retrieval_use_invalid"
    assert payload["details"]["uses_without_likely_queries"] == [
        "subject_attributes.service"
    ]


def test_verification_only_use_does_not_require_query_example():
    source = "https://example.test/directory"
    path = "collection_assessment.source_manifest.source_pages[0].url"
    check, error = _validate_subject_enrichment_check(
        {
            "status": "completed",
            "sources": [source],
            "applied_fields": {source: [path]},
            "retrieval_uses": {
                path: {
                    "roles": ["verification"],
                    "reason": "Proves which authoritative directory page was traversed.",
                }
            },
        },
        {
            "collection_assessment": {
                "source_manifest": {
                    "source_pages": [{"url": source}]
                }
            }
        },
    )

    assert error is None
    assert check.retrieval_uses[path].roles == ["verification"]
