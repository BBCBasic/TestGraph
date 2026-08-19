from copy import deepcopy

from app.api.mcp_v2 import TOOLS
from app.services.mcp_v2_policy import (
    RELATED_RELATIONSHIP_LIMIT,
    RELATED_SUBJECT_LIMIT,
    apply_chain_ingest_policy,
)


def _tool(name):
    return next(tool for tool in TOOLS if tool["name"] == name)


def test_chain_policy_ingests_large_finite_sets_instead_of_deferring():
    tools = deepcopy(TOOLS)
    apply_chain_ingest_policy(tools)

    by_name = {tool["name"]: tool for tool in tools}
    enrich = by_name["enrich_subject"]
    save = by_name["save_experience"]

    assert "complete published set" in enrich["description"]
    assert "regardless of chain size" in enrich["description"]
    assert "Do not deliberately defer a large chain" in enrich["description"]
    assert "complete published set" in save["description"]

    for tool in (enrich, save):
        context = tool["inputSchema"]["properties"]["subject_context"]["properties"]
        assert context["subjects"]["maxItems"] == RELATED_SUBJECT_LIMIT
        assert context["relationships"]["maxItems"] == RELATED_RELATIONSHIP_LIMIT


def test_chain_policy_is_idempotent():
    tools = deepcopy(TOOLS)
    apply_chain_ingest_policy(tools)
    first = deepcopy(tools)
    apply_chain_ingest_policy(tools)
    assert tools == first
