from __future__ import annotations

from copy import deepcopy


RELATED_SUBJECT_LIMIT = 500
RELATED_RELATIONSHIP_LIMIT = 1000
_SAVE_POLICY_MARKER = "Every discovered collection member must be submitted."


def apply_chain_ingest_policy(tools: list[dict]) -> None:
    """Apply the MCP v2 complete collection-discovery policy idempotently.

    A finite authoritative directory must be represented completely. The server
    rejects collection saves whose discovered and submitted member counts differ.
    """

    by_name = {tool.get("name"): tool for tool in tools}

    enrich = by_name.get("enrich_subject")
    if enrich:
        enrich["description"] = (
            "Add missing identifiers, attributes, provenance and related unreviewed subjects to an existing "
            "subject without creating another review. Use this proactively when authoritative information was "
            "missed during the original save. Search for the official website yourself. When an authoritative "
            "source exposes a finite collection, submit every discovered member as an unreviewed subject, connect "
            "each member to the collection, and preserve the directory URL and provenance. Do not omit members "
            "because they are unreviewed, numerous or may be materialised later. Do not ask the user for a URL or "
            "routine lookup permission unless automatic lookup is unavailable or identity is genuinely ambiguous. "
            "Existing conflicting values are preserved rather than silently overwritten."
        )
        _set_context_limits(enrich)

    save = by_name.get("save_experience")
    if enrich and save:
        enrich_schema = enrich["inputSchema"]
        enrich_properties = enrich_schema["properties"]
        save_properties = save["inputSchema"]["properties"]
        enrich_properties["subject_id"] = {
            "type": "string", "format": "uuid",
            "description": "Preferred stable subject locator returned by search, fetch or save_experience.",
        }
        enrich_properties["subject_enrichment_check"] = deepcopy(
            save_properties["subject_enrichment_check"]
        )
        enrich_properties["subject_enrichment_check"]["description"] = (
            "Required evidence check for this enrichment. Reconcile sources against identifiers, "
            "attributes, provenance or subject_context request paths."
        )
        enrich_properties["collection_assessment"] = deepcopy(
            save_properties["collection_assessment"]
        )
        enrich_properties["collection_assessment"]["description"] = (
            "Required collection assessment for enrichment. For member status, use subject as the "
            "existing target ref and submit it plus every discovered sibling."
        )
        enrich_schema["required"] = [
            "idempotency_key", "subject_enrichment_check", "collection_assessment",
        ]
        enrich_schema["anyOf"] = [
            {"required": ["subject_id"]},
            {"required": ["subject_type", "canonical_key"]},
        ]

    if save:
        current = save.get("description", "")
        if _SAVE_POLICY_MARKER not in current:
            save["description"] = current + (
                " Every discovered collection member must be submitted. Include reviewed_subject plus every "
                "discovered sibling in collection_assessment.submitted_member_refs; the server derives the submitted "
                "count, requires it to equal discovered_count, and verifies that every ref exists and is connected "
                "to the collection. Unreviewed status, collection size and future materialisation are not omissions."
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
