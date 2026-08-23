from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.capability import _credential, _headers
from app.db.session import get_db
from app.models.v2 import Assessment, SubjectType, V2Experience, V2Subject
from app.schemas.v2 import ExperienceCreate, SubjectEnsure
from app.services.v2 import create_experience, ensure_subject, ensure_subject_type, resolve_subject_type, vocabulary_index
from app.services.write_safety import begin_idempotent_write, finish_idempotent_write

router = APIRouter(prefix="/actions-v2", tags=["ChatGPT Actions v2"])


def _auth(db, authorization):
    if not authorization: raise HTTPException(401,"Missing Authorization header")
    scheme,_,token=authorization.partition(" ")
    if scheme.lower()!="bearer" or not token.strip(): raise HTTPException(401,"Use Bearer authentication")
    return _credential(db,token.strip())


@router.get("/vocabulary", operation_id="getTasteGraphVocabulary")
def vocabulary(authorization: str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    _auth(db,authorization); return JSONResponse(vocabulary_index(db),headers=_headers())


@router.get("/experiences", operation_id="searchTasteGraphExperiences")
def search(q:str="",subject_type:str|None=None,limit:int=Query(10,ge=1,le=20),authorization:str|None=Header(None,alias="Authorization"),db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    stmt=select(V2Experience,V2Subject,SubjectType).join(V2Subject,V2Experience.subject_id==V2Subject.id).join(SubjectType,V2Subject.subject_type_id==SubjectType.id).where(V2Experience.owner_id==cred.user_id,V2Experience.deleted_at.is_(None))
    if subject_type:
        resolved=resolve_subject_type(db,subject_type)
        if not resolved:return JSONResponse({"count":0,"results":[]},headers=_headers())
        stmt=stmt.where(SubjectType.id==resolved.id)
    if q.strip():
        p=f"%{q.strip()}%";stmt=stmt.where(or_(V2Subject.name.ilike(p),V2Experience.headline.ilike(p),V2Experience.summary.ilike(p),V2Experience.raw_text.ilike(p)))
    rows=db.execute(stmt.order_by(V2Experience.created_at.desc()).limit(limit)).all()
    return JSONResponse({"count":len(rows),"results":[{"id":str(e.id),"subject_name":s.name,"subject_type_id":str(t.id),"subject_type":t.canonical_name,"headline":e.headline,"summary":e.summary} for e,s,t in rows]},headers=_headers())


@router.get("/experiences/{experience_id}", operation_id="fetchTasteGraphExperience")
def fetch(experience_id:uuid.UUID,authorization:str|None=Header(None,alias="Authorization"),db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    row=db.execute(select(V2Experience,V2Subject,SubjectType).join(V2Subject,V2Experience.subject_id==V2Subject.id).join(SubjectType,V2Subject.subject_type_id==SubjectType.id).where(V2Experience.id==experience_id,V2Experience.owner_id==cred.user_id,V2Experience.deleted_at.is_(None))).first()
    if not row:raise HTTPException(404,"Experience not found")
    e,s,t=row;assessments=list(db.scalars(select(Assessment).where(Assessment.experience_id==e.id)).all())
    return JSONResponse({"id":str(e.id),"record_type":e.record_type,"subject":{"id":str(s.id),"name":s.name,"canonical_key":s.canonical_key,"subject_type_id":str(t.id),"subject_type":t.canonical_name},"headline":e.headline,"summary":e.summary,"raw_text":e.raw_text,"structured_data":e.structured_data,"provenance":e.provenance,"assessments":[{"id":str(a.id),"assessment_type":a.assessment_type,"conclusion":a.conclusion,"provenance":a.provenance} for a in assessments]},headers=_headers())


@router.post("/experiences", operation_id="saveTasteGraphExperience", status_code=201)
def save(payload:dict,authorization:str|None=Header(None,alias="Authorization"),db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    if payload.get("user_approved") is not True:raise HTTPException(422,"Explicit user approval is required")
    client_id=f"capability:{cred.id}:v3"; key=str(payload.get("idempotency_key", ""))
    if len(key)<8:raise HTTPException(422,"idempotency_key must contain at least 8 characters")
    relevant={k:v for k,v in payload.items() if k!="idempotency_key"}
    payload_hash,prior=begin_idempotent_write(db,client_id=client_id,key=f"experience:{key}",payload=relevant)
    if prior is not None:return JSONResponse(prior,headers=_headers())
    try:
        st,created,resolution=ensure_subject_type(db,str(payload["subject_type"]),created_by=client_id)
        subject=ensure_subject(db,SubjectEnsure(subject_type=st.canonical_name,name=payload["subject_name"],canonical_key=payload["canonical_key"],identifiers=payload.get("identifiers",{}),attributes=payload.get("subject_attributes",{})),client_id)
        exp=create_experience(db,ExperienceCreate(owner_id=cred.user_id,subject_id=subject.id,headline=payload["headline"],summary=payload["summary"],raw_text=payload["raw_text"],structured_data=payload.get("structured_data",{}),visibility=payload.get("visibility","private"),user_approved=True,source_client=client_id),client_id)
        body={"saved":True,"experience_id":str(exp.id),"subject_type_id":str(st.id),"subject_type":st.canonical_name,"type_created":created,"type_resolution":resolution}
        finish_idempotent_write(db,client_id=client_id,key=f"experience:{key}",payload_hash=payload_hash,response_body=body)
        return JSONResponse(body,status_code=201,headers=_headers())
    except (KeyError,ValueError) as exc:
        db.rollback();raise HTTPException(422,str(exc))
