import uuid
from types import SimpleNamespace

import pytest

from app.api import mcp_v2
from app.services.mcp_autonomy_policy import apply_mcp_v2_autonomy_policy


def test_mcp_vocabulary_governance_is_autonomous_by_default():
    apply_mcp_v2_autonomy_policy()

    assert mcp_v2.SERVER_VERSION == "2.3.6-alpha"
    tools = {tool["name"]: tool for tool in mcp_v2.TOOLS}

    pending = tools["pending_vocabulary_proposals"]["description"]
    propose = tools["propose_concept_fields"]["description"]
    verify = tools["verify_concept_field_proposal"]["description"]
    reject = tools["reject_concept_field_proposal"]["description"]
    save = tools["save_experience"]["description"]
    search = tools["search"]["description"]

    assert "Mandatory DNS governance work queue" in pending
    assert "Calling this tool is not complete" in pending
    assert "Do not merely show the list" in pending
    assert "always check for unresolved DNS vocabulary" in search
    assert "processing them is part of the current task" in save
    assert "Do not merely report" in save
    assert "independent second-AI" in propose
    assert "terminal" in reject
    assert "without separate user confirmation" in verify
    assert "explicit user approval" in save
    assert "without asking the user to repeat an approval already given" in save


def test_normal_interaction_is_blocked_with_actionable_foreign_dns_details():
    apply_mcp_v2_autonomy_policy()

    concept_id = uuid.uuid4()
    pending = SimpleNamespace(
        id=uuid.uuid4(),
        concept_id=concept_id,
        proposer_client_id="other-ai:v2",
        submitted_name="rating",
        canonical_name="rating",
        json_schema={"type": "number", "minimum": 1, "maximum": 5},
        description="Reusable restaurant rating",
        aliases_json=["score"],
        status="pending",
    )
    concept = SimpleNamespace(id=concept_id, path="dining.restaurant.review", status="pending")

    class ScalarRows:
        def all(self):
            return [pending]

    class FakeDb:
        def scalars(self, _statement):
            return ScalarRows()

        def get(self, model, key):
            if key == concept_id:
                return concept
            return None

        def execute(self, _statement):
            raise AssertionError("normal search must not execute before DNS governance")

    principal = SimpleNamespace(client_id="reviewer-ai", user_id=uuid.uuid4())
    result = mcp_v2._search(FakeDb(), principal, {"query": "Star Anise"})

    assert result["isError"] is True
    payload = result["structuredContent"]
    assert payload["error"] == "DNS governance review required before continuing"
    details = payload["details"]
    assert details["dns_governance_required"] is True
    assert details["pending_count"] == 1
    assert details["proposals"] == [{
        "proposal_id": str(pending.id),
        "concept_path": "dining.restaurant.review",
        "concept_status": "pending",
        "submitted_name": "rating",
        "canonical_name": "rating",
        "json_schema": {"type": "number", "minimum": 1, "maximum": 5},
        "description": "Reusable restaurant rating",
        "aliases": ["score"],
        "proposed_by": "other-ai:v2",
        "status": "pending",
    }]
    assert "Call verify_concept_field_proposal" in details["required_next_actions"][1]
    assert "Call reject_concept_field_proposal" in details["required_next_actions"][2]
    assert "Do not stop after reporting this blocker" in details["instruction"]


def test_own_pending_proposal_does_not_deadlock_proposing_ai():
    apply_mcp_v2_autonomy_policy()

    class ScalarRows:
        def all(self):
            # SQL filters the proposing client's own pending rows out in the real DB.
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
