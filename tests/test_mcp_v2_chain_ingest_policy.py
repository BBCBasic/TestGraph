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

    assert "plausible future TestGraph use" in enrich["description"]
    assert "retrieval_uses" in enrich["description"]
    assert "authoritative source surfaces needed" in enrich["description"]
    assert "generic retrieval_uses purpose" in save["description"]
    assert "source_manifest" in save["description"]
    assert "requires it to equal discovered_count" in save["description"]
    assert "lazily" not in save["description"]
    enrich_collection = enrich["inputSchema"]["properties"]["collection_assessment"]
    assert "source_manifest" in enrich_collection["properties"]

    for tool in (enrich, save):
        context = tool["inputSchema"]["properties"]["subject_context"]["properties"]
        assert context["subjects"]["maxItems"] == RELATED_SUBJECT_LIMIT == 500
        assert context["relationships"]["maxItems"] == RELATED_RELATIONSHIP_LIMIT == 1000


def test_policy_teaches_complete_search_and_safe_write_batching():
    tools = deepcopy(TOOLS)
    apply_chain_ingest_policy(tools)

    by_name = {tool["name"]: tool for tool in tools}
    search_description = by_name["search"]["description"]
    assert "Search is lexical rather than semantic" in search_description
    assert "one discriminating keyword at a time" in search_description
    assert "exact subject name" in search_description
    assert "fetch every returned review" in search_description

    for name in ("enrich_subject", "save_experience", "save_assessment"):
        description = by_name[name]["description"]
        assert "concurrently in batches of up to 10" in description
        assert "Do not batch dependent operations" in description
        assert "deterministic idempotency keys" in description
        assert "restarted conversations" in description


def test_collection_policy_is_idempotent():
    tools = deepcopy(TOOLS)
    apply_chain_ingest_policy(tools)
    first = deepcopy(tools)
    apply_chain_ingest_policy(tools)
    assert tools == first
