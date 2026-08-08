from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.security import Principal, require_scope
from app.db.session import get_db
from app.models.v2 import Assessment, Concept, ConceptField, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ConceptEnsure, ExperienceCreate, ExperienceRead, NormaliseRequest, SubjectEnsure, SubjectRead
from app.services.semantic import list_alias_candidates, propose_alias
from app.services.v2 import create_assessment, create_experience, ensure_concept, ensure_subject, normalise_data, normalise_path, vocabulary

router = APIRouter(prefix="/api/v2", tags=["TasteGraph v2"])
PageLimit = Annotated[int, Query(ge=1, le=100)]


@router.get("/concepts")
def list_concepts(q: str = "", limit: PageLimit = 50, db: Session = Depends(get_db)):
    stmt = select(Concept).where(Concept.status == "active")
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Concept.path.ilike(pattern), Concept.name.ilike(pattern), Concept.description.ilike(pattern)))
    rows = list(db.scalars(stmt.order_by(Concept.path).limit(limit)).all())
    return [{"id": str(x.id), "path": x.path, "name": x.name, "description": x.description, "version": x.version} for x in rows]


@router.get("/concepts/resolve")
def resolve_concept(path: str, db: Session = Depends(get_db)):
    canonical_path = normalise_path(path)
    obj = db.scalar(select(Concept).where(Concept.path == canonical_path, Concept.status == "active"))
    if not obj:
        raise HTTPException(404, "Concept not found")
    vocab = vocabulary(db, obj)
    fields = []
    seen = set()
    for field in vocab["fields"].values():
        if field.id in seen:
            continue
        seen.add(field.id)
        fields.append({
            "id": str(field.id), "canonical_name": field.canonical_name, "data_type": field.data_type,
            "description": field.description, "unit": field.unit, "allowed_values": field.allowed_values,
            "origin": vocab["origins"].get(field.canonical_name),
        })
    return {
        "id": str(obj.id), "path": obj.path, "name": obj.name, "version": obj.version,
        "description": obj.description, "fields": sorted(fields, key=lambda x: x["canonical_name"]),
        "accepted_aliases": vocab["aliases"],
        "alias_candidates": list_alias_candidates(db, obj),
        "semantic_policy": "Calling AI clients judge language meaning. TasteGraph records proposals, detects conflict, and promotes only independent non-conflicting consensus.",
    }


@router.post("/concepts/ensure", status_code=201)
def put_concept(payload: ConceptEnsure, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("subject:write"))):
    try:
        safe_payload = payload.model_copy(update={"created_by": principal.client_id})
        obj = ensure_concept(db, safe_payload)
    except ValueError as exc:
        db.rollback(); raise HTTPException(409, str(exc))
    return {"id": str(obj.id), "path": obj.path, "version": obj.version, "created_by": obj.created_by}


@router.post("/alias-proposals", status_code=201)
def put_alias_proposal(payload: dict, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("subject:write"))):
    try:
        path = normalise_path(str(payload["concept_path"]))
        concept = db.scalar(select(Concept).where(Concept.path == path, Concept.status == "active"))
        if not concept:
            raise HTTPException(404, "Concept not found")
        status = propose_alias(
            db,
            concept=concept,
            alias=str(payload["alias"]),
            canonical_name=str(payload["canonical_name"]),
            proposer_client_id=principal.client_id,
            confidence=payload.get("confidence"),
            rationale=payload.get("rationale"),
        )
        db.commit()
        return status
    except KeyError as exc:
        db.rollback(); raise HTTPException(422, f"Missing field: {exc.args[0]}")
    except ValueError as exc:
        db.rollback(); raise HTTPException(422, str(exc))


@router.post("/normalise")
def normalise(payload: NormaliseRequest, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("subject:write"))):
    from app.services.v2 import _concept
    concept = _concept(db, payload.concept_path)
    if not concept:
        raise HTTPException(404, "Concept not found")
    try:
        data, log = normalise_data(db, concept, payload.data)
        db.commit()
    except ValueError as exc:
        db.rollback(); raise HTTPException(422, exc.args[0] if exc.args else str(exc))
    return {"concept_path": concept.path, "canonical_data": data, "normalization_log": log, "alias_candidates": list_alias_candidates(db, concept)}


@router.post("/subjects/ensure", response_model=SubjectRead, status_code=201)
def put_subject(payload: SubjectEnsure, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("subject:write"))):
    try:
        return ensure_subject(db, payload, principal.client_id)
    except ValueError as exc:
        db.rollback(); raise HTTPException(422, str(exc))


