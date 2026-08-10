from app.api import mcp_v2
from app.services.mcp_autonomy_policy import apply_mcp_v2_autonomy_policy


def test_mcp_vocabulary_governance_is_autonomous_by_default():
    apply_mcp_v2_autonomy_policy()

    assert mcp_v2.SERVER_VERSION == "2.3.2-alpha"
    tools = {tool["name"]: tool for tool in mcp_v2.TOOLS}

    pending = tools["pending_vocabulary_proposals"]["description"]
    propose = tools["propose_concept_fields"]["description"]
    verify = tools["verify_concept_field_proposal"]["description"]
    reject = tools["reject_concept_field_proposal"]["description"]
    save = tools["save_experience"]["description"]

    assert "without asking the user" in pending
    assert "do not ask the user for routine permission" in propose
    assert "independent second-AI" in propose
    assert "without requesting separate user confirmation" in verify
    assert "without requesting separate user confirmation" in reject
    assert "genuinely ambiguous" in pending
    assert "explicit user approval" in save
    assert "personal experience or opinion" in save
