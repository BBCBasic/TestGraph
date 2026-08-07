from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.capability import _credential, _headers
from app.core.config import get_settings
from app.db.session import get_db
from app.models.v2 import Concept, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ConceptEnsure, ExperienceCreate, FieldProposal, SubjectEnsure
from app.services.v2 import create_assessment, create_experience, ensure_concept, ensure_subject, vocabulary

router = APIRouter(prefix="/actions-v2", tags=["ChatGPT Actions v2"])


def _base(): return get_settings().public_base_url.rstrip("/")
def _auth(db, authorization):
    if not authorization: raise HTTPException(401, "Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip(): raise HTTPException(401, "Use Authorization: Bearer <TasteGraph capability key>")
    try: return _credential(db, token.strip())
    except HTTPException as exc: raise HTTPException(401, "Invalid TasteGraph API key") from exc


@router.get("/experiences", operation_id="searchTasteGraphV2Experiences")
def search(q: str = "", concept_path: str | None = None, limit: int = Query(10, ge=1, le=20), authorization: str | None = Header(None, alias="Authorization"), db: Session = Depends(get_db)):
    cred = _auth(db, authorization)
    stmt = select(V2Experience, V2Subject, Concept).join(V2Subject, V2Experience.subject_id == V2Subject.id).join(Concept, V2Subject.concept_id == Concept.id).where(V2Experience.owner_id == cred.user_id, V2Experience.deleted_at.is_(None))
    if concept_path: stmt = stmt.where(Concept.path == concept_path)
    if q.strip():
        p=f"%{q.strip()}%"; stmt=stmt.where(or_(V2Subject.name.ilike(p), V2Subject.canonical_key.ilike(p), V2Experience.headline.ilike(p), V2Experience.summary.ilike(p)))
    rows=db.execute(stmt.order_by(V2Experience.created_at.desc()).limit(limit)).all()
    return JSONResponse({"count":len(rows),"results":[{"id":str(e.id),"subject_id":str(s.id),"subject_name":s.name,"concept_path":c.path,"headline":e.headline,"summary":e.summary} for e,s,c in rows]},headers=_headers())


@router.get("/experiences/{experience_id}", operation_id="fetchTasteGraphV2Experience")
def fetch(experience_id: uuid.UUID, authorization: str | None = Header(None, alias="Authorization"), db: Session = Depends(get_db)):
    cred=_auth(db,authorization)
    row=db.execute(select(V2Experience,V2Subject,Concept).join(V2Subject,V2Experience.subject_id==V2Subject.id).join(Concept,V2Subject.concept_id==Concept.id).where(V2Experience.id==experience_id,V2Experience.owner_id==cred.user_id,V2Experience.deleted_at.is_(None))).first()
    if not row: raise HTTPException(404,"Experience not found")
    e,s,c=row
    return JSONResponse({"id":str(e.id),"subject":{"id":str(s.id),"name":s.name,"canonical_key":s.canonical_key,"concept_path":c.path,"identifiers":s.identifiers_json,"attributes":s.attributes_json},"headline":e.headline,"summary":e.summary,"raw_text":e.raw_text,"structured_data":e.structured_data,"submitted_data":e.submitted_data,"normalization_log":e.normalization_log,"provenance":e.provenance,"created_at":e.created_at.isoformat()},headers=_headers())


@router.get("/concepts", operation_id="getTasteGraphV2Concept")
def concept(path:str, authorization:str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    _auth(db,authorization)
    from app.services.v2 import normalise_path
    canonical=normalise_path(path); obj=db.scalar(select(Concept).where(Concept.path==canonical,Concept.status=="active"))
    if not obj: return JSONResponse({"found":False,"path":canonical,"instruction":"The concept may be created during save. Propose fields only when no existing canonical field fits."},headers=_headers())
    vocab=vocabulary(db,obj); unique={f.id:f for f in vocab["fields"].values()}
    return JSONResponse({"found":True,"path":obj.path,"version":obj.version,"description":obj.description,"fields":[{"canonical_name":f.canonical_name,"data_type":f.data_type,"description":f.description,"unit":f.unit,"origin":vocab["origins"].get(f.canonical_name)} for f in unique.values()],"aliases":vocab["aliases"]},headers=_headers())


@router.post("/experiences", operation_id="saveTasteGraphV2Experience", status_code=201)
def save(payload:dict, authorization:str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    if payload.get("user_approved") is not True: raise HTTPException(400,"Explicit user approval is required before saving a direct experience")
    try:
        proposals=[FieldProposal.model_validate(x) for x in payload.get("proposed_fields",[])]
        c=ensure_concept(db,ConceptEnsure(path=payload["concept_path"],description=payload.get("concept_description"),proposed_fields=proposals,created_by=payload.get("source_client","chatgpt-action-v2")))
        s=ensure_subject(db,SubjectEnsure(concept_path=c.path,name=payload["subject_name"],canonical_key=payload["canonical_key"],identifiers=payload.get("identifiers",{}),attributes=payload.get("subject_attributes",{})),"chatgpt-action-v2")
        e=create_experience(db,ExperienceCreate(owner_id=cred.user_id,subject_id=s.id,headline=payload["headline"],summary=payload["summary"],raw_text=payload.get("raw_text"),structured_data=payload.get("structured_data",{}),proposed_fields=proposals,visibility=payload.get("visibility","private"),user_approved=True,source_client=payload.get("source_client","chatgpt-action-v2")),f"capability:{cred.id}")
    except (KeyError,ValueError) as exc:
        db.rollback(); raise HTTPException(422,exc.args[0] if exc.args else str(exc))
    return JSONResponse({"saved":True,"experience_id":str(e.id),"subject_id":str(s.id),"concept_path":c.path,"canonical_data":e.structured_data,"normalization_log":e.normalization_log,"read_back":f"{_base()}/actions-v2/experiences/{e.id}"},status_code=201,headers=_headers())


@router.post("/assessments", operation_id="saveTasteGraphV2Assessment", status_code=201)
def assessment(payload:dict, authorization:str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    try:
        obj=create_assessment(db,AssessmentCreate(subject_id=payload["subject_id"],user_id=cred.user_id,assessment_type=payload["assessment_type"],evidence=payload.get("evidence",{}),analysis=payload.get("analysis",{}),conclusion=payload.get("conclusion"),confidence=payload.get("confidence"),source_model=payload.get("source_model","chatgpt"),provenance=payload.get("provenance",{})))
    except (KeyError,ValueError) as exc:
        db.rollback(); raise HTTPException(422,str(exc))
    return JSONResponse({"saved":True,"assessment_id":str(obj.id),"subject_id":str(obj.subject_id),"provenance_kind":obj.provenance.get("kind")},status_code=201,headers=_headers())


@router.get("/openapi.json", include_in_schema=False)
def openapi():
    proposal={"type":"object","additionalProperties":False,"required":["submitted_name","canonical_name"],"properties":{"submitted_name":{"type":"string"},"canonical_name":{"type":"string"},"data_type":{"type":"string"},"description":{"type":"string"},"unit":{"type":"string"},"aliases":{"type":"array","items":{"type":"string"}}}}
    experience={"type":"object","additionalProperties":False,"required":["concept_path","subject_name","canonical_key","headline","summary","user_approved"],"properties":{"concept_path":{"type":"string"},"concept_description":{"type":"string"},"subject_name":{"type":"string"},"canonical_key":{"type":"string"},"identifiers":{"type":"object","additionalProperties":True},"subject_attributes":{"type":"object","additionalProperties":True},"headline":{"type":"string"},"summary":{"type":"string"},"raw_text":{"type":"string"},"structured_data":{"type":"object","additionalProperties":True},"proposed_fields":{"type":"array","items":proposal},"visibility":{"type":"string","enum":["private","unlisted","public","aggregate_only"]},"user_approved":{"type":"boolean"},"source_client":{"type":"string"}}}
    assessment={"type":"object","additionalProperties":False,"required":["subject_id","assessment_type"],"properties":{"subject_id":{"type":"string","format":"uuid"},"assessment_type":{"type":"string"},"evidence":{"type":"object","additionalProperties":True},"analysis":{"type":"object","additionalProperties":True},"conclusion":{"type":"string"},"confidence":{"type":"number","minimum":0,"maximum":1},"source_model":{"type":"string"},"provenance":{"type":"object","additionalProperties":True}}}
    return {"openapi":"3.1.0","info":{"title":"TasteGraph v2 ChatGPT Action","version":"2.0.0-alpha","description":"Flexible cross-AI experience memory. Direct user experiences and AI-derived assessments have separate provenance."},"servers":[{"url":_base()}],"components":{"securitySchemes":{"bearerAuth":{"type":"http","scheme":"bearer"}},"schemas":{"ExperienceCreate":experience,"AssessmentCreate":assessment}},"security":[{"bearerAuth":[]}],"paths":{"/actions-v2/experiences":{"get":{"operationId":"searchTasteGraphV2Experiences","summary":"Search experiences","responses":{"200":{"description":"Results"}}},"post":{"operationId":"saveTasteGraphV2Experience","summary":"Save approved direct experience","x-openai-isConsequential":True,"requestBody":{"required":True,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/ExperienceCreate"}}}},"responses":{"201":{"description":"Saved"}}}},"/actions-v2/experiences/{experience_id}":{"get":{"operationId":"fetchTasteGraphV2Experience","summary":"Fetch experience","parameters":[{"name":"experience_id","in":"path","required":True,"schema":{"type":"string","format":"uuid"}}],"responses":{"200":{"description":"Experience"}}}},"/actions-v2/concepts":{"get":{"operationId":"getTasteGraphV2Concept","summary":"Get concept vocabulary","parameters":[{"name":"path","in":"query","required":True,"schema":{"type":"string"}}],"responses":{"200":{"description":"Concept"}}}},"/actions-v2/assessments":{"post":{"operationId":"saveTasteGraphV2Assessment","summary":"Save AI-derived assessment","x-openai-isConsequential":True,"requestBody":{"required":True,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/AssessmentCreate"}}}},"responses":{"201":{"description":"Saved"}}}}}}
