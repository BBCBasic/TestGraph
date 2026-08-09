from __future__ import annotations

import re
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import Concept, ConceptField, ConceptFieldProposal, FieldAlias
from app.schemas.v2 import ConceptEnsure
from app.services.v2 import approve_field_proposal, normalise_path, normalise_token


def _words(value: str) -> list[str]:
    """Return stable lower-case word tokens from a canonical/alias value."""
    return [part for part in re.split(r"[^a-z0-9]+", value.strip().lower()) if part]


def _add(index: dict[str, list[dict[str, Any]]], value: str, location: dict[str, Any]) -> None:
    for word in _words(value):
        item = {"word": word, **location}
        if item not in index[word]:
            index[word].append(item)


def ensure_proposed_concept(db: Session, payload: ConceptEnsure) -> Concept:
    """Locate a concept path, creating missing path nodes as pending.

    Proposal-time path creation must not make AI A's new vocabulary canonical.
    Existing active nodes remain active; only genuinely new nodes are pending.
    """
    path = normalise_path(payload.path)
    parent = None
    built: list[str] = []
    for part in path.split("."):
        built.append(part)
        current_path = ".".join(built)
        current = db.scalar(select(Concept).where(Concept.path == current_path))
        if not current:
            current = Concept(
                path=current_path,
                name=part,
                parent_id=parent.id if parent else None,
                description=payload.description if current_path == path else None,
                definition_json=payload.definition if current_path == path else {},
                created_by=payload.created_by,
                status="pending",
            )
            db.add(current)
            db.flush()
        elif current.status not in {"active", "pending"}:
            raise ValueError(f"Concept path '{current_path}' is not available for proposal")
        elif current_path == path:
            if payload.description and not current.description:
                current.description = payload.description
            if payload.definition:
                merged = dict(current.definition_json or {})
                merged.update(payload.definition)
                current.definition_json = merged
        parent = current
    db.commit(); db.refresh(parent)
    return parent


def _activate_concept_chain(db: Session, concept: Concept) -> None:
    current = concept
    while current:
        if current.status == "pending":
            current.status = "active"
        current = db.get(Concept, current.parent_id) if current.parent_id else None


