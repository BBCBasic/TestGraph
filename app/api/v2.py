from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app.core.security import Principal, require_scope
from app.db.session import get_db
from app.models.v2 import Assessment, SubjectType, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ExperienceCreate, FieldEnsure, RelationshipEnsure, SubjectEnsure, SubjectRead
from app.services.v2 import (
    add_subject_type_alias, add_type_relationship, create_assessment, create_experience,
    ensure_field, ensure_subject, ensure_subject_type, fields_for_type, resolve_subject_type,
    vocabulary_index,
)

router = APIRouter(prefix="/api/v2", tags=["TasteGraph v2"])
PageLimit = Annotated[int, Query(ge=1, le=100)]


@router.get("/vocabulary")
def vocabulary(db: Session = Depends(get_db)):
    return vocabulary_index(db)


@router.get("/public/experiences")
def public_experiences(limit: PageLimit = 20, db: Session = Depends(get_db)):
    """Return a deliberately small, sanitised view of explicitly public V2 data."""
    public_filter = (
        V2Experience.deleted_at.is_(None),
        V2Experience.publication_status == "published",
        V2Experience.visibility == "public",
    )
    rows = db.execute(
        select(V2Experience, V2Subject, SubjectType)
        .join(V2Subject, V2Experience.subject_id == V2Subject.id)
        .join(SubjectType, V2Subject.subject_type_id == SubjectType.id)
        .where(*public_filter, V2Subject.deleted_at.is_(None))
        .order_by(V2Experience.created_at.desc())
        .limit(limit)
    ).all()
    experience_ids = [experience.id for experience, _, _ in rows]
    assessments = (
        list(db.scalars(
            select(Assessment)
            .where(Assessment.experience_id.in_(experience_ids))
            .order_by(Assessment.created_at)
        ).all())
        if experience_ids else []
    )
    assessments_by_experience: dict[uuid.UUID, list[dict]] = {}
    for assessment in assessments:
        assessments_by_experience.setdefault(assessment.experience_id, []).append({
            "assessment_type": assessment.assessment_type,
            "conclusion": assessment.conclusion,
            "confidence": assessment.confidence,
            "source_model": assessment.source_model,
        })
    public_experience_ids = select(V2Experience.id).where(*public_filter)
    return {
        "counts": {
            "experiences": db.scalar(select(func.count()).select_from(V2Experience).where(*public_filter)) or 0,
            "subjects": db.scalar(select(func.count(distinct(V2Experience.subject_id))).where(*public_filter)) or 0,
            "assessments": db.scalar(select(func.count()).select_from(Assessment).where(
                Assessment.experience_id.in_(public_experience_ids)
            )) or 0,
        },
        "experiences": [
            {
                "subject": {"name": subject.name, "subject_type": subject_type.canonical_name},
                "headline": experience.headline,
                "summary": experience.summary,
                "raw_text": experience.raw_text,
                "structured_data": experience.structured_data,
                "experienced_at": experience.experienced_at.isoformat() if experience.experienced_at else None,
                "created_at": experience.created_at.isoformat(),
                "assessments": assessments_by_experience.get(experience.id, []),
            }
            for experience, subject, subject_type in rows
        ],
        "privacy": "Only V2 experiences marked public and published are included.",
    }


@router.get("/subject-types/resolve")
def resolve_type(term: str, db: Session = Depends(get_db)):
    obj = resolve_subject_type(db, term)
    if not obj:
        raise HTTPException(404, "Subject type not found")
    return {"id": str(obj.id), "canonical_name": obj.canonical_name, "status": obj.status,
            "fields": [x.canonical_name for x in fields_for_type(db, obj)]}


@router.post("/subject-types", status_code=201)
def create_type(payload: dict, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("subject:write"))):
    try:
        obj, created, resolution = ensure_subject_type(db, str(payload["term"]), created_by=principal.client_id, description=payload.get("description"))
        return {"id": str(obj.id), "canonical_name": obj.canonical_name, "status": obj.status, "created": created, "resolution": resolution}
    except (KeyError, ValueError) as exc:
        db.rollback(); raise HTTPException(422, str(exc))


