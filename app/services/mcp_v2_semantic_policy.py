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


def _append_description(tool: dict, text: str) -> None:
    current = str(tool.get("description") or "").strip()
    if text not in current:
        tool["description"] = f"{current} {text}".strip()


def apply_semantic_naming_policy(tools: list[dict]) -> None:
    """Layer the low-overhead naming/semantic distinction onto MCP tool guidance.

    This intentionally changes guidance rather than adding a consensus gate. Subject-type aliases already
    resolve to the same stable ID; semantic edges remain independently editable and governable.
    """
    by_name = {tool.get("name"): tool for tool in tools}

    search = by_name.get("search")
    if search:
        _append_description(search, RETRIEVAL_POLICY)

    vocabulary = by_name.get("vocabulary_index")
    if vocabulary:
        _append_description(vocabulary, NAMING_POLICY)

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

    relationship = by_name.get("set_type_relationship")
    if relationship:
        _append_description(
            relationship,
            "This is a semantic assertion, not a naming choice. If independent AIs materially disagree about the "
            "meaning of the edge, preserve the disagreement rather than treating alternate labels as proof of it.",
        )
