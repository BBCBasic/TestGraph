from __future__ import annotations


RELATED_SUBJECT_LIMIT = 5000
RELATED_RELATIONSHIP_LIMIT = 10000
_SAVE_POLICY_MARKER = "complete published set as unreviewed subject_context"


def apply_chain_ingest_policy(tools: list[dict]) -> None:
    """Apply the MCP v2 policy for authoritative multi-location discovery.

    Authoritative finite location sets are ingested when first discovered rather
    than deliberately deferred. The underlying subject-context service is
    idempotent by canonical identity; these limits are MCP payload guardrails,
    not a semantic threshold between "small" and "large" organisations.
    """
    by_name = {tool.get("name"): tool for tool in tools}

    enrich = by_name.get("enrich_subject")
    if enrich:
        enrich["description"] = (
            "Add missing identifiers, attributes, provenance and related unreviewed subjects to an existing "
            "subject without creating another review. Use this proactively when authoritative information was "
            "missed during the original save. Search for the official website yourself. When an authoritative "
            "source exposes a finite enumerable set of locations or other related subjects, ingest the complete "
            "published set on first discovery where technically practical, regardless of chain size. Create them "
            "as unreviewed subjects, connect them to the parent organisation, preserve source provenance and the "
            "official directory URL, and rely on canonical keys/idempotency to avoid duplicates on later refreshes. "
            "Do not deliberately defer a large chain merely because it has many locations. If one MCP payload cannot "
            "carry the complete set, submit deterministic batches until the published set is represented. Do not ask "
            "the user for a URL or routine lookup permission unless automatic lookup is unavailable or identity is "
            "genuinely ambiguous. Existing conflicting values are preserved rather than silently overwritten."
        )
        _raise_context_limits(enrich)

    save = by_name.get("save_experience")
    if save:
        current = save.get("description", "")
        if _SAVE_POLICY_MARKER not in current:
            save["description"] = current + (
                " When authoritative enrichment reveals a finite enumerable set of sibling locations or related "
                "subjects, include the complete published set as unreviewed subject_context on first discovery where "
                "technically practical, regardless of chain size. Preserve parent relationships, canonical identifiers "
                "and source provenance. Do not use chain size as a reason to defer known locations; if the complete set "
                "exceeds one payload, complete the ingestion through deterministic enrich_subject batches."
            )
        _raise_context_limits(save)


def _raise_context_limits(tool: dict) -> None:
    properties = tool.get("inputSchema", {}).get("properties", {})
    context = properties.get("subject_context", {})
    context_properties = context.get("properties", {})
    subjects = context_properties.get("subjects")
    relationships = context_properties.get("relationships")
    if isinstance(subjects, dict):
        subjects["maxItems"] = RELATED_SUBJECT_LIMIT
    if isinstance(relationships, dict):
        relationships["maxItems"] = RELATED_RELATIONSHIP_LIMIT
