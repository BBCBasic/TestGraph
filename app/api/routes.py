from __future__ import annotations
import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.core.security import Principal, require_scope
from app.db.session import get_db
from app.models.entities import Experience, IdempotencyRecord, PairwiseAlignment, SchemaDefinition, Subject, User
from app.schemas.common import ExperienceCreate, ExperienceRead, PairwiseAlignmentUpsert, PersonalisedReview, PublishRequest, SubjectCreate, SubjectRead, UserCreate, UserRead
from app.services.core import create_experience, personalised, publish_experience, request_hash

router=APIRouter()

PageLimit = Annotated[int, Query(ge=0, le=100, description="Maximum number of records to return.")]
PageOffset = Annotated[int, Query(ge=0, description="Number of records to skip.")]
AUTH_RESPONSES = {
    401: {"description": "Invalid or missing credentials."},
    403: {"description": "Authenticated client lacks the required scope."},
}
NOT_FOUND_RESPONSE = {404: {"description": "Requested resource was not found."}}

@router.get("/health/live")
def live(): return {"status":"live"}

@router.get("/health/ready")
def ready(db:Session=Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status":"ready","database":"ok"}

@router.post("/api/v1/users", response_model=UserRead, responses=AUTH_RESPONSES)
def create_user(payload:UserCreate, db:Session=Depends(get_db), principal:Principal=Depends(require_scope("profile:write"))):
    obj=User(display_name=payload.display_name,bio=payload.bio,profile_data=payload.profile_data)
    db.add(obj); db.commit(); db.refresh(obj); return obj

@router.get("/api/v1/users/{user_id}", response_model=UserRead, responses=NOT_FOUND_RESPONSE)
def get_user(user_id:uuid.UUID, db:Session=Depends(get_db)):
    obj=db.get(User,user_id)
    if not obj or obj.deleted_at: raise HTTPException(404,"User not found")
    return obj

@router.post("/api/v1/subjects", response_model=SubjectRead, responses=AUTH_RESPONSES)
def create_subject(payload:SubjectCreate, db:Session=Depends(get_db), principal:Principal=Depends(require_scope("subject:write"))):
    obj=Subject(**payload.model_dump())
    db.add(obj); db.commit(); db.refresh(obj); return obj

@router.get("/api/v1/subjects", response_model=list[SubjectRead])
def list_subjects(subject_type:str|None=None, limit:PageLimit=50, offset:PageOffset=0, db:Session=Depends(get_db)):
    q=select(Subject).where(Subject.deleted_at.is_(None))
    if subject_type:q=q.where(Subject.subject_type==subject_type)
    return list(db.scalars(q.offset(offset).limit(limit)).all())

@router.post("/api/v1/experiences/drafts", response_model=ExperienceRead, status_code=201, responses={**AUTH_RESPONSES, 409: {"description": "Idempotency key conflict."}})
def create_draft(payload:ExperienceCreate, request:Request, response:Response, db:Session=Depends(get_db), idempotency_key:str|None=Header(default=None, alias="Idempotency-Key"), principal:Principal=Depends(require_scope("experience:draft"))):
    req_id=request.state.request_id
    p_hash=request_hash(payload.model_dump(mode="json"))
    if idempotency_key:
        existing=db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.client_id==principal.client_id,IdempotencyRecord.key==idempotency_key))
        if existing:
            if existing.request_hash!=p_hash: raise HTTPException(409,"Idempotency key reused with a different payload")
            return ExperienceRead.model_validate(existing.response_body)
    obj=create_experience(db,payload,client_id=principal.client_id,auth_subject=principal.subject,request_id=req_id)
    body=ExperienceRead.model_validate(obj).model_dump(mode="json")
    if idempotency_key:
        db.add(IdempotencyRecord(client_id=principal.client_id,key=idempotency_key,request_hash=p_hash,response_status=201,response_body=body)); db.commit()
    response.headers["X-Request-ID"]=req_id
    return obj

@router.get("/api/v1/experiences/{experience_id}", response_model=ExperienceRead, responses=NOT_FOUND_RESPONSE)
def get_experience(experience_id:uuid.UUID, db:Session=Depends(get_db)):
    obj=db.get(Experience,experience_id)
    if not obj or obj.deleted_at: raise HTTPException(404,"Experience not found")
    return obj

