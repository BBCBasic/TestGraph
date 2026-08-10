from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import Assessment, Concept, ConceptField, ConceptFieldProposal, FieldAlias, Source, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ConceptEnsure, ExperienceCreate, FieldProposal, SourceCreate, SubjectEnsure
from app.services.semantic import propose_alias


def normalise_token(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def normalise_path(path: str) -> str:
    """Canonicalise DNS concept paths using dots as the only separator.

    Underscores, whitespace, hyphens and other punctuation in a concept path are
    treated as hierarchy separators. Field/alias normalisation is intentionally
    unchanged and may still use underscores.
    """
    value = path.strip().lower()
    value = re.sub(r"[^a-z0-9.]+", ".", value)
    value = re.sub(r"\.+", ".", value).strip(".")
    parts = value.split(".") if value else []
    if not parts or any(not part for part in parts):
        raise ValueError("Invalid concept path")
    return ".".join(parts)


def _concept(db: Session, path: str) -> Concept | None:
    return db.scalar(select(Concept).where(Concept.path == normalise_path(path), Concept.status == "active"))


def ensure_concept(db: Session, payload: ConceptEnsure) -> Concept:
    path = normalise_path(payload.path)
    existing = _concept(db, path)
    if existing:
        if payload.description and not existing.description:
            existing.description = payload.description
        if payload.definition:
            merged = dict(existing.definition_json or {})
            merged.update(payload.definition)
            existing.definition_json = merged
        db.commit(); db.refresh(existing)
        return existing

    parent = None
    built: list[str] = []
    for part in path.split("."):
        built.append(part)
        current_path = ".".join(built)
        current = _concept(db, current_path)
        if not current:
            current = Concept(
                path=current_path,
                name=part,
                parent_id=parent.id if parent else None,
                description=payload.description if current_path == path else None,
                definition_json=payload.definition if current_path == path else {},
                created_by=payload.created_by,
            )
            db.add(current); db.flush()
        parent = current
    db.commit(); db.refresh(parent)
    return parent


def ensure_field(db: Session, concept: Concept, proposal: FieldProposal, source: str) -> ConceptField:
    """Create a canonical field only after an explicit development approval."""
    canonical = proposal.canonical_name.strip()
    canonical_key = normalise_token(canonical)
    if not canonical_key:
        raise ValueError("Canonical field name is invalid")
    try:
        Draft202012Validator.check_schema(proposal.json_schema)
    except SchemaError as exc:
        raise ValueError(f"Invalid JSON Schema for '{canonical}': {exc.message}") from exc

    existing_fields = list(db.scalars(select(ConceptField).where(
        ConceptField.concept_id == concept.id,
        ConceptField.status == "active",
    )).all())
    matches = [field for field in existing_fields if normalise_token(field.canonical_name) == canonical_key]
    if len(matches) > 1:
        raise ValueError(f"Canonical field '{canonical}' is ambiguous")
    if matches:
        return matches[0]

    field = ConceptField(
        concept_id=concept.id,
        canonical_name=canonical,
        data_type=str(proposal.json_schema.get("type", "any")),
        description=proposal.description,
        unit=None,
        allowed_values=list(proposal.json_schema.get("enum", [])),
        metadata_json={"json_schema": proposal.json_schema},
        introduced_version=concept.version + 1,
        created_by=source,
    )
    db.add(field)
    db.flush()
    concept.version += 1
    return field


def propose_concept_fields(
    db: Session,
    *,
    concept: Concept,
    proposals: list[FieldProposal],
    proposer_client_id: str,
) -> list[ConceptFieldProposal]:
    created: list[ConceptFieldProposal] = []
    active_names = {
        normalise_token(name)
        for name in vocabulary(db, concept)["fields"]
    }
    for proposal in proposals:
        canonical_key = normalise_token(proposal.canonical_name)
        if not canonical_key:
            raise ValueError("Canonical field name is invalid")
        if canonical_key in active_names:
            raise ValueError(f"Canonical field '{proposal.canonical_name}' already exists")
        try:
            Draft202012Validator.check_schema(proposal.json_schema)
        except SchemaError as exc:
            raise ValueError(
                f"Invalid JSON Schema for '{proposal.canonical_name}': {exc.message}"
            ) from exc

        existing = db.scalar(select(ConceptFieldProposal).where(
            ConceptFieldProposal.concept_id == concept.id,
            ConceptFieldProposal.canonical_name_normalized == canonical_key,
        ))
        if existing:
            same = (
                existing.submitted_name == proposal.submitted_name
                and existing.canonical_name == proposal.canonical_name
                and existing.json_schema == proposal.json_schema
                and existing.description == proposal.description
                and existing.aliases_json == proposal.aliases
            )
            if not same:
                raise ValueError(
                    f"A different proposal already exists for '{proposal.canonical_name}'"
                )
            if existing.status == "rejected":
                existing.status = "pending"
                existing.decision_by = None
                existing.decision_reason = None
                existing.decided_at = None
            created.append(existing)
            continue

        obj = ConceptFieldProposal(
            concept_id=concept.id,
            submitted_name=proposal.submitted_name,
            canonical_name=proposal.canonical_name,
            canonical_name_normalized=canonical_key,
            json_schema=proposal.json_schema,
            description=proposal.description,
            aliases_json=proposal.aliases,
            proposer_client_id=proposer_client_id,
            status="pending",
        )
        db.add(obj)
        db.flush()
        created.append(obj)
    db.commit()
    for obj in created:
        db.refresh(obj)
    return created


def list_field_proposals(
    db: Session,
    concept: Concept | None = None,
    *,
    status: str | None = None,
) -> list[ConceptFieldProposal]:
    stmt = select(ConceptFieldProposal)
    if concept is not None:
        stmt = stmt.where(ConceptFieldProposal.concept_id == concept.id)
    if status:
        stmt = stmt.where(ConceptFieldProposal.status == status)
    return list(db.scalars(stmt.order_by(ConceptFieldProposal.created_at)).all())


def approve_field_proposal(
    db: Session,
    proposal_id: uuid.UUID,
    *,
    decided_by: str = "development-user",
) -> ConceptField:
    proposal = db.get(ConceptFieldProposal, proposal_id)
    if not proposal:
        raise ValueError("Field proposal not found")
    if proposal.status == "rejected":
        raise ValueError("Rejected field proposals cannot be approved")
    concept = db.get(Concept, proposal.concept_id)
    if not concept:
        raise ValueError("Proposal concept not found")
    field = ensure_field(
        db,
        concept,
        FieldProposal(
            submitted_name=proposal.submitted_name,
            canonical_name=proposal.canonical_name,
            json_schema=proposal.json_schema,
            description=proposal.description,
            aliases=proposal.aliases_json,
        ),
        decided_by,
    )
    proposal.status = "approved"
    proposal.decision_by = decided_by
    proposal.decided_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(field)
    return field


def reject_field_proposal(
    db: Session,
    proposal_id: uuid.UUID,
    *,
    reason: str | None = None,
    decided_by: str = "development-user",
) -> ConceptFieldProposal:
    proposal = db.get(ConceptFieldProposal, proposal_id)
    if not proposal:
        raise ValueError("Field proposal not found")
    if proposal.status == "approved":
        raise ValueError("Approved field proposals cannot be rejected")
    proposal.status = "rejected"
    proposal.decision_by = decided_by
    proposal.decision_reason = reason
    proposal.decided_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(proposal)
    return proposal


def delete_field_proposal(db: Session, proposal_id: uuid.UUID) -> None:
    proposal = db.get(ConceptFieldProposal, proposal_id)
    if not proposal:
        raise ValueError("Field proposal not found")
    if proposal.status == "approved":
        raise ValueError("Approved proposals cannot be deleted while their canonical field exists")
    db.delete(proposal)
    db.commit()
def concept_chain(db: Session, concept: Concept) -> list[Concept]:
    chain = [concept]
    current = concept
    while current.parent_id:
        current = db.get(Concept, current.parent_id)
        if not current:
            break
        chain.append(current)
    chain.reverse()
    return chain


def vocabulary(db: Session, concept: Concept) -> dict[str, Any]:
    fields: dict[str, ConceptField] = {}
    aliases: dict[str, str] = {}
    origins: dict[str, str] = {}
    for node in concept_chain(db, concept):
        node_fields = list(db.scalars(select(ConceptField).where(
            ConceptField.concept_id == node.id,
            ConceptField.status == "active",
        )).all())
        for field in node_fields:
            key = normalise_token(field.canonical_name)
            if key in fields and fields[key].id != field.id:
                raise ValueError(f"Inherited canonical field collision for '{field.canonical_name}'")
            fields[key] = field
            origins[field.canonical_name] = node.path
        node_aliases = list(db.scalars(select(FieldAlias).where(FieldAlias.concept_id == node.id)).all())
        field_by_id = {field.id: field for field in node_fields}
        for alias in node_aliases:
            target = field_by_id.get(alias.field_id) or db.get(ConceptField, alias.field_id)
            if target:
                aliases[alias.alias_normalized] = target.canonical_name
    return {"fields": fields, "aliases": aliases, "origins": origins}


def normalise_data(
    db: Session,
    concept: Concept,
    data: dict[str, Any],
) -> tuple[dict, list]:
    vocab = vocabulary(db, concept)
    output: dict[str, Any] = {}
    log: list[dict] = []
    unknown: list[str] = []

    for submitted_name, value in data.items():
        key = normalise_token(submitted_name)
        field = None
        method = None
        if key in vocab["aliases"]:
            canonical = vocab["aliases"][key]
            field = vocab["fields"][normalise_token(canonical)]
            method = "accepted_alias"
        elif key in vocab["fields"]:
            field = vocab["fields"][key]
            canonical = field.canonical_name
            method = "canonical"
        else:
            unknown.append(submitted_name)
            continue

        schema = (field.metadata_json or {}).get("json_schema")
        if schema:
            try:
                Draft202012Validator(schema).validate(value)
            except ValidationError as exc:
                location = ".".join(str(part) for part in exc.absolute_path)
                suffix = f" at {location}" if location else ""
                raise ValueError(
                    f"Invalid value for canonical field '{canonical}'{suffix}: {exc.message}"
                ) from exc

        if canonical in output and output[canonical] != value:
            raise ValueError(f"Conflicting values resolve to canonical field '{canonical}'")
        output[canonical] = value
        log.append({"submitted": submitted_name, "canonical": canonical, "method": method})

    if unknown:
        available = sorted({field.canonical_name for field in vocab["fields"].values()})
        raise ValueError({
            "message": (
                "Unknown fields are not accepted during experience writes. "
                "Use propose_concept_fields first, then wait for explicit approval."
            ),
            "unknown_fields": unknown,
            "available_canonical_fields": available,
        })
    return output, log
def ensure_subject(db: Session, payload: SubjectEnsure, created_by: str = "ai-client") -> V2Subject:
    concept = _concept(db, payload.concept_path)
    if not concept:
        if not payload.create_concept_if_missing:
            raise ValueError("Concept does not exist")
        concept = ensure_concept(db, ConceptEnsure(path=payload.concept_path, created_by=created_by))
    subject = db.scalar(select(V2Subject).where(
        V2Subject.concept_id == concept.id,
        V2Subject.canonical_key == payload.canonical_key,
        V2Subject.deleted_at.is_(None),
    ))
    if subject:
        if payload.identifiers:
            merged = dict(subject.identifiers_json or {}); merged.update(payload.identifiers); subject.identifiers_json = merged
        if payload.attributes:
            merged = dict(subject.attributes_json or {}); merged.update(payload.attributes); subject.attributes_json = merged
        db.commit(); db.refresh(subject)
        return subject
    subject = V2Subject(
        concept_id=concept.id,
        name=payload.name,
        canonical_key=payload.canonical_key,
        identifiers_json=payload.identifiers,
        attributes_json=payload.attributes,
    )
    db.add(subject); db.commit(); db.refresh(subject)
    return subject


def ensure_source(db: Session, payload: SourceCreate | None) -> Source | None:
    if not payload:
        return None
    if payload.external_id:
        existing = db.scalar(select(Source).where(Source.provider == payload.provider, Source.external_id == payload.external_id))
        if existing:
            return existing
    obj = Source(**payload.model_dump(mode="json"))
    db.add(obj); db.flush()
    return obj


def create_experience(db: Session, payload: ExperienceCreate, client_id: str) -> V2Experience:
    if not payload.user_approved:
        raise ValueError("Explicit user approval is required before saving a direct user experience")
    subject = db.get(V2Subject, payload.subject_id)
    if not subject or subject.deleted_at:
        raise ValueError("Subject not found")
    concept = db.get(Concept, subject.concept_id)
    if not concept:
        raise ValueError("Subject concept not found")
    normalised, log = normalise_data(db, concept, payload.structured_data)
    source = ensure_source(db, payload.source)
    obj = V2Experience(
        owner_id=payload.owner_id,
        subject_id=subject.id,
        source_id=source.id if source else None,
        experienced_at=payload.experienced_at,
        headline=payload.headline,
        summary=payload.summary,
        raw_text=payload.raw_text,
        structured_data=normalised,
        submitted_data=payload.structured_data,
        normalization_log=log,
        visibility=payload.visibility,
        publication_status="published",
        provenance={"kind": "direct_user_experience", "source_client": payload.source_client},
        created_by_client=client_id,
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def create_assessment(
    db: Session,
    payload: AssessmentCreate,
    *,
    client_id: str,
    user_id: uuid.UUID | None = None,
) -> Assessment:
    experience = db.get(V2Experience, payload.experience_id)
    if not experience or experience.deleted_at:
        raise ValueError("Experience not found")
    if user_id is not None and experience.owner_id != user_id:
        raise ValueError("Experience not found")
    subject = db.get(V2Subject, experience.subject_id)
    if not subject or subject.deleted_at:
        raise ValueError("Experience subject not found")
    obj = Assessment(
        subject_id=experience.subject_id,
        experience_id=experience.id,
        user_id=experience.owner_id,
        assessment_type=payload.assessment_type,
        evidence_json=payload.evidence,
        analysis_json=payload.analysis,
        conclusion=payload.conclusion,
        confidence=payload.confidence,
        source_model=payload.source_model,
        provenance={
            **payload.provenance,
            "kind": "ai_derived_assessment",
            "target_experience_id": str(experience.id),
            "source_client": client_id,
        },
        created_by_client=client_id,
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj
