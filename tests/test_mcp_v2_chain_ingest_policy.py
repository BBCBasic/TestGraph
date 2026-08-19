from copy import deepcopy

from app.api.mcp_v2 import TOOLS
from app.services.mcp_v2_policy import (
    RELATED_RELATIONSHIP_LIMIT,
    RELATED_SUBJECT_LIMIT,
    apply_chain_ingest_policy,
)


def test_collection_policy_requires_complete_finite_sets():
    tools = deepcopy(TOOLS)
    apply_chain_ingest_policy(tools)

    by_name = {tool["name"]: tool for tool in tools}
    enrich = by_name["enrich_subject"]
    save = by_name["save_experience"]

    assert "Do not stop at one company landing page" in enrich["description"]
    assert "every authoritative collection surface" in enrich["description"]
    assert "source_manifest" in enrich["description"]
    assert "Do not stop at one company landing page" in save["description"]
    assert "source_manifest" in save["description"]
    assert "requires it to equal discovered_count" in save["description"]
    assert "lazily" not in save["description"]
    enrich_collection = enrich["inputSchema"]["properties"]["collection_assessment"]
    assert "source_manifest" in enrich_collection["properties"]

    for tool in (enrich, save):
        context = tool["inputSchema"]["properties"]["subject_context"]["properties"]
        assert context["subjects"]["maxItems"] == RELATED_SUBJECT_LIMIT == 500
        assert context["relationships"]["maxItems"] == RELATED_RELATIONSHIP_LIMIT == 1000


def test_collection_policy_is_idempotent():
    tools = deepcopy(TOOLS)
    apply_chain_ingest_policy(tools)
    first = deepcopy(tools)
    apply_chain_ingest_policy(tools)
    assert tools == first
