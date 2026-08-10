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
    from app.models.v2 import ConceptFieldProposal
    from app.services.v2 import normalise_token
    from sqlalchemy import select

    mcp_v2.SERVER_VERSION = "2.3.3-alpha"

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
            "repeating the mistake. A rejected proposal is terminal and must not be silently reopened by resubmitting the "
            "same concept/field/schema. This is internal AI vocabulary governance; when your review is clearly negative, "
            "execute the rejection without requesting separate user confirmation. Ask only if the modelling choice is genuinely ambiguous."
        ),
        "propose_concept_fields": (
            "Schema design, not review extraction. Before proposing, inspect pending proposals, rejected proposals where relevant, "
            "the vocabulary index, the intended concept path and its ancestors. If no suitable canonical concept exists and the domain "
            "placement is clear, propose a sensible new domain concept path and the smallest durable reusable field set directly; do "
            "not ask the user for routine permission merely because the concept path is new. The independent second-AI verification "
            "step is the safeguard before anything becomes canonical. Never resubmit an unchanged proposal that another AI already "
            "rejected; use the rejection reason to choose a materially better concept/field design. Propose only fields that materially "
            "improve future search/comparison/personalisation, prefer raw_text for one-off or uncertain detail, and use machine-readable "
            "schemas for measurements and money. Ask the user only when the ontology or semantic modelling choice is genuinely ambiguous."
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

    # The underlying v2 service historically revived an identical rejected proposal
    # by changing it back to pending. That creates an AI-to-AI rejection loop. Guard
    # the MCP write path so a rejection remains authoritative unless the proposal is
    # materially redesigned rather than simply resubmitted unchanged.
    current = mcp_v2.propose_concept_fields
    if not getattr(current, "_tastegraph_rejection_guard", False):
        original = current

        def guarded_propose_concept_fields(
            db,
            *,
            concept,
            proposals,
            proposer_client_id,
        ):
            for proposal in proposals:
                canonical_key = normalise_token(proposal.canonical_name)
                existing = db.scalar(
                    select(ConceptFieldProposal).where(
                        ConceptFieldProposal.concept_id == concept.id,
                        ConceptFieldProposal.canonical_name_normalized == canonical_key,
                    )
                )
                if not existing or existing.status != "rejected":
                    continue

                same = (
                    existing.submitted_name == proposal.submitted_name
                    and existing.canonical_name == proposal.canonical_name
                    and existing.json_schema == proposal.json_schema
                    and existing.description == proposal.description
                    and existing.aliases_json == proposal.aliases
                )
                if same:
                    reason = existing.decision_reason or "No rejection reason was recorded."
                    raise ValueError(
                        "Identical vocabulary proposal was previously rejected and will not be reopened. "
                        f"Rejection reason: {reason} Use a materially different concept/field/schema rather than resubmitting unchanged."
                    )
                raise ValueError(
                    f"A rejected proposal already occupies canonical field name '{proposal.canonical_name}' on this concept. "
                    "Choose a materially different canonical design instead of overwriting or reopening the rejected proposal."
                )

            return original(
                db,
                concept=concept,
                proposals=proposals,
                proposer_client_id=proposer_client_id,
            )

        guarded_propose_concept_fields._tastegraph_rejection_guard = True
        mcp_v2.propose_concept_fields = guarded_propose_concept_fields


__all__ = ["apply_mcp_v2_autonomy_policy"]
