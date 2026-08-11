import uuid
from types import SimpleNamespace

import pytest

from app.api import mcp_v2
from app.services.mcp_autonomy_policy import apply_mcp_v2_autonomy_policy
from app.services.v2 import normalise_path, normalise_token


def test_mcp_vocabulary_governance_is_independent_peer_review():
    apply_mcp_v2_autonomy_policy()

    assert mcp_v2.SERVER_VERSION == "2.4.0-alpha"
    tools = {tool["name"]: tool for tool in mcp_v2.TOOLS}

    pending = tools["pending_vocabulary_proposals"]["description"]
    propose = tools["propose_concept_fields"]["description"]
    verify = tools["verify_concept_field_proposal"]["description"]
    reject = tools["reject_concept_field_proposal"]["description"]
    save = tools["save_experience"]["description"]
    search = tools["search"]["description"]

    assert "Independent peer-review queue" in pending
    assert "does not block unrelated user work" in pending
    assert "may not verify its own proposal" in verify
    assert "constrained schema-governance decision" in verify
    assert "published schema rules" in verify
    assert "independent second-AI" in propose
    assert "dots only" in propose
    assert "terminal" in reject
    assert "explicit user approval" in save
    assert "Unrelated pending vocabulary proposals must never block the save" in save
    assert "without asking the user to repeat an approval already given" in save
    assert "Pending vocabulary proposals do not block reads" in search


def test_dns_paths_use_dots_but_field_tokens_keep_underscores():
    assert normalise_path("travel.transportation.bike_share") == "travel.transportation.bike.share"
    assert normalise_path("travel transportation-bike_share") == "travel.transportation.bike.share"
    assert normalise_path("dining.restaurant.review") == "dining.restaurant.review"
    assert "_" not in normalise_path("dining_restaurant_review")

    # This rule is deliberately DNS-only: field/alias names retain snake_case.
    assert normalise_token("overall rating") == "overall_rating"
    assert normalise_token("return_intent") == "return_intent"


def test_unrelated_pending_proposals_do_not_block_normal_interaction():
    apply_mcp_v2_autonomy_policy()

    class EmptyResult:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, _statement):
            raise AssertionError("normal interaction must not inspect the global pending queue")

        def execute(self, _statement):
            return EmptyResult()

    principal = SimpleNamespace(client_id="reviewer-ai", user_id=uuid.uuid4())
    result = mcp_v2._search(FakeDb(), principal, {"query": "Foxglove Junction"})

    assert result["structuredContent"]["count"] == 0
    assert "isError" not in result


def test_own_pending_proposal_does_not_deadlock_proposing_ai():
    apply_mcp_v2_autonomy_policy()

    class EmptyResult:
        def all(self):
            return []

    class FakeDb:
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
