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
    assert "names an exposed MCP tool" in description
    assert "reconstructing the procedure yourself" in description


def test_post_save_write_tools_require_following_the_workflow_next_action():
    tools = deepcopy(TOOLS)
    apply_guidance_tool_policy(tools)
    by_name = {tool["name"]: tool for tool in tools}

    for name in ("save_experience", "enrich_subject"):
        description = by_name[name]["description"].lower()

        assert "workflow.workflow_action_required" in description
        assert "must follow workflow.next_action" in description


def test_existing_subject_mutation_tools_publish_the_prewrite_classification_gate():
    tools = deepcopy(TOOLS)
    apply_guidance_tool_policy(tools)
    by_name = {tool["name"]: tool for tool in tools}

    for name in (
        "enrich_subject",
        "correct_subject_fact",
        "save_experience",
        "assert_location",
    ):
        description = by_name[name]["description"].lower()
        assert "before mutation" in description
        assert "classification_review_required" in description
        assert "same deterministic idempotency key" in description
        assert "must not report the update as complete" in description


def test_classification_writes_keep_write_scope_without_per_call_version_probe():
    assert "affirm_subject_classification" in WRITE_TOOL_NAMES
    assert "propose_subject_reclassification" in WRITE_TOOL_NAMES
    assert "reopen_subject_classification" in WRITE_TOOL_NAMES

    tools = deepcopy(TOOLS)
    apply_guidance_tool_policy(tools)
    by_name = {tool["name"]: tool for tool in tools}
    for name in ("affirm_subject_classification", "propose_subject_reclassification", "reopen_subject_classification"):
        schema = by_name[name]["inputSchema"]
        assert "version_check" not in schema["properties"]
        assert "version_check" not in schema["required"]
