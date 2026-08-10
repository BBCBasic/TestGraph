import uuid
from types import SimpleNamespace

import pytest

from app.api import mcp_v2
from app.services.mcp_autonomy_policy import apply_mcp_v2_autonomy_policy
from app.services.v2 import normalise_path, normalise_token


def test_mcp_vocabulary_governance_is_autonomous_by_default():
    apply_mcp_v2_autonomy_policy()

    assert mcp_v2.SERVER_VERSION == "2.3.7-alpha"
    tools = {tool["name"]: tool for tool in mcp_v2.TOOLS}

    pending = tools["pending_vocabulary_proposals"]["description"]
    propose = tools["propose_concept_fields"]["description"]
    verify = tools["verify_concept_field_proposal"]["description"]
    reject = tools["reject_concept_field_proposal"]["description"]
    save = tools["save_experience"]["description"]
    search = tools["search"]["description"]

    assert "Mandatory DNS governance" in pending
    assert "always check for unresolved DNS vocabulary" in search
    assert "always check for unresolved DNS vocabulary" in save
    assert "independent second-AI" in propose
    assert "dots only" in propose
    assert "terminal" in reject
    assert "without separate user confirmation" in verify
    assert "explicit user approval" in save
    assert "without asking the user to repeat an approval already given" in save


def test_dns_paths_use_dots_but_field_tokens_keep_underscores():
    assert normalise_path("travel.transportation.bike_share") == "travel.transportation.bike.share"
    assert normalise_path("travel transportation-bike_share") == "travel.transportation.bike.share"
    assert normalise_path("dining.restaurant.review") == "dining.restaurant.review"
    assert "_" not in normalise_path("dining_restaurant_review")

    # This rule is deliberately DNS-only: field/alias names retain snake_case.
    assert normalise_token("overall rating") == "overall_rating"
    assert normalise_token("return_intent") == "return_intent"


def test_normal_interaction_is_blocked_until_foreign_dns_proposals_are_reviewed():
    apply_mcp_v2_autonomy_policy()

    pending = SimpleNamespace(
        id=uuid.uuid4(),
        proposer_client_id="other-ai:v2",
        status="pending",
        concept_id=uuid.uuid4(),
        submitted_name="rating",
        canonical_name="rating",
        json_schema={"type": "number"},
        description="Reusable rating",
        aliases_json=["score"],
    )
    concept = SimpleNamespace(path="dining.restaurant.review", status="pending")

    class ScalarRows:
        def all(self):
            return [pending]

    class FakeDb:
        def scalars(self, _statement):
            return ScalarRows()

        def get(self, _model, object_id):
            if object_id == pending.concept_id:
                return concept
            return None

        def execute(self, _statement):
            raise AssertionError("normal search must not execute before DNS governance")

    principal = SimpleNamespace(client_id="reviewer-ai", user_id=uuid.uuid4())
    result = mcp_v2._search(FakeDb(), principal, {"query": "Star Anise"})

    assert result["isError"] is True
    payload = result["structuredContent"]
    assert payload["error"] == "DNS governance review required before continuing"
    assert payload["details"]["dns_governance_required"] is True
    assert payload["details"]["pending_count"] == 1
    assert payload["details"]["proposals"][0]["proposal_id"] == str(pending.id)
    assert payload["details"]["proposals"][0]["concept_path"] == "dining.restaurant.review"
    assert payload["details"]["proposals"][0]["canonical_name"] == "rating"


def test_own_pending_proposal_does_not_deadlock_proposing_ai():
    apply_mcp_v2_autonomy_policy()

    class ScalarRows:
        def all(self):
            # SQL filters the proposing client's own row out in the real DB.
            return []

    class EmptyResult:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, _statement):
            return ScalarRows()

        def execute(self, _statement):
            return EmptyResult()

    principal = SimpleNamespace(client_id="reviewer-ai", user_id=uuid.uuid4())
    result = mcp_v2._search(FakeDb(), principal, {"query": ""})

    assert result["structuredContent"]["count"] == 0


def test_identical_rejected_mcp_proposal_is_not_reopened():
    apply_mcp_v2_autonomy_policy()

    concept_id = uuid.uuid4()
    concept = SimpleNamespace(id=concept_id)
    proposal = SimpleNamespace(
        submitted_name="review",
        canonical_name="review",
        json_schema={"type": "string"},
        description="Freeform review text for a subject",
        aliases=["experience_review"],
    )
    rejected = SimpleNamespace(
        concept_id=concept_id,
        canonical_name_normalized="review",
        submitted_name="review",
        canonical_name="review",
        json_schema={"type": "string"},
        description="Freeform review text for a subject",
        aliases_json=["experience_review"],
        status="rejected",
        decision_reason="Wrong concept placement and duplicates raw_text.",
    )

    class FakeDb:
        def scalar(self, _statement):
            return rejected

    with pytest.raises(ValueError, match="previously rejected and will not be reopened"):
        mcp_v2.propose_concept_fields(
            FakeDb(),
            concept=concept,
            proposals=[proposal],
            proposer_client_id="capability:test:v2",
        )
