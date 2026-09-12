from app.core.config import get_settings
from app.services.guidance import BASELINE_GUIDANCE, active_baseline_guidance
from app.services.mcp_v2_semantic_policy import apply_semantic_naming_policy


def test_induction_separates_naming_from_semantic_disagreement():
    guidance = {item["key"]: item["text"] for item in BASELINE_GUIDANCE}

    assert "Naming disagreement is non-blocking" in guidance["classification"]
    assert "Semantic disagreement is different" in guidance["classification"]
    assert "retrieval as softer than canonical naming" in guidance["retrieval"]
    assert "Stable IDs, not preferred labels, determine identity" in guidance["retrieval"]
    assert "list_root_subject_types" in guidance["classification"]
    assert "fallback" in guidance["classification"].casefold()
    assert "backtrack" in guidance["retrieval"].casefold()


def test_mcp_policy_does_not_add_a_consensus_gate_for_aliases():
    tools = [
        {"name": "search", "description": "Search."},
        {"name": "vocabulary_index", "description": "Vocabulary."},
        {"name": "list_root_subject_types", "description": "Roots."},
        {"name": "list_child_subject_types", "description": "Children."},
        {"name": "get_subject_type_path", "description": "Path."},
        {"name": "resolve_subject_type", "description": "Resolve."},
        {"name": "register_subject_type_alias", "description": "Alias."},
        {"name": "resolve_subject_hierarchy", "description": "Hierarchy."},
        {"name": "propose_subject_reclassification", "description": "Reclassify."},
        {"name": "set_type_relationship", "description": "Relationship."},
    ]

    apply_semantic_naming_policy(tools)
    by_name = {tool["name"]: tool for tool in tools}

    assert "does not require another AI to prefer the same name" in by_name["register_subject_type_alias"]["description"]
    assert "stable subject-type ID is the identity boundary" in by_name["resolve_subject_type"]["description"]
    assert "Retrieval is deliberately softer than canonical naming" in by_name["search"]["description"]
    assert "semantic assertion, not a naming choice" in by_name["set_type_relationship"]["description"]
    assert "administration and debugging" in by_name["vocabulary_index"]["description"]
    assert "rank" in by_name["list_root_subject_types"]["description"].casefold()
    assert "fallback" in by_name["list_child_subject_types"]["description"].casefold()

    for name in ("vocabulary_index", "resolve_subject_hierarchy", "propose_subject_reclassification", "set_type_relationship"):
        description = by_name[name]["description"]
        assert "what a subject fundamentally is" in description
        assert "Material, arrangement/grouping, state/condition" in description
        assert "not a simplistic head-noun rule" in description
        assert "server independently validates structural writes" in description


def test_typed_induction_guidance_separates_two_semantic_questions(monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_MODE", "typed")
    get_settings.cache_clear()
    try:
        guidance = {item["key"]: item["text"] for item in active_baseline_guidance()}
        classification = guidance["classification"].casefold()

        assert "what fundamentally is this thing" in classification
        assert "what larger thing or system" in classification
        assert "do not force" in classification
    finally:
        get_settings.cache_clear()