@router.get("/subjects")
def find_subjects(q: str = "", concept_path: str | None = None, limit: PageLimit = 50, db: Session = Depends(get_db)):
    stmt = select(V2Subject, Concept).join(Concept, V2Subject.concept_id == Concept.id).where(V2Subject.deleted_at.is_(None))
    if concept_path:
        stmt = stmt.where(Concept.path == concept_path)
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(V2Subject.name.ilike(pattern), V2Subject.canonical_key.ilike(pattern)))
    rows = db.execute(stmt.order_by(V2Subject.name).limit(limit)).all()
    return [{"id": str(subject.id), "name": subject.name, "canonical_key": subject.canonical_key, "concept_path": concept.path, "identifiers": subject.identifiers_json, "attributes": subject.attributes_json} for subject, concept in rows]


@router.post("/experiences", response_model=ExperienceRead, status_code=201)
def save_experience(payload: ExperienceCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("experience:publish"))):
    if principal.user_id and payload.owner_id != principal.user_id:
        raise HTTPException(403, "Cannot save an experience for another user")
    try:
        return create_experience(db, payload, principal.client_id)
    except ValueError as exc:
        db.rollback(); raise HTTPException(422, exc.args[0] if exc.args else str(exc))


@router.get("/experiences")
def find_experiences(q: str = "", owner_id: uuid.UUID | None = None, subject_id: uuid.UUID | None = None, limit: PageLimit = 50, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("experience:read"))):
    owner = owner_id or principal.user_id
    stmt = select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.deleted_at.is_(None))
    if owner:
        stmt = stmt.where(V2Experience.owner_id == owner)
    if subject_id:
        stmt = stmt.where(V2Experience.subject_id == subject_id)
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(V2Subject.name.ilike(pattern), V2Experience.headline.ilike(pattern), V2Experience.summary.ilike(pattern)))
    rows = db.execute(stmt.order_by(V2Experience.created_at.desc()).limit(limit)).all()
    return [{"id": str(exp.id), "subject_id": str(subject.id), "subject_name": subject.name, "concept_path": concept.path, "headline": exp.headline, "summary": exp.summary, "structured_data": exp.structured_data, "normalization_log": exp.normalization_log, "created_at": exp.created_at.isoformat()} for exp, subject, concept in rows]


@router.get("/experiences/{experience_id}", response_model=ExperienceRead)
def get_experience(experience_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("experience:read"))):
    obj = db.get(V2Experience, experience_id)
    if not obj or obj.deleted_at:
        raise HTTPException(404, "Experience not found")
    if principal.user_id and obj.owner_id != principal.user_id:
        raise HTTPException(404, "Experience not found")
    return obj


@router.post("/assessments", status_code=201)
def save_assessment(payload: AssessmentCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("experience:publish"))):
    try:
        obj = create_assessment(
            db,
            payload,
            client_id=principal.client_id,
            user_id=principal.user_id,
        )
    except ValueError as exc:
        db.rollback(); raise HTTPException(422, str(exc))
    return {"id": str(obj.id), "experience_id": str(obj.experience_id), "subject_id": str(obj.subject_id), "user_id": str(obj.user_id) if obj.user_id else None, "assessment_type": obj.assessment_type, "evidence": obj.evidence_json, "analysis": obj.analysis_json, "conclusion": obj.conclusion, "confidence": obj.confidence, "source_model": obj.source_model, "provenance": obj.provenance, "created_by_client": obj.created_by_client}


@router.get("/assessments")
def find_assessments(experience_id: uuid.UUID | None = None, subject_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None, limit: PageLimit = 50, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("experience:read"))):
    user = user_id or principal.user_id
    stmt = select(Assessment).order_by(Assessment.created_at.desc())
    if subject_id:
        stmt = stmt.where(Assessment.subject_id == subject_id)
    if experience_id:
        stmt = stmt.where(Assessment.experience_id == experience_id)
    if user:
        stmt = stmt.where(or_(Assessment.user_id == user, Assessment.user_id.is_(None)))
    rows = list(db.scalars(stmt.limit(limit)).all())
    return [{"id": str(x.id), "experience_id": str(x.experience_id) if x.experience_id else None, "subject_id": str(x.subject_id), "user_id": str(x.user_id) if x.user_id else None, "assessment_type": x.assessment_type, "evidence": x.evidence_json, "analysis": x.analysis_json, "conclusion": x.conclusion, "confidence": x.confidence, "source_model": x.source_model, "provenance": x.provenance, "created_by_client": x.created_by_client, "created_at": x.created_at.isoformat()} for x in rows]
