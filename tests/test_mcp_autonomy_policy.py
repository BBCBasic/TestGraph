import uuid
from types import SimpleNamespace

from app.api import mcp_v2
from app.services.mcp_autonomy_policy import apply_mcp_v2_autonomy_policy
from app.services.v2 import normalise_path, normalise_token


def test_mcp_vocabulary_governance_is_server_side():
    apply_mcp_v2_autonomy_policy()

    assert mcp_v2.SERVER_VERSION == "2.5.0-alpha"
    tools = {tool["name"]: tool for tool in mcp_v2.TOOLS}

    assert "verify_concept_field_proposal" not in tools
    assert "reject_concept_field_proposal" not in tools

    propose = tools["propose_concept_fields"]["description"]
    alias = tools["propose_alias"]["description"]
    save = tools["save_experience"]["description"]
    search = tools["search"]["description"]

    assert "TasteGraph governs it with deterministic server-side rules" in propose
    assert "accepted, revise, rejected or review" in propose
    assert "materially improve the proposal and resubmit" in propose
    assert "generic rating, score, stars, sentiment or satisfaction fields" in propose
    assert "no second AI vote is required" in alias
    assert "Unrelated vocabulary proposals must never block the save" in save
    assert "Vocabulary governance does not block reads" in search


def test_dns_paths_use_dots_but_field_tokens_keep_existing_token_format():
    assert normalise_path("travel.transportation.bike_share") == "travel.transportation.bike.share"
    assert normalise_path("travel transportation-bike_share") == "travel.transportation.bike.share"
    assert normalise_path("dining.restaurant.review") == "dining.restaurant.review"
    assert "_" not in normalise_path("dining_restaurant_review")

    # The no-underscore rule is for DNS hierarchy and governance status words.
    # Existing canonical field token normalisation remains backwards compatible.
    assert normalise_token("overall rating") == "overall_rating"
    assert normalise_token("return_intent") == "return_intent"


def test_unrelated_vocabulary_does_not_block_normal_interaction():
    apply_mcp_v2_autonomy_policy()

    class EmptyResult:
        def all(self):
            return []

    class FakeDb:
        def execute(self, _statement):
            return EmptyResult()

    principal = SimpleNamespace(client_id="reviewer-ai", user_id=uuid.uuid4())
    result = mcp_v2._search(FakeDb(), principal, {"query": "Foxglove Junction"})

    assert result["structuredContent"]["count"] == 0
    assert "isError" not in result


def test_hidden_peer_review_handlers_are_disabled():
    apply_mcp_v2_autonomy_policy()

    principal = SimpleNamespace(client_id="reviewer-ai", user_id=uuid.uuid4())
    result = mcp_v2._verify_concept_field_proposal(None, principal, {"proposal_id": str(uuid.uuid4()), "rationale": "would previously verify"})

    assert result["isError"] is True
    assert result["structuredContent"]["error"] == "AI peer vocabulary review is disabled"
    assert result["structuredContent"]["details"]["governance"] == "server"
    assert "accepted, revise, rejected or review" in result["structuredContent"]["details"]["instruction"]
