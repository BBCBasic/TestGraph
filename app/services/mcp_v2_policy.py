from __future__ import annotations


RELATED_SUBJECT_LIMIT = 50
RELATED_RELATIONSHIP_LIMIT = 100
_SAVE_POLICY_MARKER = "Other members are created lazily, not bulk-ingested."


def apply_chain_ingest_policy(tools: list[dict]) -> None:
    """Apply the MCP v2 collection-discovery policy idempotently.

    The graph stores the reviewed subject and its collection relationship at save
    time. Other collection members are materialised lazily when reviewed,
    explicitly requested or relevant to a search.
    """
    by_name = {tool.get("name"): tool for tool in tools}

    enrich = by_name.get("enrich_subject")
    if enrich:
        enrich["description"] = (
            "Add missing identifiers, attributes, provenance and related unreviewed subjects to an existing "
            "subject without creating another review. Use this proactively when authoritative information was "
            "missed during the original save. Search for the official website yourself. For a multi-location or "
            "otherwise collected subject, preserve the parent collection, its authoritative directory URL and the "
            "relationship to the existing subject. Do not bulk-ingest every published member merely because a "
            "directory exists; materialise another member when it is reviewed, explicitly requested or relevant to "
            "a search. Do not ask the user for a URL or routine lookup permission unless automatic lookup is "
            "unavailable or identity is genuinely ambiguous. Existing conflicting values are preserved rather than "
            "silently overwritten."
        )
        _set_context_limits(enrich)

    save = by_name.get("save_experience")
    if save:
        current = save.get("description", "")
        if _SAVE_POLICY_MARKER not in current:
            save["description"] = current + (
                " Collection assessment is mandatory and server-enforced. A collection member must include the "
                "collection subject, authoritative directory evidence, reported member count and a relationship to "
                "reviewed_subject. Other members are created lazily, not bulk-ingested."
            )
        _set_context_limits(save)


def _set_context_limits(tool: dict) -> None:
    properties = tool.get("inputSchema", {}).get("properties", {})
    context = properties.get("subject_context", {})
    context_properties = context.get("properties", {})
    subjects = context_properties.get("subjects")
    relationships = context_properties.get("relationships")
    if isinstance(subjects, dict):
        subjects["maxItems"] = RELATED_SUBJECT_LIMIT
    if isinstance(relationships, dict):
        relationships["maxItems"] = RELATED_RELATIONSHIP_LIMIT
