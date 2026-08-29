from __future__ import annotations


NAMING_POLICY = (
    "Naming disagreement is soft and must not block use. If two labels are genuinely equivalent, "
    "they may resolve to the same stable subject-type identity through an alias even when different "
    "AI clients prefer different display names. Do not require cross-model agreement on wording before "
    "using an existing type. Semantic disagreement is different: disagreement about whether two concepts "
    "mean the same thing, or about a belongs_to/other relationship, may require preservation as separate "
    "concepts or a deliberation rather than silently collapsing them."
)

RETRIEVAL_POLICY = (
    "Retrieval is deliberately softer than canonical naming. Search using the user's wording first, then "
    "try known aliases, canonical type names and useful broader/related types when needed. A search miss for "
    "one label is not evidence that the underlying subject or concept is absent. Stable IDs, not preferred "
    "labels, determine identity."
)

SEMANTIC_HEAD_POLICY = (
    "Classification vocabulary should represent what a subject fundamentally is. Before creating, selecting, "
    "relating or proposing a subject type, identify the semantic head and descriptive modifiers. Material, "
    "arrangement/grouping, state/condition, quantity, colour, size, location and purpose/use normally belong "
    "in attributes or relationships rather than subject-type names. This is not a simplistic head-noun rule: "
    "a compound may remain a distinct type when the combined concept has materially different identity, "
    "behaviour, relationships, classification meaning or realistic retrieval needs. The server independently "
    "validates structural writes, so client guidance cannot bypass this rule."
)


def _append_description(tool: dict, text: str) -> None:
    current = str(tool.get("description") or "").strip()
    if text not in current:
        tool["description"] = f"{current} {text}".strip()


def apply_semantic_naming_policy(tools: list[dict]) -> None:
    """Layer naming, retrieval and semantic-head policy onto MCP tool guidance."""
    by_name = {tool.get("name"): tool for tool in tools}

    search = by_name.get("search")
    if search:
        _append_description(search, RETRIEVAL_POLICY)

    vocabulary = by_name.get("vocabulary_index")
    if vocabulary:
        _append_description(vocabulary, NAMING_POLICY)
        _append_description(vocabulary, SEMANTIC_HEAD_POLICY)

    resolve_type = by_name.get("resolve_subject_type")
    if resolve_type:
        _append_description(
            resolve_type,
            "Equivalent aliases are valid lookup inputs; canonical wording is not a prerequisite for use. "
            "The returned stable subject-type ID is the identity boundary.",
        )

    alias = by_name.get("register_subject_type_alias")
    if alias:
        _append_description(
            alias,
            "Use this for genuine naming equivalence. Registering or using an equivalent alias does not require "
            "another AI to prefer the same name; disagreement about wording alone is not a semantic conflict.",
        )

    hierarchy = by_name.get("resolve_subject_hierarchy")
    if hierarchy:
        _append_description(
            hierarchy,
            "Before creating a new semantic node, distinguish a genuinely different concept from a mere naming "
            "variant. Naming variants should reuse identity; genuine meaning differences may remain separate.",
        )
        _append_description(hierarchy, SEMANTIC_HEAD_POLICY)

    reclassification = by_name.get("propose_subject_reclassification")
    if reclassification:
        _append_description(reclassification, SEMANTIC_HEAD_POLICY)

    relationship = by_name.get("set_type_relationship")
    if relationship:
        _append_description(
            relationship,
            "This is a semantic assertion, not a naming choice. If independent AIs materially disagree about the "
            "meaning of the edge, preserve the disagreement rather than treating alternate labels as proof of it.",
        )
        _append_description(relationship, SEMANTIC_HEAD_POLICY)
