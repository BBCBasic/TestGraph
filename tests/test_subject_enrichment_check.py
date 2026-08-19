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
    elif status=="unavailable":
        raw.update(reason="No reliable source found",attempts=["Searched subject name and publisher"])
    else:
        raw["reason"]="The reviewed subject is a private, user-defined item"
    check,error=_validate_subject_enrichment_check(raw)
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
