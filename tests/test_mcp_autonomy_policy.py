import uuid
from types import SimpleNamespace

import pytest

from app.api import mcp_v2
from app.services.mcp_autonomy_policy import apply_mcp_v2_autonomy_policy


def test_mcp_vocabulary_governance_is_autonomous_by_default():
    apply_mcp_v2_autonomy_policy()

    assert mcp_v2.SERVER_VERSION == "2.3.3-alpha"
    tools = {tool["name"]: tool for tool in mcp_v2.TOOLS}

    pending = tools["pending_vocabulary_proposals"]["description"]
    propose = tools["propose_concept_fields"]["description"]
    verify = tools["verify_concept_field_proposal"]["description"]
    reject = tools["reject_concept_field_proposal"]["description"]
    save = tools["save_experience"]["description"]

    assert "without asking the user" in pending
    assert "do not ask the user for routine permission" in propose
    assert "independent second-AI" in propose
    assert "Never resubmit an unchanged proposal" in propose
    assert "terminal" in reject
    assert "without requesting separate user confirmation" in verify
    assert "without requesting separate user confirmation" in reject
    assert "genuinely ambiguous" in pending
    assert "explicit user approval" in save
    assert "personal experience or opinion" in save


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
