from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2 import Assessment, Concept, ConceptField, FieldAlias, Source, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ConceptEnsure, ExperienceCreate, FieldProposal, SourceCreate, SubjectEnsure
from app.services.semantic import propose_alias


def normalise_token(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def normalise_path(path: str) -> str:
    parts = [normalise_token(part) for part in path.strip().split(".") if part.strip()]
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
        for proposal in payload.proposed_fields:
            ensure_field(db, existing, proposal, payload.created_by)
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
    for proposal in payload.proposed_fields:
        ensure_field(db, parent, proposal, payload.created_by)
    db.commit(); db.refresh(parent)
    return parent


def ensure_field(db: Session, concept: Concept, proposal: FieldProposal, source: str) -> ConceptField:
    """Ensure a canonical field exists.

    This function deliberately does NOT turn submitted names or suggested aliases
    into accepted aliases. Semantic equivalence is proposed by authenticated AI
    clients and promoted only through the consensus service.
    """
    canonical = proposal.canonical_name.strip()
    canonical_key = normalise_token(canonical)
    if not canonical_key:
        raise ValueError("Canonical field name is invalid")
    existing_fields = list(db.scalars(select(ConceptField).where(
        ConceptField.concept_id == concept.id,
        ConceptField.status == "active",
    )).all())
    matches = [field for field in existing_fields if normalise_token(field.canonical_name) == canonical_key]
    if len(matches) > 1:
        raise ValueError(f"Canonical field '{canonical}' is ambiguous")
    field = matches[0] if matches else None
    if not field:
        field = ConceptField(
            concept_id=concept.id,
            canonical_name=canonical,
            data_type=proposal.data_type,
            description=proposal.description,
            unit=proposal.unit,
            allowed_values=proposal.allowed_values,
            introduced_version=concept.version,
            created_by=source,
        )
        db.add(field); db.flush()
        concept.version += 1
    return field


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


def normalise_data(db: Session, concept: Concept, data: dict[str, Any], proposals: list[FieldProposal], proposer_client_id: str) -> tuple[dict, list]:
    proposal_map = {normalise_token(p.submitted_name): p for p in proposals}
    for proposal in proposals:
        field = ensure_field(db, concept, proposal, proposer_client_id)
        for alias in {proposal.submitted_name, *proposal.aliases}:
            if normalise_token(alias) and normalise_token(alias) != normalise_token(field.canonical_name):
                propose_alias(
                    db,
                    concept=concept,
                    alias=alias,
                    canonical_name=field.canonical_name,
                    proposer_client_id=proposer_client_id,
                    rationale="Alias proposed while introducing or using a canonical field",
                )
    db.flush()

    vocab = vocabulary(db, concept)
    output: dict[str, Any] = {}
    log: list[dict] = []
    unknown: list[str] = []

    for submitted_name, value in data.items():
        key = normalise_token(submitted_name)
        canonical = None
        method = None
        if key in vocab["aliases"]:
            canonical = vocab["aliases"][key]
            method = "accepted_alias"
        elif key in vocab["fields"]:
            canonical = vocab["fields"][key].canonical_name
            method = "canonical"
        elif key in proposal_map:
            canonical = proposal_map[key].canonical_name
            method = "new_field_proposal"
        else:
            unknown.append(submitted_name)
            continue

        if canonical in output and output[canonical] != value:
            raise ValueError(f"Conflicting values resolve to canonical field '{canonical}'")
        output[canonical] = value
        log.append({"submitted": submitted_name, "canonical": canonical, "method": method})

    if unknown:
        available = sorted({field.canonical_name for field in vocab["fields"].values()})
        raise ValueError({
            "message": "Unknown fields must use an existing canonical field, an accepted alias, or a genuinely new field proposal. If the caller believes an unknown term means an existing field, it should propose that semantic alias separately and write using the existing canonical field until consensus accepts the alias.",
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
    normalised, log = normalise_data(db, concept, payload.structured_data, payload.proposed_fields, client_id)
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


def create_assessment(db: Session, payload: AssessmentCreate) -> Assessment:
    subject = db.get(V2Subject, payload.subject_id)
    if not subject or subject.deleted_at:
        raise ValueError("Subject not found")
    obj = Assessment(
        subject_id=payload.subject_id,
        user_id=payload.user_id,
        assessment_type=payload.assessment_type,
        evidence_json=payload.evidence,
        analysis_json=payload.analysis,
        conclusion=payload.conclusion,
        confidence=payload.confidence,
        source_model=payload.source_model,
        provenance={"kind": "ai_derived_assessment", **payload.provenance},
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj
