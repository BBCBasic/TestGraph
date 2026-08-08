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
from app.schemas.v2 import AssessmentCreate, ExperienceCreate, SubjectEnsure
from app.services.semantic import list_alias_candidates, propose_alias
from app.services.v2 import create_assessment, create_experience, ensure_subject, normalise_path, vocabulary
from app.services.write_safety import begin_idempotent_write, finish_idempotent_write

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
    canonical=normalise_path(path); obj=db.scalar(select(Concept).where(Concept.path==canonical,Concept.status=="active"))
    if not obj: return JSONResponse({"found":False,"path":canonical,"instruction":"The concept may be created during save. Use your own semantic judgement before proposing new fields."},headers=_headers())
    vocab=vocabulary(db,obj); unique={f.id:f for f in vocab["fields"].values()}
    return JSONResponse({"found":True,"path":obj.path,"version":obj.version,"description":obj.description,"fields":[{"canonical_name":f.canonical_name,"data_type":f.data_type,"description":f.description,"unit":f.unit,"origin":vocab["origins"].get(f.canonical_name)} for f in unique.values()],"accepted_aliases":vocab["aliases"],"alias_candidates":list_alias_candidates(db,obj),"semantic_policy":"The calling AI judges language meaning. TasteGraph records proposals, detects conflicts, and promotes an alias only after independent client consensus."},headers=_headers())


