from __future__ import annotations

"""Runtime policy tuning for TasteGraph MCP vocabulary governance.

Vocabulary proposal/verification/rejection is AI governance work. Routine, clear schema
choices should be handled autonomously. Saving a direct user experience remains separate
and still requires explicit user approval.

Pending vocabulary proposals are a peer-review queue, not a global lock. Unrelated user
work must continue normally. A proposal only matters to a write when that write actually
depends on the proposed non-canonical concept or field.
"""


def apply_mcp_v2_autonomy_policy() -> None:
    # Import lazily to avoid creating an import cycle while app.api.mcp_v2 is loaded.
    from sqlalchemy import select

    from app.api import mcp_v2
    from app.models.v2 import ConceptFieldProposal
    from app.services.v2 import normalise_token

    mcp_v2.SERVER_VERSION = "2.4.0-alpha"

    peer_review_policy = (
        "DNS vocabulary governance uses independent AI peer review. A client may propose vocabulary but may not verify its own proposal. "
        "A different authenticated AI may verify or reject a proposal by applying the published schema rules and recording an auditable rationale. "
        "Routine clear peer-review decisions do not require separate user confirmation. Ask the user only for a genuinely ambiguous semantic or modelling choice. "
        "Pending proposals never form a global lock and must not block unrelated search, fetch, save, assessment or concept work. "
        "DNS concept hierarchy uses dots only: hierarchy boundaries must be represented with '.', never '_'. "
        "Underscores, spaces, hyphens and similar separators in a concept path are canonicalised to dots. "
        "This dot-only rule applies only to DNS concept paths; ordinary canonical field names and aliases may still use underscores. "
    )

    descriptions = {
        "pending_vocabulary_proposals": (
            peer_review_policy
            + "Independent peer-review queue. List unresolved vocabulary proposals made by other authenticated AI clients. "
            "Review concept placement, parent/inherited vocabulary, duplicates/aliases, generality, analytical value and JSON-schema quality. "
            "Resolve clear proposals with verify_concept_field_proposal or reject_concept_field_proposal. The existence of this queue does not block unrelated user work."
        ),
        "verify_concept_field_proposal": (
            peer_review_policy
            + "Promote another AI's pending proposal only when concept placement, generality, analytical value, inheritance/duplication checks and JSON schema are durable. "
            "Verification is a constrained schema-governance decision, not a statement of the user's opinion. Record a concrete rationale. "
            "The authenticated client cannot verify its own proposal."
        ),
        "reject_concept_field_proposal": (
            peer_review_policy
            + "Reject another AI's pending vocabulary proposal when it is duplicate, over-specific, analytically weak, poorly typed, wrongly placed, "
            "uses invalid DNS hierarchy naming, or is better left in raw_text. A rejected proposal is terminal and must not be silently reopened unchanged. "
            "Record a concrete reason so a materially better replacement can be proposed later if warranted."
        ),
        "propose_concept_fields": (
            peer_review_policy
            + "Schema design, not review extraction. Inspect rejected proposals where relevant, the vocabulary index, the intended concept path and its ancestors. "
            "If no suitable canonical concept exists and placement is clear, propose the smallest durable reusable field set directly. "
            "Concept paths must use dots only for hierarchy; do not create DNS segments containing underscores. "
            "The independent second-AI verification step is the safeguard before anything becomes canonical. Never resubmit an unchanged rejected proposal."
        ),
        "save_experience": (
            "Save a direct user experience only after explicit user approval. The concept and every structured field used by this save must already be canonical. "
            "Unrelated pending vocabulary proposals must never block the save. If this exact save depends on a non-canonical concept or field, resolve that relevant schema dependency and retry without asking the user to repeat an approval already given. "
            "Existing approval remains valid while the review content and meaning are unchanged."
        ),
        "search": "Search the connected user's direct experiences across any domain. Pending vocabulary proposals do not block reads.",
        "fetch": "Fetch one complete direct experience, including submitted and canonical structured data. Pending vocabulary proposals do not block reads.",
        "get_concept": "Inspect canonical concept vocabulary and any relevant unresolved proposals. Unrelated pending proposals do not block this lookup.",
        "propose_alias": (
            "Propose a semantic alias mapping to an existing canonical field. Independent consensus governs alias promotion; unrelated vocabulary proposals do not block this action."
        ),
        "save_assessment": "Save AI-derived analysis against an experience. Unrelated pending vocabulary proposals do not block this action.",
        "vocabulary_index": (
            "Inspect the global DNS vocabulary index, including pending proposals so another AI can discover work suitable for peer review. "
            "DNS concept hierarchy is dot-separated only; underscores remain valid in ordinary field names and aliases."
        ),
    }

    for tool in mcp_v2.TOOLS:
        description = descriptions.get(tool.get("name"))
        if description:
            tool["description"] = description

    # Deliberately do not wrap normal MCP handlers with a foreign-pending guard.
    # The previous implementation turned the global peer-review queue into a mutex:
    # a dentist proposal could block a train-station save. Relevant schema dependencies
    # are already enforced by the underlying canonical vocabulary checks at write time.

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