@router.get("/api/v1/experiences", response_model=list[ExperienceRead])
def list_experiences(subject_id:uuid.UUID|None=None, owner_id:uuid.UUID|None=None, subject_type:str|None=None, publication_status:str|None=None, limit:PageLimit=50, offset:PageOffset=0, db:Session=Depends(get_db)):
    q=select(Experience).where(Experience.deleted_at.is_(None))
    if subject_id:q=q.where(Experience.subject_id==subject_id)
    if owner_id:q=q.where(Experience.owner_id==owner_id)
    if subject_type:q=q.where(Experience.subject_type==subject_type)
    if publication_status:q=q.where(Experience.publication_status==publication_status)
    q=q.order_by(Experience.created_at.desc())
    return list(db.scalars(q.offset(offset).limit(limit)).all())

@router.post("/api/v1/experiences/{experience_id}/publish", response_model=ExperienceRead, responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, 409: {"description": "Draft version conflict."}})
def publish(experience_id:uuid.UUID,payload:PublishRequest,request:Request,db:Session=Depends(get_db),principal:Principal=Depends(require_scope("experience:publish"))):
    obj=db.get(Experience,experience_id)
    if not obj or obj.deleted_at: raise HTTPException(404,"Experience not found")
    if not payload.user_approved: raise HTTPException(400,"Explicit user approval is required")
    try:return publish_experience(db,obj,payload.approved_version,actor_id=principal.subject,client_id=principal.client_id,request_id=request.state.request_id)
    except ValueError as e: raise HTTPException(409,str(e))

@router.delete("/api/v1/experiences/{experience_id}", status_code=204, responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE})
def soft_delete(experience_id:uuid.UUID,db:Session=Depends(get_db),principal:Principal=Depends(require_scope("experience:delete"))):
    obj=db.get(Experience,experience_id)
    if not obj: raise HTTPException(404,"Experience not found")
    from datetime import datetime,timezone
    obj.deleted_at=datetime.now(timezone.utc);obj.deleted_by=principal.subject;db.commit();return Response(status_code=204)

@router.put("/api/v1/alignments", responses=AUTH_RESPONSES)
def put_alignment(payload:PairwiseAlignmentUpsert,db:Session=Depends(get_db),principal:Principal=Depends(require_scope("profile:write"))):
    obj=db.scalar(select(PairwiseAlignment).where(PairwiseAlignment.source_user_id==payload.source_user_id,PairwiseAlignment.target_user_id==payload.target_user_id))
    if obj: obj.dimensions=payload.dimensions
    else: obj=PairwiseAlignment(**payload.model_dump());db.add(obj)
    db.commit();db.refresh(obj);return {"id":obj.id,"dimensions":obj.dimensions}

@router.get("/api/v1/experiences/{experience_id}/for/{reader_id}", response_model=PersonalisedReview, responses=NOT_FOUND_RESPONSE)
def for_reader(experience_id:uuid.UUID,reader_id:uuid.UUID,db:Session=Depends(get_db)):
    obj=db.get(Experience,experience_id);reader=db.get(User,reader_id)
    if not obj or not reader: raise HTTPException(404,"Experience or reader not found")
    return personalised(db,obj,reader)

@router.get("/schemas")
def schemas(db:Session=Depends(get_db)):
    rows=db.scalars(select(SchemaDefinition)).all()
    return {"supported_subject_types":{r.subject_type:{"version":r.version,"status":r.status,"url":f"/schemas/{r.subject_type}/{r.version}"} for r in rows}}

@router.get("/schemas/{subject_type}/{version}", responses=NOT_FOUND_RESPONSE)
def schema(subject_type:str,version:str,db:Session=Depends(get_db)):
    row=db.scalar(select(SchemaDefinition).where(SchemaDefinition.subject_type==subject_type,SchemaDefinition.version==version))
    if not row: raise HTTPException(404,"Schema not found")
    return row.json_schema

@router.get("/.well-known/review-service.json")
def discovery():
    return {"service":"TasteGraph","version":"1.0","purpose":"Store and retrieve structured experiences across supported domains","openapi":"/openapi.json","schemas":"/schemas","draft_endpoint":"/api/v1/experiences/drafts","authentication":{"current":"development_api_key","planned":"oauth2"}}

@router.get("/.well-known/oauth-authorization-server")
def oauth_placeholder():
    return {"status":"not_implemented","planned":"OAuth 2.1 with scoped access tokens"}
