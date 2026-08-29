from copy import deepcopy

from app.api.mcp_v2 import TOOLS
from app.services.mcp_v2_guidance_policy import WRITE_TOOL_NAMES, apply_guidance_tool_policy


def test_enrichment_uses_server_owned_workflow_guidance():
    tools = deepcopy(TOOLS)
    apply_guidance_tool_policy(tools)
    enrich = next(tool for tool in tools if tool["name"] == "enrich_subject")
    description = enrich["description"]

    assert "WORKFLOW" in description
    assert "server now owns the post-enrichment procedure" in description
    assert "workflow.next_action" in description
    assert "reconstructing the procedure yourself" in description


def test_classification_writes_use_live_version_safety():
    assert "affirm_subject_classification" in WRITE_TOOL_NAMES
    assert "propose_subject_reclassification" in WRITE_TOOL_NAMES
    assert "reopen_subject_classification" in WRITE_TOOL_NAMES

    tools = deepcopy(TOOLS)
    apply_guidance_tool_policy(tools)
    by_name = {tool["name"]: tool for tool in tools}
    for name in ("affirm_subject_classification", "propose_subject_reclassification", "reopen_subject_classification"):
        schema = by_name[name]["inputSchema"]
        assert "version_check" in schema["properties"]
        assert "version_check" in schema["required"]
