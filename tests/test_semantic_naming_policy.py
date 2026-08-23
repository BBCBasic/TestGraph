from app.services.guidance import BASELINE_GUIDANCE
from app.services.mcp_v2_semantic_policy import apply_semantic_naming_policy


def test_induction_separates_naming_from_semantic_disagreement():
    guidance = {item["key"]: item["text"] for item in BASELINE_GUIDANCE}

    assert "Naming disagreement is non-blocking" in guidance["classification"]
    assert "Semantic disagreement is different" in guidance["classification"]
    assert "retrieval as softer than canonical naming" in guidance["retrieval"]
    assert "Stable IDs, not preferred labels, determine identity" in guidance["retrieval"]


def test_mcp_policy_does_not_add_a_consensus_gate_for_aliases():
    tools = [
        {"name": "search", "description": "Search."},
        {"name": "vocabulary_index", "description": "Vocabulary."},
        {"name": "resolve_subject_type", "description": "Resolve."},
        {"name": "register_subject_type_alias", "description": "Alias."},
        {"name": "resolve_subject_hierarchy", "description": "Hierarchy."},
        {"name": "set_type_relationship", "description": "Relationship."},
    ]

    apply_semantic_naming_policy(tools)
    by_name = {tool["name"]: tool for tool in tools}

    assert "does not require another AI to prefer the same name" in by_name["register_subject_type_alias"]["description"]
    assert "stable subject-type ID is the identity boundary" in by_name["resolve_subject_type"]["description"]
    assert "Retrieval is deliberately softer than canonical naming" in by_name["search"]["description"]
    assert "semantic assertion, not a naming choice" in by_name["set_type_relationship"]["description"]
