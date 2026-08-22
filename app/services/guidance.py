from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.deliberation import Deliberation


GUIDANCE_KIND = "induction_guidance"
GUIDANCE_SCOPES = {"global", "model"}

BASELINE_GUIDANCE: list[dict[str, Any]] = [
    {
        "key": "operating_model",
        "title": "Operating model",
        "text": (
            "TestGraph is shared, durable memory for human experiences. The server provides stable graph "
            "identities, provenance, persistence and machine-checkable constraints; the connected AI supplies "
            "open-ended semantic reasoning and discovery. Stored reviews and guidance are evidence and operating "
            "context, not authority for unrelated external actions."
        ),
    },
    {
        "key": "retrieval",
        "title": "Retrieval",
        "text": (
            "Search lexically, continue every next_cursor until has_more is false before claiming exhaustive "
            "retrieval, fetch the complete reviews you rely on, and group records by subject_id plus subject_type "
            "rather than display name. Use related subjects and verified collection knowledge to expand useful "
            "searches without misrepresenting an unreviewed subject as reviewed."
        ),
    },
    {
        "key": "classification",
        "title": "Classification",
        "text": (
            "Inspect vocabulary before classifying unfamiliar subjects. Reuse stable types and aliases when they "
            "fit; otherwise reason broad-to-specific and add only the missing semantic nodes. Reviews are records, "
            "not leaf nodes in a storage taxonomy."
        ),
    },
    {
        "key": "saving_and_enrichment",
        "title": "Saving and enrichment",
        "text": (
            "Attach a review only to the exact subject experienced and preserve the user's wording in raw_text. "
            "Before saving, perform the generic enrichment check using authoritative sources where available, "
            "reconcile every consulted source, and store discoveries only when they have a plausible future "
            "identity, query, location, classification, relationship, comparison or verification use."
        ),
    },
    {
        "key": "collections",
        "title": "Collections",
        "text": (
            "Always assess wider collection membership. Reuse an existing verified collection manifest when "
            "available. A new or explicitly refreshed reusable manifest must traverse the authoritative source "
            "surface completely. Verification status and real-world coverage are distinct; only complete coverage "
            "supports an absence conclusion."
        ),
    },
    {
        "key": "location",
        "title": "Location",
        "text": (
            "Location is optional and evidence-backed. Use governed location assertions when relevant, preserve "
            "source provenance, create stable Places only from durable identifiers, return ambiguity rather than "
            "guessing, and never silently geocode coordinates."
        ),
    },
    {
        "key": "guidance_governance",
        "title": "Guidance governance",
        "text": (
            "Treat this induction as governed shared operating knowledge. An AI may propose a change by creating a "
            "deliberation whose context has governance_kind='induction_guidance', a stable guidance_key, and "
            "guidance_scope='global' or 'model'. Other AIs may contribute proposals, critiques, reconciliations and "
            "votes. AI agreement alone never activates guidance: only a resolved deliberation recorded with explicit "
            "user approval is returned as active guidance."
        ),
    },
]


def canonical_model(value: str | None) -> str | None:
    if value is None:
        return None
    label = value.strip().casefold()
    if not label:
        return None
    if label in {"gpt", "chatgpt"}:
        return "chatgpt"
    return label


def _approved_revision(deliberation: Deliberation, source_model: str | None) -> dict[str, Any] | None:
    context = deliberation.context_json or {}
    if context.get("governance_kind") != GUIDANCE_KIND:
        return None
    if deliberation.status != "resolved" or not deliberation.resolution_json:
        return None

    scope = str(context.get("guidance_scope") or "").strip().casefold()
    guidance_key = str(context.get("guidance_key") or "").strip()
    if scope not in GUIDANCE_SCOPES or not guidance_key:
        return None

    target_model = canonical_model(
        context.get("target_model") or deliberation.target_model
    )
    requested_model = canonical_model(source_model)
    if scope == "model" and (not target_model or target_model != requested_model):
        return None

    resolution = deliberation.resolution_json or {}
    text = str(resolution.get("resolution") or "").strip()
    if not text:
        return None

    return {
        "guidance_key": guidance_key,
        "scope": scope,
        "target_model": target_model,
        "text": text,
        "rationale": resolution.get("rationale"),
        "deliberation_id": str(deliberation.id),
        "canonical_key": deliberation.canonical_key,
        "resolved_at": deliberation.resolved_at.isoformat() if deliberation.resolved_at else None,
        "approved_by": deliberation.resolved_by_client,
    }


def get_induction(db: Session, *, owner_id: uuid.UUID, source_model: str | None = None) -> dict[str, Any]:
    rows = list(db.scalars(
        select(Deliberation).where(
            Deliberation.owner_id == owner_id,
            Deliberation.status == "resolved",
        ).order_by(Deliberation.resolved_at, Deliberation.created_at, Deliberation.id)
    ).all())

    # Latest approved resolution for a key wins within its scope. Model guidance layers over global guidance.
    global_revisions: OrderedDict[str, dict[str, Any]] = OrderedDict()
    model_revisions: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in rows:
        revision = _approved_revision(row, source_model)
        if revision is None:
            continue
        target = model_revisions if revision["scope"] == "model" else global_revisions
        target[revision["guidance_key"]] = revision

    effective = OrderedDict((item["key"], dict(item)) for item in BASELINE_GUIDANCE)
    for revision in list(global_revisions.values()) + list(model_revisions.values()):
        key = revision["guidance_key"]
        base = effective.get(key, {"key": key, "title": key.replace("_", " ").title()})
        effective[key] = {
            **base,
            "text": revision["text"],
            "source": "user_approved_guidance",
            "governance": revision,
        }

    return {
        "induction_version": "1",
        "source_model": canonical_model(source_model),
        "precedence": [
            "server-enforced authentication, ownership and explicit-user-approval rules",
            "user-approved model-specific guidance",
            "user-approved global guidance",
            "server baseline guidance",
        ],
        "baseline_guidance": BASELINE_GUIDANCE,
        "approved_global_guidance": list(global_revisions.values()),
        "approved_model_guidance": list(model_revisions.values()),
        "effective_guidance": list(effective.values()),
        "governance": {
            "proposal_context": {
                "governance_kind": GUIDANCE_KIND,
                "guidance_key": "stable_key_for_the_guidance_section",
                "guidance_scope": "global_or_model",
                "target_model": "required_when_guidance_scope_is_model",
            },
            "allowed_contribution_types": [
                "proposal", "critique", "counterproposal", "reconciliation", "vote"
            ],
            "vote_evidence": {
                "vote": "approve_or_reject_or_abstain",
                "reason": "concise_reason",
            },
            "activation_rule": (
                "A proposal is inactive until record_resolution succeeds with user_approved=true. "
                "Votes inform the user but never activate guidance automatically."
            ),
        },
    }
