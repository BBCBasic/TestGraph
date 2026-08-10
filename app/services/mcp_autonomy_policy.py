from __future__ import annotations

"""Runtime policy tuning for TasteGraph MCP vocabulary governance.

The MCP server deliberately keeps schema writes separate from direct user-experience
writes. Vocabulary verification/rejection is AI governance work: a reviewing AI should
make the routine decision itself after an independent check, and involve the user only
when the semantic choice is genuinely ambiguous or consequential.
"""


def apply_mcp_v2_autonomy_policy() -> None:
    # Import lazily to avoid creating an import cycle while app.api.mcp_v2 is loaded.
    from app.api import mcp_v2

    mcp_v2.SERVER_VERSION = "2.3.1-alpha"

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
            "the concept path and its ancestors. Propose only the smallest set of broadly reusable fields that materially "
            "improve future search/comparison/personalisation. Prefer raw_text for one-off or uncertain detail. Measurements "
            "and money must be machine-readable. Routine proposal housekeeping should be handled by the AI; involve the user "
            "only for genuinely ambiguous conceptual choices."
        ),
    }

    for tool in mcp_v2.TOOLS:
        description = descriptions.get(tool.get("name"))
        if description:
            tool["description"] = description


__all__ = ["apply_mcp_v2_autonomy_policy"]
