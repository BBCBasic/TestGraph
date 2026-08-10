from __future__ import annotations

"""Runtime policy tuning for TasteGraph MCP vocabulary governance.

The MCP server deliberately keeps schema writes separate from direct user-experience
writes. Vocabulary proposal/verification/rejection is AI governance work: an AI should
make routine, clear schema decisions itself after checking the shared vocabulary, and
involve the user only when the semantic choice is genuinely ambiguous. Saving a direct
user experience remains separate and still requires explicit user approval.
"""


def apply_mcp_v2_autonomy_policy() -> None:
    # Import lazily to avoid creating an import cycle while app.api.mcp_v2 is loaded.
    from app.api import mcp_v2

    mcp_v2.SERVER_VERSION = "2.3.2-alpha"

    descriptions = {
        "pending_vocabulary_proposals": (
            "List unresolved vocabulary proposals made by other authenticated AI clients. "
            "Perform the full independent review yourself: inspect concept placement, parent/inherited vocabulary, "
            "duplicates/aliases, generality, analytical value, and JSON-schema quality. When the decision is clear, "
            "continue directly to verify_concept_field_proposal or reject_concept_field_proposal without asking the "
            "user for routine schema-maintenance confirmation. Ask the user only when there is a genuinely ambiguous "
            "semantic or modelling choice that cannot be resolved from the vocabulary and proposal evidence."
        ),
        "verify_concept_field_proposal": (
            "Independently promote another AI's pending proposal only when concept placement, generality, analytical "
            "value, inheritance/duplication checks and JSON schema are durable. This is internal AI vocabulary governance, "
            "not a statement of the user's opinion; when your review is clearly positive, execute the verification without "
            "requesting separate user confirmation. The authenticated client cannot verify its own proposal."
        ),
        "reject_concept_field_proposal": (
            "Reject another AI's pending vocabulary proposal when it is duplicate, over-specific, analytically weak, "
            "poorly typed, wrongly placed, or better left in raw_text. Record a concrete reason so future AIs can avoid "
            "repeating the mistake. This is internal AI vocabulary governance; when your review is clearly negative, "
            "execute the rejection without requesting separate user confirmation. Ask only if the modelling choice is genuinely ambiguous."
        ),
        "propose_concept_fields": (
            "Schema design, not review extraction. Before proposing, inspect pending proposals, the vocabulary index, "
            "the intended concept path and its ancestors. If no suitable canonical concept exists and the domain placement "
            "is clear, propose a sensible new domain concept path and the smallest durable reusable field set directly; do "
            "not ask the user for routine permission merely because the concept path is new. The independent second-AI "
            "verification step is the safeguard before anything becomes canonical. Propose only fields that materially "
            "improve future search/comparison/personalisation, prefer raw_text for one-off or uncertain detail, and use "
            "machine-readable schemas for measurements and money. Ask the user only when the ontology or semantic modelling "
            "choice is genuinely ambiguous."
        ),
        "save_experience": (
            "Save a direct user experience only after explicit user approval. Vocabulary housekeeping is separate: AI clients "
            "may propose, verify and reject clear schema changes autonomously, but must not treat that autonomy as approval to "
            "store the user's personal experience or opinion. The concept and every structured field must already be canonical. "
            "This tool never changes the schema. Reuse idempotency_key on retry."
        ),
    }

    for tool in mcp_v2.TOOLS:
        description = descriptions.get(tool.get("name"))
        if description:
            tool["description"] = description


__all__ = ["apply_mcp_v2_autonomy_policy"]