@router.post("/subject-types/{subject_type_id}/aliases", status_code=201)
def create_alias(subject_type_id: uuid.UUID, payload: dict, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("subject:write"))):
    obj = db.get(SubjectType, subject_type_id)
    if not obj: raise HTTPException(404, "Subject type not found")
    try:
        alias = add_subject_type_alias(db, obj, str(payload["alias"]), source=principal.client_id)
        return {"id": str(alias.id), "alias": alias.alias, "subject_type_id": str(obj.id)}
    except (KeyError, ValueError) as exc:
        db.rollback(); raise HTTPException(422, str(exc))


@router.post("/relationships", status_code=201)
def create_relationship(payload: RelationshipEnsure, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("subject:write"))):
    source = resolve_subject_type(db, payload.source_type); target = resolve_subject_type(db, payload.target_type)
    if not source or not target: raise HTTPException(404, "Both subject types must exist")
    try:
        obj = add_type_relationship(db, source, payload.relationship, target, source=principal.client_id)
        return {"id": str(obj.id), "source": source.canonical_name, "relationship": obj.relationship, "target": target.canonical_name}
    except ValueError as exc:
        db.rollback(); raise HTTPException(422, str(exc))


@router.post("/fields", status_code=201)
def create_field(payload: FieldEnsure, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("subject:write"))):
    try:
        obj = ensure_field(db, payload, source=principal.client_id)
        return {"id": str(obj.id), "canonical_name": obj.canonical_name, "json_schema": obj.json_schema}
    except ValueError as exc:
        db.rollback(); raise HTTPException(422, str(exc))


@router.post("/subjects/ensure", response_model=SubjectRead, status_code=201)
def put_subject(payload: SubjectEnsure, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("subject:write"))):
    try: return ensure_subject(db, payload, principal.client_id)
    except ValueError as exc: db.rollback(); raise HTTPException(422, str(exc))


@router.get("/subjects")
def find_subjects(q: str = "", subject_type: str | None = None, limit: PageLimit = 50, db: Session = Depends(get_db)):
    stmt = select(V2Subject, SubjectType).join(SubjectType, V2Subject.subject_type_id == SubjectType.id).where(V2Subject.deleted_at.is_(None))
    if subject_type:
        resolved = resolve_subject_type(db, subject_type)
        if not resolved: return []
        stmt = stmt.where(SubjectType.id == resolved.id)
    if q.strip():
        p=f"%{q.strip()}%"; stmt=stmt.where(or_(V2Subject.name.ilike(p),V2Subject.canonical_key.ilike(p)))
    return [{"id":str(s.id),"name":s.name,"canonical_key":s.canonical_key,"subject_type_id":str(t.id),"subject_type":t.canonical_name} for s,t in db.execute(stmt.limit(limit)).all()]


@router.post("/experiences", status_code=201)
def save_experience(payload: ExperienceCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("experience:publish"))):
    if principal.user_id and payload.owner_id != principal.user_id: raise HTTPException(403, "Cannot save for another user")
    try: return create_experience(db, payload, principal.client_id)
    except ValueError as exc: db.rollback(); raise HTTPException(422, str(exc))


@router.get("/experiences/{experience_id}")
def get_experience(experience_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("experience:read"))):
    obj=db.get(V2Experience,experience_id)
    if not obj or obj.deleted_at or (principal.user_id and obj.owner_id!=principal.user_id): raise HTTPException(404,"Experience not found")
    return obj


@router.post("/assessments", status_code=201)
def save_assessment(payload: AssessmentCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_scope("experience:publish"))):
    try: obj=create_assessment(db,payload,client_id=principal.client_id,user_id=principal.user_id)
    except ValueError as exc: db.rollback(); raise HTTPException(422,str(exc))
    return {"id":str(obj.id),"experience_id":str(obj.experience_id),"provenance":obj.provenance}
