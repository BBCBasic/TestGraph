from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.semantic import SemanticAliasProposal
from app.models.v2 import Concept, ConceptField, FieldAlias

ALIAS_PROMOTION_MIN_CLIENTS = 2


def normalise_token(value: str) -> str:
    import re
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def _field_by_canonical(db: Session, concept: Concept, canonical_name: str) -> ConceptField | None:
    target = normalise_token(canonical_name)
    fields = list(db.scalars(select(ConceptField).where(
        ConceptField.concept_id == concept.id,
        ConceptField.status == "active",
    )).all())
    matches = [field for field in fields if normalise_token(field.canonical_name) == target]
    if len(matches) > 1:
        raise ValueError(f"Canonical field '{canonical_name}' is ambiguous in concept '{concept.path}'")
    return matches[0] if matches else None


def alias_consensus_status(db: Session, concept: Concept, alias: str) -> dict[str, Any]:
    alias_key = normalise_token(alias)
    accepted = db.scalar(select(FieldAlias).where(
        FieldAlias.concept_id == concept.id,
        FieldAlias.alias_normalized == alias_key,
    ))
    if accepted:
        field = db.get(ConceptField, accepted.field_id)
        return {
            "alias": alias,
            "alias_normalized": alias_key,
            "status": "accepted",
            "canonical_name": field.canonical_name if field else None,
            "supporting_clients": None,
            "targets": [],
        }

    proposals = list(db.scalars(select(SemanticAliasProposal).where(
        SemanticAliasProposal.concept_id == concept.id,
        SemanticAliasProposal.alias_normalized == alias_key,
    )).all())
    by_target: dict[Any, set[str]] = defaultdict(set)
    target_names: dict[Any, str] = {}
    for proposal in proposals:
        by_target[proposal.target_field_id].add(proposal.proposer_client_id)
        field = db.get(ConceptField, proposal.target_field_id)
        if field:
            target_names[proposal.target_field_id] = field.canonical_name

    targets = [
        {
            "canonical_name": target_names.get(field_id),
            "supporting_clients": len(clients),
        }
        for field_id, clients in by_target.items()
    ]
    targets.sort(key=lambda x: (-x["supporting_clients"], x["canonical_name"] or ""))

    if len(by_target) > 1:
        status = "conflict"
    elif len(by_target) == 1:
        support = len(next(iter(by_target.values())))
        status = "supported" if support >= ALIAS_PROMOTION_MIN_CLIENTS else "proposed"
    else:
        status = "unseen"

    return {
        "alias": alias,
        "alias_normalized": alias_key,
        "status": status,
        "canonical_name": targets[0]["canonical_name"] if len(targets) == 1 else None,
        "supporting_clients": targets[0]["supporting_clients"] if len(targets) == 1 else 0,
        "targets": targets,
    }


def propose_alias(
    db: Session,
    *,
    concept: Concept,
    alias: str,
    canonical_name: str,
    proposer_client_id: str,
    confidence: float | None = None,
    rationale: str | None = None,
) -> dict[str, Any]:
    alias_key = normalise_token(alias)
    if not alias_key:
        raise ValueError("Alias must contain at least one alphanumeric character")
    field = _field_by_canonical(db, concept, canonical_name)
    if not field:
        raise ValueError(f"Canonical field '{canonical_name}' does not exist in concept '{concept.path}'")

    if alias_key == normalise_token(field.canonical_name):
        return {
            "alias": alias,
            "alias_normalized": alias_key,
            "status": "canonical",
            "canonical_name": field.canonical_name,
            "supporting_clients": None,
            "targets": [],
        }

    accepted = db.scalar(select(FieldAlias).where(
        FieldAlias.concept_id == concept.id,
        FieldAlias.alias_normalized == alias_key,
    ))
    if accepted:
        accepted_field = db.get(ConceptField, accepted.field_id)
        if accepted.field_id != field.id:
            raise ValueError(
                f"Alias '{alias}' is already accepted for canonical field "
                f"'{accepted_field.canonical_name if accepted_field else accepted.field_id}'"
            )
        return alias_consensus_status(db, concept, alias)

    vote = db.scalar(select(SemanticAliasProposal).where(
        SemanticAliasProposal.concept_id == concept.id,
        SemanticAliasProposal.alias_normalized == alias_key,
        SemanticAliasProposal.proposer_client_id == proposer_client_id,
    ))
    if vote:
        vote.alias = alias
        vote.target_field_id = field.id
        vote.confidence = confidence
        vote.rationale = rationale
    else:
        db.add(SemanticAliasProposal(
            concept_id=concept.id,
            alias=alias,
            alias_normalized=alias_key,
            target_field_id=field.id,
            proposer_client_id=proposer_client_id,
            confidence=confidence,
            rationale=rationale,
        ))
    db.flush()

    status = alias_consensus_status(db, concept, alias)
    if status["status"] == "supported" and len(status["targets"]) == 1:
        db.add(FieldAlias(
            concept_id=concept.id,
            field_id=field.id,
            alias=alias,
            alias_normalized=alias_key,
            confidence=1.0,
            source=f"client_consensus:{status['supporting_clients']}",
        ))
        db.flush()
        status = alias_consensus_status(db, concept, alias)
        status["promoted_by_consensus"] = True
    else:
        status["promoted_by_consensus"] = False
    return status


def list_alias_candidates(db: Session, concept: Concept) -> list[dict[str, Any]]:
    keys = list(db.scalars(select(SemanticAliasProposal.alias_normalized).where(
        SemanticAliasProposal.concept_id == concept.id,
    ).distinct()).all())
    return [alias_consensus_status(db, concept, key) for key in sorted(keys)]
