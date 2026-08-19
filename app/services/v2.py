from __future__ import annotations

import re
import unicodedata
import uuid

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy import delete as sql_delete, or_, select
from sqlalchemy.orm import Session

from app.models.v2 import (
    Assessment, FieldAlias, FieldDefinition, Source, SubjectRelationship, SubjectType,
    SubjectTypeAlias, SubjectTypeField, TypeRelationship, V2Experience, V2Subject, now_utc,
)
from app.schemas.v2 import (
    AssessmentCreate, ExperienceCreate, FieldEnsure, SourceCreate, SubjectContextEnsure,
    SubjectEnsure,
)


def normalise_term(value: str) -> str:
    """Return a conservative dictionary lookup key, never rewritten user prose."""
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = value.replace("’", "'")
    value = re.sub(r"['`]s\b", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()
    words = value.split()
    if not words:
        raise ValueError("Vocabulary term is empty")
    last = words[-1]
    if len(last) > 3 and last.endswith("ies"):
        last = last[:-3] + "y"
    elif len(last) > 4 and last.endswith("sses"):
        last = last[:-2]
    elif len(last) > 3 and last.endswith("s") and not last.endswith(("ss", "us", "is")):
        last = last[:-1]
    words[-1] = last
    return " ".join(words)


def canonical_label(value: str) -> str:
    return normalise_term(value)


def resolve_subject_type(db: Session, term: str) -> SubjectType | None:
    key = normalise_term(term)
    direct = db.scalar(select(SubjectType).where(SubjectType.normalized_name == key))
    if direct:
        return direct
    return db.scalar(
        select(SubjectType)
        .join(SubjectTypeAlias, SubjectTypeAlias.subject_type_id == SubjectType.id)
        .where(SubjectTypeAlias.normalized_alias == key)
    )


def ensure_subject_type(
    db: Session, term: str, *, created_by: str, description: str | None = None,
    create_if_missing: bool = True, commit: bool = True,
) -> tuple[SubjectType, bool, str]:
    obj = resolve_subject_type(db, term)
    if obj:
        return obj, False, "canonical" if obj.normalized_name == normalise_term(term) else "alias"
    if not create_if_missing:
        raise ValueError(f"Unknown subject type '{term}'")
    key = normalise_term(term)
    obj = SubjectType(
        canonical_name=canonical_label(term), normalized_name=key, description=description,
        status="provisional", created_by=created_by,
    )
    db.add(obj)
    if commit:
        db.commit(); db.refresh(obj)
    else:
        db.flush()
    return obj, True, "created_provisional"


def add_subject_type_alias(db: Session, subject_type: SubjectType, alias: str, *, source: str) -> SubjectTypeAlias:
    key = normalise_term(alias)
    existing_type = resolve_subject_type(db, alias)
    if existing_type and existing_type.id != subject_type.id:
        raise ValueError(f"'{alias}' already resolves to '{existing_type.canonical_name}'")
    existing = db.scalar(select(SubjectTypeAlias).where(SubjectTypeAlias.normalized_alias == key))
    if existing:
        return existing
    if key == subject_type.normalized_name:
        raise ValueError("Alias is identical to the canonical subject type")
    obj = SubjectTypeAlias(subject_type_id=subject_type.id, alias=alias.strip(), normalized_alias=key, source=source)
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def add_type_relationship(db: Session, source_type: SubjectType, relationship: str, target_type: SubjectType, *, source: str) -> TypeRelationship:
    rel = normalise_term(relationship).replace(" ", "_")
    if source_type.id == target_type.id:
        raise ValueError("A subject type cannot relate to itself")
    existing = db.scalar(select(TypeRelationship).where(
        TypeRelationship.source_type_id == source_type.id,
        TypeRelationship.relationship == rel,
        TypeRelationship.target_type_id == target_type.id,
    ))
    if existing:
        return existing
    obj = TypeRelationship(source_type_id=source_type.id, relationship=rel, target_type_id=target_type.id, source=source)
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def resolve_field(db: Session, term: str) -> FieldDefinition | None:
    key = normalise_term(term)
    direct = db.scalar(select(FieldDefinition).where(FieldDefinition.normalized_name == key, FieldDefinition.status == "active"))
    if direct:
        return direct
    return db.scalar(
        select(FieldDefinition).join(FieldAlias, FieldAlias.field_id == FieldDefinition.id)
        .where(FieldAlias.normalized_alias == key, FieldDefinition.status == "active")
    )


def ensure_field(db: Session, payload: FieldEnsure, *, source: str) -> FieldDefinition:
    key = normalise_term(payload.canonical_name)
    existing = resolve_field(db, payload.canonical_name)
    if existing:
        if existing.normalized_name != key:
            raise ValueError(f"'{payload.canonical_name}' is already an alias of '{existing.canonical_name}'")
        field = existing
    else:
        try:
            Draft202012Validator.check_schema(payload.json_schema)
        except SchemaError as exc:
            raise ValueError(f"Invalid JSON Schema: {exc.message}") from exc
        field = FieldDefinition(
            canonical_name=canonical_label(payload.canonical_name).replace(" ", "_"),
            normalized_name=key, json_schema=payload.json_schema,
            description=payload.description, created_by=source,
        )
        db.add(field); db.flush()
    for alias in payload.aliases:
        alias_key = normalise_term(alias)
        collision = resolve_field(db, alias)
        if collision and collision.id != field.id:
            raise ValueError(f"Field alias '{alias}' already resolves to '{collision.canonical_name}'")
        if alias_key != field.normalized_name and not db.scalar(select(FieldAlias).where(FieldAlias.normalized_alias == alias_key)):
            db.add(FieldAlias(field_id=field.id, alias=alias.strip(), normalized_alias=alias_key, source=source))
    for type_term in payload.subject_types:
        subject_type = resolve_subject_type(db, type_term)
        if not subject_type:
            raise ValueError(f"Unknown subject type '{type_term}'")
        if not db.scalar(select(SubjectTypeField).where(SubjectTypeField.subject_type_id == subject_type.id, SubjectTypeField.field_id == field.id)):
            db.add(SubjectTypeField(subject_type_id=subject_type.id, field_id=field.id, source=source))
    db.commit(); db.refresh(field)
    return field


def fields_for_type(db: Session, subject_type: SubjectType) -> list[FieldDefinition]:
    return list(db.scalars(
        select(FieldDefinition).join(SubjectTypeField, SubjectTypeField.field_id == FieldDefinition.id)
        .where(SubjectTypeField.subject_type_id == subject_type.id, FieldDefinition.status == "active")
        .order_by(FieldDefinition.canonical_name)
    ).all())


def normalise_data(
    db: Session, subject_type: SubjectType, data: dict, *, source: str = "ai-client",
) -> tuple[dict, list]:
    allowed = {field.id: field for field in fields_for_type(db, subject_type)}
    result, log = {}, []
    for submitted_name, value in data.items():
        field = resolve_field(db, submitted_name)
        if not field:
            raise ValueError(
                f"Field '{submitted_name}' is not registered globally. Preserve a one-off detail in "
                "raw_text or register a genuinely reusable field before saving."
            )
        try:
            Draft202012Validator(field.json_schema).validate(value)
        except ValidationError as exc:
            raise ValueError(f"Field '{field.canonical_name}' is invalid: {exc.message}") from exc
        attached = field.id in allowed
        if not attached:
            db.add(SubjectTypeField(
                subject_type_id=subject_type.id, field_id=field.id, source=source,
            ))
            db.flush()
            allowed[field.id] = field
        if field.canonical_name in result:
            raise ValueError(f"Multiple submitted fields resolve to '{field.canonical_name}'")
        result[field.canonical_name] = value
        method = "canonical" if normalise_term(submitted_name) == field.normalized_name else "alias"
        log.append({
            "submitted": submitted_name, "canonical": field.canonical_name,
            "field_id": str(field.id), "method": method,
            "attached_to_subject_type": not attached,
        })
    return result, log


def _fill_missing(existing: dict | None, incoming: dict) -> tuple[dict, bool]:
    merged = dict(existing or {})
    changed = False
    for key, value in incoming.items():
        if key not in merged or merged[key] in (None, ""):
            merged[key] = value
            changed = True
    return merged, changed


def ensure_subject(
    db: Session, payload: SubjectEnsure, client_id: str = "ai-client",
    *, owner_id: uuid.UUID | None = None,
) -> V2Subject:
    subject_type = resolve_subject_type(db, payload.subject_type)
    if not subject_type:
        raise ValueError(f"Unknown subject type '{payload.subject_type}'")
    obj = db.scalar(select(V2Subject).where(
        V2Subject.subject_type_id == subject_type.id,
        V2Subject.canonical_key == payload.canonical_key,
        V2Subject.deleted_at.is_(None),
    ))
    if obj:
        identifiers, identifiers_changed = _fill_missing(obj.identifiers_json, payload.identifiers)
        attributes, attributes_changed = _fill_missing(obj.attributes_json, payload.attributes)
        provenance, provenance_changed = _fill_missing(obj.provenance_json, payload.provenance)
        if identifiers_changed or attributes_changed or provenance_changed:
            obj.identifiers_json = identifiers
            obj.attributes_json = attributes
            obj.provenance_json = provenance
            db.commit(); db.refresh(obj)
        return obj
    obj = V2Subject(
        subject_type_id=subject_type.id, owner_id=owner_id,
        name=payload.name, canonical_key=payload.canonical_key,
        identifiers_json=payload.identifiers, attributes_json=payload.attributes,
        provenance_json=payload.provenance,
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def add_subject_relationship(
    db: Session, source_subject: V2Subject, relationship: str, target_subject: V2Subject,
    *, provenance: dict | None = None, created_by: str = "ai-client",
) -> SubjectRelationship:
    rel = normalise_term(relationship).replace(" ", "_")
    if source_subject.id == target_subject.id:
        raise ValueError("A subject cannot relate to itself")
    existing = db.scalar(select(SubjectRelationship).where(
        SubjectRelationship.source_subject_id == source_subject.id,
        SubjectRelationship.relationship == rel,
        SubjectRelationship.target_subject_id == target_subject.id,
    ))
    if existing:
        merged, changed = _fill_missing(existing.provenance_json, provenance or {})
        if changed:
            existing.provenance_json = merged
            db.commit(); db.refresh(existing)
        return existing
    obj = SubjectRelationship(
        source_subject_id=source_subject.id, relationship=rel,
        target_subject_id=target_subject.id, provenance_json=provenance or {},
        status="active", created_by=created_by,
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def ensure_subject_context(
    db: Session, reviewed_subject: V2Subject, payload: SubjectContextEnsure,
    *, client_id: str, owner_id: uuid.UUID | None = None,
) -> dict:
    refs = {"reviewed_subject": reviewed_subject, "subject": reviewed_subject}
    created_subjects = []
    for item in payload.subjects:
        if item.ref in refs:
            raise ValueError(f"Duplicate or reserved subject reference '{item.ref}'")
        subject = ensure_subject(
            db,
            SubjectEnsure(
                subject_type=item.subject_type, name=item.name,
                canonical_key=item.canonical_key, identifiers=item.identifiers,
                attributes=item.attributes, provenance=item.provenance,
            ),
            client_id, owner_id=owner_id,
        )
        refs[item.ref] = subject
        created_subjects.append(subject)
    relationships = []
    for item in payload.relationships:
        if item.source_ref not in refs or item.target_ref not in refs:
            raise ValueError("Subject relationship refers to an unknown context reference")
        relationships.append(add_subject_relationship(
            db, refs[item.source_ref], item.relationship, refs[item.target_ref],
            provenance=item.provenance, created_by=client_id,
        ))
    return {
        "subjects": [
            {"id": str(subject.id), "name": subject.name, "canonical_key": subject.canonical_key}
            for subject in created_subjects
        ],
        "relationships": [
            {
                "id": str(rel.id), "source_subject_id": str(rel.source_subject_id),
                "relationship": rel.relationship, "target_subject_id": str(rel.target_subject_id),
            }
            for rel in relationships
        ],
    }


def _source(db: Session, payload: SourceCreate | None) -> Source | None:
    if payload is None:
        return None
    if payload.external_id:
        existing = db.scalar(select(Source).where(Source.provider == payload.provider, Source.external_id == payload.external_id))
        if existing:
            return existing
    obj = Source(**payload.model_dump())
    db.add(obj); db.flush()
    return obj


def create_experience(db: Session, payload: ExperienceCreate, client_id: str) -> V2Experience:
    if not payload.user_approved:
        raise ValueError("Explicit user approval is required")
    subject = db.get(V2Subject, payload.subject_id)
    if not subject or subject.deleted_at:
        raise ValueError("Subject not found")
    subject_type = db.get(SubjectType, subject.subject_type_id)
    canonical_data, log = normalise_data(
        db, subject_type, payload.structured_data, source=client_id,
    )
    source = _source(db, payload.source)
    enrichment_check = (
        payload.subject_enrichment_check.model_dump(mode="json")
        if payload.subject_enrichment_check else None
    )
    if enrichment_check is not None:
        enrichment_check["recorded_at"] = now_utc().isoformat()
    collection_assessment = (
        payload.collection_assessment.model_dump(mode="json")
        if payload.collection_assessment else None
    )
    if collection_assessment is not None:
        collection_assessment["recorded_at"] = now_utc().isoformat()
    obj = V2Experience(
        owner_id=payload.owner_id, subject_id=subject.id, source_id=source.id if source else None,
        record_type="review", experienced_at=payload.experienced_at or now_utc(), headline=payload.headline,
        summary=payload.summary, raw_text=payload.raw_text, structured_data=canonical_data,
        submitted_data=payload.structured_data, normalization_log=log, visibility=payload.visibility,
        publication_status="published",
        provenance={
            "kind": "direct_user_experience", "source_client": client_id,
            **({"subject_enrichment_check": enrichment_check} if enrichment_check else {}),
            **({"collection_assessment": collection_assessment} if collection_assessment else {}),
        },
        created_by_client=client_id,
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def delete_owned_experience(
    db: Session, experience_id: uuid.UUID, owner_id: uuid.UUID,
    *, delete_orphan_subject: bool = True,
) -> dict:
    experience = db.scalar(select(V2Experience).where(
        V2Experience.id == experience_id,
        V2Experience.owner_id == owner_id,
        V2Experience.deleted_at.is_(None),
    ))
    if not experience:
        raise ValueError("Experience not found")
    subject = db.get(V2Subject, experience.subject_id)
    assessment_ids = list(db.scalars(select(Assessment.id).where(
        Assessment.experience_id == experience.id
    )).all())
    db.execute(sql_delete(Assessment).where(Assessment.experience_id == experience.id))
    db.delete(experience)
    db.flush()

    subject_deleted = False
    if delete_orphan_subject and subject and subject.owner_id == owner_id:
        remaining_experience = db.scalar(select(V2Experience.id).where(
            V2Experience.subject_id == subject.id,
            V2Experience.deleted_at.is_(None),
        ).limit(1))
        connected_relationship = db.scalar(select(SubjectRelationship.id).where(or_(
            SubjectRelationship.source_subject_id == subject.id,
            SubjectRelationship.target_subject_id == subject.id,
        )).limit(1))
        if not remaining_experience and not connected_relationship:
            db.delete(subject)
            subject_deleted = True
    db.commit()
    return {
        "deleted": True,
        "experience_id": str(experience_id),
        "assessment_ids": [str(item) for item in assessment_ids],
        "subject_id": str(subject.id) if subject else None,
        "subject_deleted": subject_deleted,
    }


def create_assessment(db: Session, payload: AssessmentCreate, *, client_id: str, user_id: uuid.UUID | None) -> Assessment:
    experience = db.scalar(select(V2Experience).where(
        V2Experience.id == payload.experience_id, V2Experience.deleted_at.is_(None),
        *([V2Experience.owner_id == user_id] if user_id else []),
    ))
    if not experience:
        raise ValueError("Experience not found")
    provenance = {"source_client": client_id, "kind": "ai_derived_assessment", "target_experience_id": str(experience.id)}
    obj = Assessment(
        subject_id=experience.subject_id, experience_id=experience.id, user_id=user_id,
        assessment_type=payload.assessment_type, evidence_json=payload.evidence,
        analysis_json=payload.analysis, conclusion=payload.conclusion, confidence=payload.confidence,
        source_model=payload.source_model, provenance=provenance, created_by_client=client_id,
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def vocabulary_index(db: Session) -> dict:
    types = list(db.scalars(select(SubjectType).order_by(SubjectType.canonical_name)).all())
    aliases = list(db.scalars(select(SubjectTypeAlias).order_by(SubjectTypeAlias.alias)).all())
    relationships = list(db.scalars(select(TypeRelationship).where(TypeRelationship.status == "active").order_by(TypeRelationship.relationship)).all())
    fields = list(db.scalars(select(FieldDefinition).order_by(FieldDefinition.canonical_name)).all())
    by_id = {x.id: x for x in types}
    return {
        "subject_types": [{"id": str(x.id), "canonical_name": x.canonical_name, "status": x.status,
                           "aliases": [a.alias for a in aliases if a.subject_type_id == x.id]} for x in types],
        "relationships": [{"source": by_id[x.source_type_id].canonical_name, "relationship": x.relationship,
                           "target": by_id[x.target_type_id].canonical_name} for x in relationships
                          if x.source_type_id in by_id and x.target_type_id in by_id],
        "fields": [{"id": str(x.id), "canonical_name": x.canonical_name, "json_schema": x.json_schema} for x in fields],
    }


def descendant_type_ids(db: Session, root: SubjectType) -> set[uuid.UUID]:
    found, frontier = {root.id}, {root.id}
    while frontier:
        rows = list(db.scalars(select(TypeRelationship).where(
            TypeRelationship.relationship == "belongs_to", TypeRelationship.status == "active",
            TypeRelationship.target_type_id.in_(frontier)
        )).all())
        new = {row.source_type_id for row in rows} - found
        found |= new; frontier = new
    return found