@router.post("/alias-proposals", operation_id="proposeTasteGraphV2Alias", status_code=201)
def alias_proposal(payload:dict, authorization:str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    try:
        path=normalise_path(payload["concept_path"])
        obj=db.scalar(select(Concept).where(Concept.path==path,Concept.status=="active"))
        if not obj: raise HTTPException(404,"Concept not found")
        status=propose_alias(db,concept=obj,alias=payload["alias"],canonical_name=payload["canonical_name"],proposer_client_id=f"capability:{cred.id}:v2",confidence=payload.get("confidence"),rationale=payload.get("rationale"))
        db.commit()
    except KeyError as exc:
        db.rollback(); raise HTTPException(422,f"Missing field: {exc.args[0]}")
    except ValueError as exc:
        db.rollback(); raise HTTPException(422,str(exc))
    return JSONResponse(status,status_code=201,headers=_headers())


@router.post("/experiences", operation_id="saveTasteGraphV2Experience", status_code=201)
def save(payload:dict, authorization:str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    if payload.get("user_approved") is not True:
        raise HTTPException(400,"Explicit user approval is required before saving a direct experience")
    try:
        key=payload["idempotency_key"]
        relevant={k:v for k,v in payload.items() if k!="idempotency_key"}
        client_id=f"capability:{cred.id}:v2"
        payload_hash,prior=begin_idempotent_write(db,client_id=client_id,key=f"experience:{key}",payload=relevant)
        if prior is not None:
            return JSONResponse(prior,status_code=201,headers=_headers())
        path=normalise_path(payload["concept_path"])
        concept=db.scalar(select(Concept).where(Concept.path==path,Concept.status=="active"))
        if not concept:
            raise ValueError("Concept does not exist; propose and approve its fields before saving")
        subject=ensure_subject(
            db,
            SubjectEnsure(
                concept_path=concept.path,
                name=payload["subject_name"],
                canonical_key=payload["canonical_key"],
                identifiers=payload.get("identifiers",{}),
                attributes=payload.get("subject_attributes",{}),
                create_concept_if_missing=False,
            ),
            client_id,
        )
        experience=create_experience(
            db,
            ExperienceCreate(
                owner_id=cred.user_id,
                subject_id=subject.id,
                headline=payload["headline"],
                summary=payload["summary"],
                raw_text=payload["raw_text"],
                structured_data=payload.get("structured_data",{}),
                visibility=payload.get("visibility","private"),
                user_approved=True,
                source_client=client_id,
            ),
            client_id,
        )
        body={"saved":True,"experience_id":str(experience.id),"subject_id":str(subject.id),"concept_path":concept.path,"canonical_data":experience.structured_data,"normalization_log":experience.normalization_log,"alias_candidates":list_alias_candidates(db,concept),"read_back":f"{_base()}/actions-v2/experiences/{experience.id}"}
        finish_idempotent_write(db,client_id=client_id,key=f"experience:{key}",payload_hash=payload_hash,response_body=body)
    except (KeyError,ValueError) as exc:
        db.rollback()
        raise HTTPException(422,exc.args[0] if exc.args else str(exc))
    return JSONResponse(body,status_code=201,headers=_headers())


@router.post("/assessments", operation_id="saveTasteGraphV2Assessment", status_code=201)
def assessment(payload:dict, authorization:str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    try:
        key=payload["idempotency_key"]; relevant={k:v for k,v in payload.items() if k!="idempotency_key"}; client_id=f"capability:{cred.id}:v2"
        payload_hash,prior=begin_idempotent_write(db,client_id=client_id,key=f"assessment:{key}",payload=relevant)
        if prior is not None: return JSONResponse(prior,status_code=201,headers=_headers())
        obj=create_assessment(db,AssessmentCreate(subject_id=payload["subject_id"],user_id=cred.user_id,assessment_type=payload["assessment_type"],evidence=payload.get("evidence",{}),analysis=payload.get("analysis",{}),conclusion=payload.get("conclusion"),confidence=payload.get("confidence"),source_model=payload.get("source_model","chatgpt"),provenance=payload.get("provenance",{})))
        body={"saved":True,"assessment_id":str(obj.id),"subject_id":str(obj.subject_id),"provenance_kind":obj.provenance.get("kind")}
        finish_idempotent_write(db,client_id=client_id,key=f"assessment:{key}",payload_hash=payload_hash,response_body=body)
    except (KeyError,ValueError) as exc:
        db.rollback(); raise HTTPException(422,str(exc))
    return JSONResponse(body,status_code=201,headers=_headers())


@router.get("/openapi.json", include_in_schema=False)
def openapi():
    alias_proposal={"type":"object","additionalProperties":False,"required":["concept_path","alias","canonical_name"],"properties":{"concept_path":{"type":"string"},"alias":{"type":"string"},"canonical_name":{"type":"string"},"confidence":{"type":"number","minimum":0,"maximum":1},"rationale":{"type":"string"}}}
    idem={"type":"string","minLength":8,"maxLength":200,"description":"Stable key reused only when retrying the same write."}
    experience={"type":"object","additionalProperties":False,"required":["concept_path","subject_name","canonical_key","headline","summary","raw_text","user_approved","idempotency_key"],"properties":{"concept_path":{"type":"string"},"subject_name":{"type":"string"},"canonical_key":{"type":"string"},"identifiers":{"type":"object","additionalProperties":True},"subject_attributes":{"type":"object","additionalProperties":True},"headline":{"type":"string"},"summary":{"type":"string"},"raw_text":{"type":"string","minLength":1},"structured_data":{"type":"object","additionalProperties":True},"visibility":{"type":"string","enum":["private","unlisted","public","aggregate_only"]},"user_approved":{"type":"boolean"},"idempotency_key":idem}}
    assessment={"type":"object","additionalProperties":False,"required":["subject_id","assessment_type","idempotency_key"],"properties":{"subject_id":{"type":"string","format":"uuid"},"assessment_type":{"type":"string"},"evidence":{"type":"object","additionalProperties":True},"analysis":{"type":"object","additionalProperties":True},"conclusion":{"type":"string"},"confidence":{"type":"number","minimum":0,"maximum":1},"source_model":{"type":"string"},"provenance":{"type":"object","additionalProperties":True},"idempotency_key":idem}}
    return {"openapi":"3.1.0","info":{"title":"TasteGraph v2 ChatGPT Action","version":"2.1.0-alpha","description":"Cross-AI experience memory. The calling AI performs semantic judgement; TasteGraph coordinates independent proposals, conflicts, consensus and canonical storage."},"servers":[{"url":_base()}],"components":{"securitySchemes":{"bearerAuth":{"type":"http","scheme":"bearer"}},"schemas":{"ExperienceCreate":experience,"AssessmentCreate":assessment,"AliasProposal":alias_proposal}},"security":[{"bearerAuth":[]}],"paths":{"/actions-v2/experiences":{"get":{"operationId":"searchTasteGraphV2Experiences","summary":"Search experiences","responses":{"200":{"description":"Results"}}},"post":{"operationId":"saveTasteGraphV2Experience","summary":"Save approved direct experience","x-openai-isConsequential":True,"requestBody":{"required":True,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/ExperienceCreate"}}}},"responses":{"201":{"description":"Saved"}}}},"/actions-v2/experiences/{experience_id}":{"get":{"operationId":"fetchTasteGraphV2Experience","summary":"Fetch experience","parameters":[{"name":"experience_id","in":"path","required":True,"schema":{"type":"string","format":"uuid"}}],"responses":{"200":{"description":"Experience"}}}},"/actions-v2/concepts":{"get":{"operationId":"getTasteGraphV2Concept","summary":"Get canonical vocabulary, accepted aliases and semantic proposals","parameters":[{"name":"path","in":"query","required":True,"schema":{"type":"string"}}],"responses":{"200":{"description":"Concept"}}}},"/actions-v2/alias-proposals":{"post":{"operationId":"proposeTasteGraphV2Alias","summary":"Propose that a term means an existing canonical field","x-openai-isConsequential":False,"requestBody":{"required":True,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/AliasProposal"}}}},"responses":{"201":{"description":"Proposal recorded or consensus alias accepted"}}}},"/actions-v2/assessments":{"post":{"operationId":"saveTasteGraphV2Assessment","summary":"Save AI-derived assessment","x-openai-isConsequential":True,"requestBody":{"required":True,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/AssessmentCreate"}}}},"responses":{"201":{"description":"Saved"}}}}}}
