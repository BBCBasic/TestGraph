from __future__ import annotations

from datetime import datetime, timezone

from app.models.v2 import V2Subject
from app.services.client_identity import canonical_client_identity


CLASSIFICATION_PROPOSAL_KEY = "classification_proposal"


def without_classification_proposal(provenance: dict | None) -> dict:
    """Return caller provenance without the server-owned proposal record."""
    sanitized = dict(provenance or {})
    sanitized.pop(CLASSIFICATION_PROPOSAL_KEY, None)
    return sanitized


def record_classification_proposal(
    subject: V2Subject,
    *,
    source_client: str,
    source_model: str | None = None,
    identity_basis: str = "authenticated_client",
) -> dict:
    """Record the immutable creation proposal for a newly created subject."""
    provenance = without_classification_proposal(subject.provenance_json)
    proposal = {
        "proposed_type_id": str(subject.subject_type_id),
        "source_client": canonical_client_identity(source_client),
        "source_model": source_model,
        "identity_basis": identity_basis,
        "proposed_at": datetime.now(timezone.utc).isoformat(),
    }
    provenance[CLASSIFICATION_PROPOSAL_KEY] = proposal
    subject.provenance_json = provenance
    return proposal


def classification_proposal(subject: V2Subject) -> dict:
    """Return persisted creation-proposal attribution for classification audit output."""
    proposal = (subject.provenance_json or {}).get(CLASSIFICATION_PROPOSAL_KEY)
    if not isinstance(proposal, dict):
        return {}
    result = dict(proposal)
    result["source_client"] = canonical_client_identity(result.get("source_client"))
    return result
