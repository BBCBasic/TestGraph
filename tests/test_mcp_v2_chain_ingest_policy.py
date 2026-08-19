from copy import deepcopy

from app.api.mcp_v2 import TOOLS
from app.services.mcp_v2_policy import (
    RELATED_RELATIONSHIP_LIMIT,
    RELATED_SUBJECT_LIMIT,
    apply_chain_ingest_policy,
)


def test_collection_policy_keeps_only_relevant_members():
    tools = deepcopy(TOOLS)
    apply_chain_ingest_policy(tools)

    by_name = {tool["name"]: tool for tool in tools}
    enrich = by_name["enrich_subject"]
    save = by_name["save_experience"]

    assert "Do not bulk-ingest every published member" in enrich["description"]
    assert "materialise another member" in enrich["description"]
    assert "Collection assessment is mandatory" in save["description"]
    assert "Other members are created lazily" in save["description"]

    for tool in (enrich, save):
        context = tool["inputSchema"]["properties"]["subject_context"]["properties"]
        assert context["subjects"]["maxItems"] == RELATED_SUBJECT_LIMIT == 50
        assert context["relationships"]["maxItems"] == RELATED_RELATIONSHIP_LIMIT == 100


def test_collection_policy_is_idempotent():
    tools = deepcopy(TOOLS)
    apply_chain_ingest_policy(tools)
    first = deepcopy(tools)
    apply_chain_ingest_policy(tools)
    assert tools == first