def vocabulary_index(db: Session, word: str | None = None) -> dict[str, Any]:
    """Build a live word -> DNS-tree-position index from authoritative rows."""
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)

    concepts = list(db.scalars(
        select(Concept).where(Concept.status == "active").order_by(Concept.path)
    ).all())
    concept_by_id = {concept.id: concept for concept in concepts}

    for concept in concepts:
        for segment_index, segment in enumerate(concept.path.split(".")):
            _add(index, segment, {
                "kind": "concept_path_segment",
                "concept_id": str(concept.id),
                "concept_path": concept.path,
                "segment_index": segment_index,
                "surface": segment,
                "status": "active",
                "position": f"concept:{concept.id}:path:{segment_index}",
            })

    fields = list(db.scalars(
        select(ConceptField).where(ConceptField.status == "active").order_by(ConceptField.created_at)
    ).all())
    field_by_id = {field.id: field for field in fields}
    for field in fields:
        concept = concept_by_id.get(field.concept_id) or db.get(Concept, field.concept_id)
        if not concept or concept.status != "active":
            continue
        _add(index, field.canonical_name, {
            "kind": "canonical_field",
            "concept_id": str(concept.id),
            "concept_path": concept.path,
            "field_id": str(field.id),
            "canonical_name": field.canonical_name,
            "surface": field.canonical_name,
            "status": "active",
            "position": f"concept:{concept.id}:field:{field.id}",
        })

    aliases = list(db.scalars(select(FieldAlias).order_by(FieldAlias.created_at)).all())
    for alias in aliases:
        concept = concept_by_id.get(alias.concept_id) or db.get(Concept, alias.concept_id)
        field = field_by_id.get(alias.field_id) or db.get(ConceptField, alias.field_id)
        if not concept or concept.status != "active" or not field:
            continue
        _add(index, alias.alias, {
            "kind": "accepted_alias",
            "concept_id": str(concept.id),
            "concept_path": concept.path,
            "field_id": str(field.id),
            "canonical_name": field.canonical_name,
            "alias": alias.alias,
            "surface": alias.alias,
            "status": "active",
            "position": f"concept:{concept.id}:alias:{alias.id}",
        })

    proposals = list(db.scalars(
        select(ConceptFieldProposal)
        .where(ConceptFieldProposal.status == "pending")
        .order_by(ConceptFieldProposal.created_at)
    ).all())
    indexed_pending_concepts: set[uuid.UUID] = set()
    for proposal in proposals:
        concept = db.get(Concept, proposal.concept_id)
        if not concept:
            continue
        base = {
            "kind": "pending_field_proposal",
            "concept_id": str(concept.id),
            "concept_path": concept.path,
            "proposal_id": str(proposal.id),
            "canonical_name": proposal.canonical_name,
            "proposed_by": proposal.proposer_client_id,
            "status": proposal.status,
        }
        if concept.status == "pending" and concept.id not in indexed_pending_concepts:
            for segment_index, segment in enumerate(concept.path.split(".")):
                _add(index, segment, {
                    "kind": "pending_concept_path",
                    "concept_id": str(concept.id),
                    "concept_path": concept.path,
                    "proposal_id": str(proposal.id),
                    "proposed_by": proposal.proposer_client_id,
                    "segment_index": segment_index,
                    "surface": segment,
                    "status": "pending",
                    "position": f"proposal:{proposal.id}:concept_path:{segment_index}",
                })
            indexed_pending_concepts.add(concept.id)
        _add(index, proposal.submitted_name, {
            **base,
            "proposal_part": "submitted_name",
            "surface": proposal.submitted_name,
            "position": f"proposal:{proposal.id}:submitted_name",
        })
        _add(index, proposal.canonical_name, {
            **base,
            "proposal_part": "canonical_name",
            "surface": proposal.canonical_name,
            "position": f"proposal:{proposal.id}:canonical_name",
        })
        for alias_position, alias in enumerate(proposal.aliases_json or []):
            _add(index, alias, {
                **base,
                "proposal_part": "alias",
                "alias_index": alias_position,
                "surface": alias,
                "position": f"proposal:{proposal.id}:alias:{alias_position}",
            })

    if word is not None and word.strip():
        query_words = _words(word)
        if not query_words:
            return {"query": word, "words": [], "match_count": 0, "matches": []}
        selected: list[dict[str, Any]] = []
        for query_word in query_words:
            selected.extend(index.get(query_word, []))
        return {"query": word, "words": query_words, "match_count": len(selected), "matches": selected}

    rows = [{"word": key, "locations": locations} for key, locations in sorted(index.items())]
    return {"word_count": len(rows), "index": rows}


def verify_field_proposal(
    db: Session,
    proposal_id: uuid.UUID,
    *,
    verifier_client_id: str,
    reason: str | None = None,
) -> tuple[ConceptFieldProposal, ConceptField]:
    """Promote a pending concept/field only after independent AI verification."""
    proposal = db.get(ConceptFieldProposal, proposal_id)
    if not proposal:
        raise ValueError("Field proposal not found")
    if proposal.status == "rejected":
        raise ValueError("Rejected field proposals cannot be verified")
    if proposal.status == "pending" and proposal.proposer_client_id == verifier_client_id:
        raise ValueError("A client cannot verify its own field proposal")

    concept = db.get(Concept, proposal.concept_id)
    if not concept:
        raise ValueError("Proposal concept not found")

    if proposal.status == "approved":
        fields = list(db.scalars(select(ConceptField).where(
            ConceptField.concept_id == concept.id,
            ConceptField.status == "active",
        )).all())
        matches = [field for field in fields if normalise_token(field.canonical_name) == proposal.canonical_name_normalized]
        if len(matches) != 1:
            raise ValueError("Approved proposal does not resolve to exactly one canonical field")
        return proposal, matches[0]

    _activate_concept_chain(db, concept)
    db.flush()
    field = approve_field_proposal(db, proposal.id, decided_by=verifier_client_id)
    proposal = db.get(ConceptFieldProposal, proposal.id)
    if reason:
        proposal.decision_reason = reason
        db.commit(); db.refresh(proposal)
    return proposal, field
