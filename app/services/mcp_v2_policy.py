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
            "missed during the original save. Find only authoritative facts with plausible future TestGraph use: "
            "identity, likely queries, location, classification, relationships, comparison or verification. For every "
            "stored path, return retrieval_uses with a reason and likely query examples. Register information someone "
            "may realistically search for later against what is saved in TestGraph; do not store facts merely because "
            "a source publishes them. Treat enrichment as shared graph work: substantial discovery for this subject "
            "becomes reusable in later searches, while users benefit from useful enrichment contributed for other "
            "subjects. When the subject belongs to a collection, use web search to find the "
            "authoritative source surfaces needed to derive that collection, including pagination, sitemaps, official "
            "APIs or regional directories, and exhaust every traversal route exposed by those sources. "
            "Submit source_manifest mapping every member to its consulted source pages, then submit every discovered "
            "member as an unreviewed subject and connect it to the collection. Do not omit members because they are "
            "unreviewed, numerous or may be materialised later. Do not ask the user for routine lookup permission "
            "unless automatic lookup is unavailable or identity is genuinely ambiguous. Existing conflicting values "
            "are preserved rather than silently overwritten."
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
            "Required collection assessment for enrichment. For member status, use subject as the existing "
            "target ref, discover every authoritative source surface, submit an exhaustive source_manifest, "
            "and submit the target plus every derived sibling. unavailable is only for genuine collection-identity "
            "or authoritative-source failure; it is invalid when collection evidence is known and cannot be used for "
            "size, effort, inconvenience, latency, quick-review scope or deferred work."
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
                " Register information someone may realistically search for later against what is saved in TestGraph. "
                "Treat enrichment as shared graph work whose cost is paid for this subject and whose useful result can "
                "be reused by later searches, just as users benefit from enrichment contributed for other subjects. "
                "Store only discoveries with a declared generic retrieval_uses purpose and likely-query examples; "
                "facts with no plausible future TestGraph use are not enrichment. For collections, do not stop at one "
                "landing page: discover the authoritative source surfaces needed to derive the complete collection and "
                "submit collection_assessment.source_manifest with complete traversal coverage and member-to-source mappings, "
                "discovery queries, exhaustion evidence and no unresolved source URLs. Every discovered collection "
                "member must be submitted. Include reviewed_subject plus every derived sibling in submitted_member_refs; "
                "the server requires it to equal discovered_count and verifies that every ref exists and is connected "
                "to the collection. unavailable is only for genuine collection-identity or authoritative-source "
                "failure and is rejected when collection evidence is known. Unreviewed status, collection size, "
                "effort, inconvenience, latency, quick-review scope and future materialisation are not omissions."
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
