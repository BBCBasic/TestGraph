from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import Concept, ConceptField, FieldAlias


def normalise_token(value: str) -> str:
    import re
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def _concept_chain(db: Session, concept: Concept) -> list[Concept]:
    chain = [concept]
    current = concept
    while current.parent_id:
        current = db.get(Concept, current.parent_id)
        if not current:
            break
        chain.append(current)
    chain.reverse()
    return chain


def _field_by_canonical(db: Session, concept: Concept, canonical_name: str) -> ConceptField | None:
    target = normalise_token(canonical_name)
    matches: list[ConceptField] = []
    for node in _concept_chain(db, concept):
        fields = list(db.scalars(select(ConceptField).where(
            ConceptField.concept_id == node.id,
            ConceptField.status == "active",
        )).all())
        matches.extend(field for field in fields if normalise_token(field.canonical_name) == target)
    if len(matches) > 1:
        raise ValueError(f"Canonical field '{canonical_name}' is ambiguous in concept hierarchy '{concept.path}'")
    return matches[0] if matches else None


def _canonical_collision(db: Session, concept: Concept, alias_key: str, target_field: ConceptField) -> ConceptField | None:
    """Return another canonical field whose normalised name would collide with an alias."""
    for node in _concept_chain(db, concept):
        fields = list(db.scalars(select(ConceptField).where(
            ConceptField.concept_id == node.id,
            ConceptField.status == "active",
        )).all())
        for field in fields:
            if field.id != target_field.id and normalise_token(field.canonical_name) == alias_key:
                return field
    return None


def alias_consensus_status(db: Session, concept: Concept, alias: str) -> dict[str, Any]:
    """Compatibility name: alias status is now determined by server rules, not consensus."""
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
            "governance": "server",
        }
    return {
        "alias": alias,
        "alias_normalized": alias_key,
        "status": "unseen",
        "canonical_name": None,
        "governance": "server",
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
    """Apply deterministic alias rules; no second-AI vote is required."""
    alias_key = normalise_token(alias)
    if not alias_key:
        raise ValueError("Alias must contain at least one alphanumeric character")

    field = _field_by_canonical(db, concept, canonical_name)
    if not field:
        raise ValueError(f"Canonical field '{canonical_name}' does not exist in concept hierarchy '{concept.path}'")

    if alias_key == normalise_token(field.canonical_name):
        return {
            "alias": alias,
            "alias_normalized": alias_key,
            "status": "accepted",
            "canonical_name": field.canonical_name,
            "governance": "server",
            "reason": "Alias normalises to the canonical field name.",
        }

    accepted = db.scalar(select(FieldAlias).where(
        FieldAlias.concept_id == concept.id,
        FieldAlias.alias_normalized == alias_key,
    ))
    if accepted:
        accepted_field = db.get(ConceptField, accepted.field_id)
        if accepted.field_id != field.id:
            return {
                "alias": alias,
                "alias_normalized": alias_key,
                "status": "rejected",
                "canonical_name": canonical_name,
                "governance": "server",
                "reason": f"Alias is already accepted for canonical field '{accepted_field.canonical_name if accepted_field else accepted.field_id}'.",
                "resubmit": False,
            }
        return alias_consensus_status(db, concept, alias)

    collision = _canonical_collision(db, concept, alias_key, field)
    if collision:
        return {
            "alias": alias,
            "alias_normalized": alias_key,
            "status": "rejected",
            "canonical_name": canonical_name,
            "governance": "server",
            "reason": f"Alias collides with canonical field '{collision.canonical_name}'.",
            "resubmit": False,
        }

    rationale_text = (rationale or "").strip()
    if len(rationale_text) < 20:
        return {
            "alias": alias,
            "alias_normalized": alias_key,
            "status": "revise",
            "canonical_name": field.canonical_name,
            "governance": "server",
            "reason": "Explain in at least 20 characters why the alias has the same durable meaning as the canonical field.",
            "resubmit": True,
            "instruction": "Add a concrete semantic rationale and resubmit. Human review is not required yet.",
        }

    if confidence is not None and confidence < 0.8:
        return {
            "alias": alias,
            "alias_normalized": alias_key,
            "status": "revise",
            "canonical_name": field.canonical_name,
            "governance": "server",
            "reason": "Alias confidence is below the 0.8 deterministic acceptance threshold.",
            "resubmit": True,
            "instruction": "Only resubmit if stronger evidence supports the same meaning; otherwise leave the unfamiliar wording unaliased.",
        }

    db.add(FieldAlias(
        concept_id=concept.id,
        field_id=field.id,
        alias=alias,
        alias_normalized=alias_key,
        confidence=confidence if confidence is not None else 1.0,
        source=f"tastegraph-policy:{proposer_client_id}",
    ))
    db.flush()
    return {
        "alias": alias,
        "alias_normalized": alias_key,
        "status": "accepted",
        "canonical_name": field.canonical_name,
        "governance": "server",
        "reason": "Alias passed deterministic collision, target and evidence rules.",
        "resubmit": False,
    }


def list_alias_candidates(db: Session, concept: Concept) -> list[dict[str, Any]]:
    aliases = list(db.scalars(select(FieldAlias).where(
        FieldAlias.concept_id == concept.id,
    ).order_by(FieldAlias.alias_normalized)).all())
    result = []
    for alias in aliases:
        field = db.get(ConceptField, alias.field_id)
        result.append({
            "alias": alias.alias,
            "alias_normalized": alias.alias_normalized,
            "status": "accepted",
            "canonical_name": field.canonical_name if field else None,
            "governance": "server",
        })
    return result
