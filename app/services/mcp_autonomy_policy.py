from __future__ import annotations

"""Runtime policy tuning for TasteGraph MCP vocabulary governance.

Vocabulary proposal/verification/rejection is AI governance work. Routine, clear schema
choices should be handled autonomously. Saving a direct user experience remains separate
and still requires explicit user approval.

Every normal MCP interaction must first clear any independently-reviewable DNS vocabulary
work. This prevents pending schema decisions from sitting unnoticed until a later save.
"""


def apply_mcp_v2_autonomy_policy() -> None:
    # Import lazily to avoid creating an import cycle while app.api.mcp_v2 is loaded.
    from contextvars import ContextVar

    from sqlalchemy import select

    from app.api import mcp_v2
    from app.models.v2 import ConceptFieldProposal
    from app.services.v2 import normalise_token

    mcp_v2.SERVER_VERSION = "2.3.5-alpha"

    dns_preflight = (
        "Before carrying out the user's requested TasteGraph action, always check for unresolved DNS vocabulary "
        "proposals from other authenticated AI clients. If any exist, review and resolve every clear proposal first "
        "using verify_concept_field_proposal or reject_concept_field_proposal, without asking for routine human approval, "
        "then continue the original action. Ask the user only for a genuinely ambiguous ontology decision. "
    )

    descriptions = {
        "pending_vocabulary_proposals": (
            "Mandatory DNS governance check. List unresolved vocabulary proposals made by other authenticated AI clients. "
            "Perform the full independent review yourself: inspect concept placement, parent/inherited vocabulary, duplicates/aliases, "
            "generality, analytical value, and JSON-schema quality. Resolve every clearly reviewable proposal in the batch without "
            "pausing for user confirmation between fields. Continue directly to verify_concept_field_proposal or "
            "reject_concept_field_proposal as appropriate. Ask the user only when there is a genuinely ambiguous semantic or modelling choice."
        ),
        "verify_concept_field_proposal": (
            "Independently promote another AI's pending proposal only when concept placement, generality, analytical value, "
            "inheritance/duplication checks and JSON schema are durable. This is internal AI vocabulary governance, not a statement "
            "of the user's opinion. When clearly positive, verify without separate user confirmation. If several proposals are clear, "
            "verify them all in sequence. The authenticated client cannot verify its own proposal."
        ),
        "reject_concept_field_proposal": (
            "Reject another AI's pending vocabulary proposal when it is duplicate, over-specific, analytically weak, poorly typed, "
            "wrongly placed, or better left in raw_text. A rejected proposal is terminal and must not be silently reopened by "
            "resubmitting the same concept/field/schema. This is internal AI vocabulary governance; when clearly negative, reject "
            "without separate user confirmation. If several proposals are clearly rejectable, reject them all in sequence."
        ),
        "propose_concept_fields": (
            dns_preflight
            + "Schema design, not review extraction. Inspect rejected proposals where relevant, the vocabulary index, the intended concept "
            "path and its ancestors. If no suitable canonical concept exists and placement is clear, propose a sensible new domain concept "
            "path and the smallest durable reusable field set directly. The independent second-AI verification step is the safeguard before "
            "anything becomes canonical. Never resubmit an unchanged rejected proposal. Propose all clearly needed fields together."
        ),
        "save_experience": (
            dns_preflight
            + "Save a direct user experience only after explicit user approval. Vocabulary housekeeping is separate. If a save is blocked "
            "only by non-canonical vocabulary, resolve all independently reviewable governance work and retry the original save without "
            "asking the user to repeat an approval already given. Existing approval remains valid while the review content and meaning are unchanged."
        ),
        "search": dns_preflight + "Search the connected user's direct experiences across any domain.",
        "fetch": dns_preflight + "Fetch one complete direct experience, including submitted and canonical structured data.",
        "get_concept": dns_preflight + "Inspect canonical concept vocabulary after mandatory pending-DNS governance has been cleared.",
        "propose_alias": dns_preflight + "Propose a semantic alias mapping only after mandatory pending-DNS governance has been cleared.",
        "save_assessment": dns_preflight + "Save AI-derived analysis only after mandatory pending-DNS governance has been cleared.",
        "vocabulary_index": (
            "Inspect the global DNS vocabulary index. This is a governance/preflight tool and may be used while pending proposals exist."
        ),
    }

    for tool in mcp_v2.TOOLS:
        description = descriptions.get(tool.get("name"))
        if description:
            tool["description"] = description

    # Keep the authenticated principal available to wrappers whose original function
    # signatures do not receive it (for example get_concept).
    principal_context: ContextVar[object | None] = ContextVar("tastegraph_mcp_principal", default=None)
    current_principal = mcp_v2._principal
    if not getattr(current_principal, "_tastegraph_dns_context", False):
        original_principal = current_principal

        def contextual_principal(request, scope):
            principal = original_principal(request, scope)
            principal_context.set(principal)
            return principal

        contextual_principal._tastegraph_dns_context = True
        mcp_v2._principal = contextual_principal

    def foreign_pending(db, principal):
        if principal is None:
            return []
        client_id = f"{principal.client_id}:v2"
        return list(
            db.scalars(
                select(ConceptFieldProposal)
                .where(
                    ConceptFieldProposal.status == "pending",
                    ConceptFieldProposal.proposer_client_id != client_id,
                )
                .order_by(ConceptFieldProposal.created_at)
            ).all()
        )

    def blocked_result(rows):
        return mcp_v2._error(
            "DNS governance review required before continuing",
            {
                "dns_governance_required": True,
                "pending_count": len(rows),
                "proposal_ids": [str(row.id) for row in rows[:50]],
                "instruction": (
                    "Call pending_vocabulary_proposals now, independently verify or reject every clear proposal, "
                    "then retry the original TasteGraph action. Do not ask the user for routine governance approval."
                ),
            },
        )

    # Enforce the preflight server-side on normal interaction tools. Governance tools
    # themselves remain callable so the client can clear the queue. Own proposals do not
    # block the proposing AI because it cannot self-verify them; another AI will be forced
    # to review them on its next normal interaction.
    principal_handlers = (
        "_search",
        "_fetch",
        "_propose_concept_fields",
        "_propose_alias",
        "_save_experience",
        "_save_assessment",
    )
    for handler_name in principal_handlers:
        current_handler = getattr(mcp_v2, handler_name)
        if getattr(current_handler, "_tastegraph_dns_preflight", False):
            continue
        original_handler = current_handler

        def make_principal_guard(original):
            def guarded(db, principal, args):
                rows = foreign_pending(db, principal)
                if rows:
                    return blocked_result(rows)
                return original(db, principal, args)

            guarded._tastegraph_dns_preflight = True
            return guarded

        setattr(mcp_v2, handler_name, make_principal_guard(original_handler))

    current_get_concept = mcp_v2._get_concept
    if not getattr(current_get_concept, "_tastegraph_dns_preflight", False):
        original_get_concept = current_get_concept

        def guarded_get_concept(db, args):
            rows = foreign_pending(db, principal_context.get())
            if rows:
                return blocked_result(rows)
            return original_get_concept(db, args)

        guarded_get_concept._tastegraph_dns_preflight = True
        mcp_v2._get_concept = guarded_get_concept

    # The underlying v2 service historically revived an identical rejected proposal by
    # changing it back to pending. Guard the MCP write path so rejection stays terminal.
    current = mcp_v2.propose_concept_fields
    if not getattr(current, "_tastegraph_rejection_guard", False):
        original = current

        def guarded_propose_concept_fields(db, *, concept, proposals, proposer_client_id):
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

            return original(db, concept=concept, proposals=proposals, proposer_client_id=proposer_client_id)

        guarded_propose_concept_fields._tastegraph_rejection_guard = True
        mcp_v2.propose_concept_fields = guarded_propose_concept_fields


__all__ = ["apply_mcp_v2_autonomy_policy"]
