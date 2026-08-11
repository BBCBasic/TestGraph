from __future__ import annotations

"""Runtime policy tuning for TasteGraph MCP vocabulary governance.

AI clients may propose vocabulary, but they do not approve or reject one another's
schema changes. TasteGraph applies deterministic server-side rules. The normal proposal
outcomes are deliberately simple words: accepted, revise, rejected, review.

A proposal that can be repaired is returned as revise with concrete guidance and an
explicit instruction to resubmit. Human/admin review is an escalation after a materially
revised proposal still cannot satisfy the rules, not the first destination for ambiguity.
Unrelated vocabulary work never blocks normal user work.
"""


def apply_mcp_v2_autonomy_policy() -> None:
    # Import lazily to avoid creating an import cycle while app.api.mcp_v2 is loaded.
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.api import mcp_v2
    from app.models.v2 import Concept, ConceptFieldProposal
    from app.schemas.v2 import ConceptEnsure
    from app.services.concept_placement import resolve_concept_path, validate_review_save_path
    from app.services.v2 import normalise_path, normalise_token, vocabulary
    from app.services.vocabulary_governance import ensure_proposed_concept, verify_field_proposal

    mcp_v2.SERVER_VERSION = "2.7.0-alpha"

    governance_policy = (
        "AI clients propose vocabulary; TasteGraph governs it with deterministic server-side rules. "
        "AI peer approval and peer rejection are not part of the governance model. "
        "Proposal outcomes are accepted, revise, rejected or review. "
        "revise means the idea may be sound but the submitted design is not acceptable yet: read every returned reason, materially improve the proposal and resubmit it. "
        "rejected is terminal for substantially the same design. review is exceptional human/admin escalation and is reached only after a materially revised proposal still cannot satisfy the published rules. "
        "Pending or unresolved vocabulary never forms a global lock and must not block unrelated user work. "
        "DNS concept hierarchy uses dots only; hierarchy boundaries must never use underscores. "
    )

    hidden_peer_tools = {"verify_concept_field_proposal", "reject_concept_field_proposal"}
    mcp_v2.TOOLS[:] = [tool for tool in mcp_v2.TOOLS if tool.get("name") not in hidden_peer_tools]

    descriptions = {
        "pending_vocabulary_proposals": (
            governance_policy
            + "Inspect unresolved proposals for context only. Do not approve or reject another AI's proposal. "
            "Use the returned reasons/status to avoid repeating a known bad design."
        ),
        "propose_concept_fields": (
            governance_policy
            + "Before creating any concept node, inspect the canonical vocabulary index for the proposed words. "
            "Reuse an established compatible word position and extend beneath it instead of creating a parallel branch. "
            "A new review concept must be domain rooted, for example food.recipe.review rather than recipe.review. "
            "Propose the smallest broadly reusable field set that materially improves future search, comparison, recommendation or personalisation. "
            "Check existing canonical fields, aliases and ancestors first. Preserve one-off or narrative detail in raw_text. "
            "Measurements and money must be machine-readable. Review concepts must not introduce generic rating, score, stars, sentiment or satisfaction fields. "
            "If TasteGraph returns revise, correct every listed issue and resubmit the materially improved proposal before any human escalation."
        ),
        "save_experience": (
            "Save a direct user experience only after explicit user approval. The concept must be a specific domain-rooted review leaf, never a broad ancestor, and every structured field used by this save must already be canonical. "
            "Unrelated vocabulary proposals must never block the save. If this exact save depends on non-canonical vocabulary, propose the required durable schema and follow TasteGraph's accepted/revise/rejected/review result. "
            "Do not ask the user to repeat approval while the review content and meaning are unchanged."
        ),
        "search": "Search the connected user's direct experiences across any domain. Vocabulary governance does not block reads.",
        "fetch": "Fetch one complete direct experience, including submitted and canonical structured data. Vocabulary governance does not block reads.",
        "get_concept": "Inspect canonical concept vocabulary and relevant unresolved proposals. Unrelated proposals do not block this lookup.",
        "propose_alias": (
            governance_policy
            + "Propose an alias only for an existing canonical field. TasteGraph validates collisions and promotes clear aliases deterministically; no second AI vote is required."
        ),
        "save_assessment": "Save AI-derived analysis against an experience. Vocabulary governance does not block this action.",
        "vocabulary_index": (
            "Inspect the global DNS vocabulary index and unresolved proposal positions. DNS concept hierarchy is dot-separated only; ordinary field names and aliases may retain their established token format."
        ),
    }

    for tool in mcp_v2.TOOLS:
        description = descriptions.get(tool.get("name"))
        if description:
            tool["description"] = description

    def peer_review_disabled(db, principal, args):
        return mcp_v2._error(
            "AI peer vocabulary review is disabled",
            {
                "governance": "server",
                "instruction": "Use propose_concept_fields. TasteGraph will return accepted, revise, rejected or review and will provide resubmission guidance when revision is possible.",
            },
        )

    mcp_v2._verify_concept_field_proposal = peer_review_disabled
    mcp_v2._reject_concept_field_proposal = peer_review_disabled

    current_handler = mcp_v2._propose_concept_fields
    if not getattr(current_handler, "_tastegraph_server_governance", False):

        def governed_propose_concept_fields(db, principal, args):
            raw_fields = args.get("fields", [])
            if not raw_fields:
                return mcp_v2._error("At least one field proposal is required")

            submitted_path = normalise_path(str(args["concept_path"]))
            placement = resolve_concept_path(db, submitted_path)
            if placement["status"] == "revise":
                return mcp_v2._result({
                    "concept_path": submitted_path,
                    "status": "revise",
                    "decisions": [],
                    "peer_review_required": False,
                    "experience_created": False,
                    "placement": placement,
                    "instruction": "Revise the concept path using the vocabulary guidance and resubmit. Do not create a new parallel root or ask for routine human approval.",
                })

            path = placement["path"]
            existing_context = mcp_v2._existing_vocabulary_for_path(db, path)
            existing_vocab = vocabulary(db, existing_context) if existing_context else {"fields": {}, "aliases": {}}
            existing_field_names = set(existing_vocab["fields"].keys())
            existing_aliases = set(existing_vocab["aliases"].keys())
            client_id = f"{principal.client_id}:v2"
            decisions = []
            seen_batch: set[str] = set()
            concept = None

            def get_concept():
                nonlocal concept
                if concept is None:
                    concept = ensure_proposed_concept(
                        db,
                        ConceptEnsure(
                            path=path,
                            description=args.get("concept_description"),
                            created_by=client_id,
                        ),
                    )
                return concept

            for raw in raw_fields:
                canonical_name = str(raw.get("canonical_name", "")).strip()
                canonical_key = normalise_token(canonical_name)
                aliases = [normalise_token(str(alias)) for alias in raw.get("aliases", []) if str(alias).strip()]
                issues = list(mcp_v2._proposal_quality_issues(raw))
                hard_reasons: list[str] = []

                if not canonical_key:
                    hard_reasons.append("canonical_name must contain at least one alphanumeric character")
                if canonical_key in seen_batch:
                    hard_reasons.append("the same canonical field appears more than once in this proposal batch")
                seen_batch.add(canonical_key)

                review_path = "review" in set(path.split("."))
                prohibited_review_tokens = {"rating", "ratings", "score", "scores", "star", "stars", "sentiment", "satisfaction"}
                name_tokens = set(part for part in canonical_key.split("_") if part)
                if review_path and name_tokens & prohibited_review_tokens:
                    hard_reasons.append(
                        "review concepts must not add generic rating, score, stars, sentiment or satisfaction fields; preserve direct user evidence in the experience and AI interpretation in assessments"
                    )

                if canonical_key in existing_field_names:
                    hard_reasons.append("an existing canonical field already covers this field name/meaning")
                if canonical_key in existing_aliases:
                    hard_reasons.append("this name is already an accepted alias for existing canonical vocabulary")
                if any(alias in existing_field_names or alias in existing_aliases for alias in aliases):
                    hard_reasons.append("one or more proposed aliases collide with existing canonical vocabulary")

                if hard_reasons:
                    decisions.append({
                        "canonical_name": canonical_name,
                        "status": "rejected",
                        "reasons": hard_reasons,
                        "resubmit": False,
                        "instruction": "Do not resubmit substantially the same design. Reuse the existing vocabulary or choose a materially different model that obeys the rule.",
                    })
                    continue

                target_concept = get_concept()
                existing = db.scalar(
                    select(ConceptFieldProposal).where(
                        ConceptFieldProposal.concept_id == target_concept.id,
                        ConceptFieldProposal.canonical_name_normalized == canonical_key,
                    )
                )

                if existing and existing.status == "rejected":
                    decisions.append({
                        "proposal_id": str(existing.id),
                        "canonical_name": canonical_name,
                        "status": "rejected",
                        "reasons": [existing.decision_reason or "A substantially identical design was already rejected."],
                        "resubmit": False,
                    })
                    continue
                if existing and existing.status in {"accepted", "approved"}:
                    decisions.append({
                        "proposal_id": str(existing.id),
                        "canonical_name": canonical_name,
                        "status": "accepted",
                        "reasons": ["This proposal is already canonical."],
                        "resubmit": False,
                    })
                    continue
                if existing and existing.status == "review":
                    decisions.append({
                        "proposal_id": str(existing.id),
                        "canonical_name": canonical_name,
                        "status": "review",
                        "reasons": [existing.decision_reason or "This proposal has already been escalated for human/admin review."],
                        "resubmit": False,
                        "manual_review_url": f"{mcp_v2._base()}/development/concept-fields",
                    })
                    continue

                materially_changed = False
                if existing:
                    materially_changed = any((
                        existing.submitted_name != raw.get("submitted_name"),
                        existing.canonical_name != canonical_name,
                        existing.json_schema != (raw.get("json_schema") or {}),
                        existing.description != raw.get("description"),
                        existing.aliases_json != raw.get("aliases", []),
                    ))

                if issues:
                    status = "revise"
                    if existing and existing.status == "revise" and materially_changed:
                        status = "review"
                    reason_text = "; ".join(issues)
                    if existing:
                        existing.submitted_name = str(raw.get("submitted_name", ""))
                        existing.canonical_name = canonical_name
                        existing.canonical_name_normalized = canonical_key
                        existing.json_schema = raw.get("json_schema") or {}
                        existing.description = raw.get("description")
                        existing.aliases_json = raw.get("aliases", [])
                        existing.status = status
                        existing.decision_by = "tastegraph-policy"
                        existing.decision_reason = reason_text
                        existing.decided_at = datetime.now(timezone.utc)
                        row = existing
                    else:
                        row = ConceptFieldProposal(
                            concept_id=target_concept.id,
                            submitted_name=str(raw.get("submitted_name", "")),
                            canonical_name=canonical_name,
                            canonical_name_normalized=canonical_key,
                            json_schema=raw.get("json_schema") or {},
                            description=raw.get("description"),
                            aliases_json=raw.get("aliases", []),
                            proposer_client_id=client_id,
                            status=status,
                            decision_by="tastegraph-policy",
                            decision_reason=reason_text,
                            decided_at=datetime.now(timezone.utc),
                        )
                        db.add(row)
                    db.commit()
                    db.refresh(row)
                    decision = {
                        "proposal_id": str(row.id),
                        "canonical_name": canonical_name,
                        "status": status,
                        "reasons": issues,
                        "resubmit": status == "revise",
                    }
                    if status == "revise":
                        decision["instruction"] = "Correct every listed issue and resubmit a materially improved proposal. Human review is not required yet."
                    else:
                        decision["instruction"] = "A materially revised submission still fails deterministic rules, so this proposal now requires human/admin review."
                        decision["manual_review_url"] = f"{mcp_v2._base()}/development/concept-fields"
                    decisions.append(decision)
                    continue

                if existing:
                    existing.submitted_name = str(raw.get("submitted_name", ""))
                    existing.canonical_name = canonical_name
                    existing.canonical_name_normalized = canonical_key
                    existing.json_schema = raw.get("json_schema") or {}
                    existing.description = raw.get("description")
                    existing.aliases_json = raw.get("aliases", [])
                    existing.proposer_client_id = client_id
                    existing.status = "pending"
                    existing.decision_by = None
                    existing.decision_reason = None
                    existing.decided_at = None
                    db.commit()
                    row = existing
                else:
                    row = ConceptFieldProposal(
                        concept_id=target_concept.id,
                        submitted_name=str(raw.get("submitted_name", "")),
                        canonical_name=canonical_name,
                        canonical_name_normalized=canonical_key,
                        json_schema=raw.get("json_schema") or {},
                        description=raw.get("description"),
                        aliases_json=raw.get("aliases", []),
                        proposer_client_id=client_id,
                        status="pending",
                    )
                    db.add(row)
                    db.commit()
                    db.refresh(row)

                try:
                    proposal, field = verify_field_proposal(
                        db,
                        row.id,
                        verifier_client_id="tastegraph-policy",
                        reason="Accepted by deterministic TasteGraph vocabulary rules.",
                    )
                except ValueError as exc:
                    db.rollback()
                    row = db.get(ConceptFieldProposal, row.id)
                    if row:
                        row.status = "revise"
                        row.decision_by = "tastegraph-policy"
                        row.decision_reason = str(exc)
                        row.decided_at = datetime.now(timezone.utc)
                        db.commit()
                    decisions.append({
                        "proposal_id": str(row.id) if row else None,
                        "canonical_name": canonical_name,
                        "status": "revise",
                        "reasons": [str(exc)],
                        "resubmit": True,
                        "instruction": "Correct the schema error and resubmit a materially improved proposal. Human review is not required yet.",
                    })
                    continue

                proposal.status = "accepted"
                proposal.decision_by = "tastegraph-policy"
                proposal.decision_reason = "Accepted by deterministic TasteGraph vocabulary rules."
                proposal.decided_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(proposal)
                decisions.append({
                    "proposal_id": str(proposal.id),
                    "field_id": str(field.id),
                    "canonical_name": field.canonical_name,
                    "status": "accepted",
                    "reasons": [proposal.decision_reason],
                    "resubmit": False,
                })

            statuses = {item["status"] for item in decisions}
            overall = "accepted" if statuses == {"accepted"} else "rejected" if statuses == {"rejected"} else "review" if "review" in statuses else "revise" if "revise" in statuses else "accepted"
            return mcp_v2._result({
                "concept_path": path,
                "submitted_concept_path": submitted_path,
                "status": overall,
                "decisions": decisions,
                "peer_review_required": False,
                "experience_created": False,
                "placement": placement,
                "instruction": "For revise decisions, correct every returned reason and resubmit the materially improved field. Do not send a revise decision to the user for routine approval. review is reserved for exceptional human/admin escalation.",
            })

        governed_propose_concept_fields._tastegraph_server_governance = True
        mcp_v2._propose_concept_fields = governed_propose_concept_fields


    current_save_handler = mcp_v2._save_experience
    if not getattr(current_save_handler, "_tastegraph_review_path_governance", False):

        def governed_save_experience(db, principal, args):
            submitted_path = normalise_path(str(args.get("concept_path", "")))
            placement = validate_review_save_path(db, submitted_path)
            if placement["status"] == "revise":
                return mcp_v2._error(
                    "Review concept path must be revised before saving",
                    {
                        "status": "revise",
                        "concept_path": submitted_path,
                        "placement": placement,
                        "experience_created": False,
                        "instruction": (
                            "Inspect vocabulary_index, choose or create the specific "
                            "domain.subject.review leaf, then resubmit the unchanged "
                            "approved review. Never fall back to a broad ancestor."
                        ),
                    },
                )
            return current_save_handler(db, principal, args)

        governed_save_experience._tastegraph_review_path_governance = True
        mcp_v2._save_experience = governed_save_experience


__all__ = ["apply_mcp_v2_autonomy_policy"]
