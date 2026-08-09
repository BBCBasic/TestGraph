from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.capability import _credential, _headers
from app.core.config import get_settings
from app.db.session import get_db
from app.models.v2 import Assessment, Concept, ConceptFieldProposal, V2Experience, V2Subject
from app.schemas.v2 import AssessmentCreate, ConceptEnsure, ExperienceCreate, FieldProposal, SubjectEnsure
from app.services.semantic import list_alias_candidates, propose_alias
from app.services.v2 import (
    create_assessment,
    create_experience,
    ensure_concept,
    ensure_subject,
    normalise_path,
    propose_concept_fields,
    vocabulary,
)
from app.services.vocabulary_governance import verify_field_proposal, vocabulary_index
from app.services.write_safety import begin_idempotent_write, finish_idempotent_write

router = APIRouter(prefix="/actions-v2", tags=["ChatGPT Actions v2"])


def _base(): return get_settings().public_base_url.rstrip("/")
def _auth(db, authorization):
    if not authorization: raise HTTPException(401, "Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip(): raise HTTPException(401, "Use Authorization: Bearer <TasteGraph capability key>")
    try: return _credential(db, token.strip())
    except HTTPException as exc: raise HTTPException(401, "Invalid TasteGraph API key") from exc


def _concept_route(path: str | None = None) -> str:
    base = f"{_base()}/actions-v2/concepts"
    return f"{base}?path={path}" if path else base


def _concept_ref(obj: Concept) -> dict:
    return {"id": str(obj.id), "name": obj.name, "path": obj.path, "version": obj.version, "canonical_route": _concept_route(obj.path)}


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
    assessments=list(db.scalars(select(Assessment).where(Assessment.experience_id==e.id).order_by(Assessment.created_at)).all())
    return JSONResponse({"id":str(e.id),"subject":{"id":str(s.id),"name":s.name,"canonical_key":s.canonical_key,"concept_path":c.path,"identifiers":s.identifiers_json,"attributes":s.attributes_json},"headline":e.headline,"summary":e.summary,"raw_text":e.raw_text,"structured_data":e.structured_data,"submitted_data":e.submitted_data,"normalization_log":e.normalization_log,"provenance":e.provenance,"assessments":[{"id":str(a.id),"experience_id":str(a.experience_id),"assessment_type":a.assessment_type,"evidence":a.evidence_json,"analysis":a.analysis_json,"conclusion":a.conclusion,"confidence":a.confidence,"source_model":a.source_model,"provenance":a.provenance,"created_by_client":a.created_by_client,"created_at":a.created_at.isoformat()} for a in assessments],"created_at":e.created_at.isoformat()},headers=_headers())


@router.get("/concepts", operation_id="getTasteGraphV2Concept")
def concept(path: str | None = None, authorization: str | None = Header(None, alias="Authorization"), db: Session = Depends(get_db)):
    _auth(db,authorization)
    if path is None or not path.strip():
        roots = list(db.scalars(select(Concept).where(Concept.parent_id.is_(None), Concept.status == "active").order_by(Concept.path)).all())
        return JSONResponse({"found":True,"kind":"concept_root","path":None,"canonical_route":_concept_route(),"children":[_concept_ref(child) for child in roots],"has_children":bool(roots),"instruction":"Start here when the correct concept is unknown. Search the global vocabulary index for relevant words, then follow children to the most specific applicable concept."},headers=_headers())
    canonical=normalise_path(path)
    obj=db.scalar(select(Concept).where(Concept.path==canonical,Concept.status=="active"))
    if not obj:
        return JSONResponse({"found":False,"path":canonical,"canonical_route":_concept_route(canonical),"root_route":_concept_route(),"instruction":"Concept not found. Search the global vocabulary index and browse from root before proposing a genuinely new path."},headers=_headers())
    parent = db.get(Concept, obj.parent_id) if obj.parent_id else None
    if parent is not None and parent.status != "active": parent = None
    children = list(db.scalars(select(Concept).where(Concept.parent_id == obj.id, Concept.status == "active").order_by(Concept.path)).all())
    vocab=vocabulary(db,obj); unique={f.id:f for f in vocab["fields"].values()}
    return JSONResponse({"found":True,"kind":"concept","id":str(obj.id),"name":obj.name,"path":obj.path,"canonical_route":_concept_route(obj.path),"root_route":_concept_route(),"parent":_concept_ref(parent) if parent else None,"children":[_concept_ref(child) for child in children],"has_children":bool(children),"version":obj.version,"description":obj.description,"fields":[{"canonical_name":f.canonical_name,"data_type":f.data_type,"description":f.description,"unit":f.unit,"origin":vocab["origins"].get(f.canonical_name)} for f in unique.values()],"accepted_aliases":vocab["aliases"],"alias_candidates":list_alias_candidates(db,obj),"semantic_policy":"Canonical and pending vocabulary is globally indexed by word. One authenticated AI proposes a new field; a different authenticated AI may verify and commit it. A proposer cannot verify its own proposal."},headers=_headers())


@router.get("/vocabulary-index", operation_id="searchTasteGraphV2VocabularyIndex")
def word_index(word: str | None = None, authorization: str | None = Header(None, alias="Authorization"), db: Session = Depends(get_db)):
    _auth(db, authorization)
    return JSONResponse(vocabulary_index(db, word), headers=_headers())


@router.post("/concept-field-proposals", operation_id="proposeTasteGraphV2ConceptFields", status_code=201)
def concept_field_proposal(payload:dict, authorization:str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    try:
        proposals=[FieldProposal.model_validate(item) for item in payload.get("fields",[])]
        if not proposals: raise ValueError("At least one field proposal is required")
        client_id=f"capability:{cred.id}:v2"
        concept=ensure_concept(db,ConceptEnsure(path=payload["concept_path"],description=payload.get("concept_description"),created_by=client_id))
        rows=propose_concept_fields(db,concept=concept,proposals=proposals,proposer_client_id=client_id)
        body={"concept_path":concept.path,"concept_version":concept.version,"proposals":[{"id":str(row.id),"canonical_name":row.canonical_name,"json_schema":row.json_schema,"status":row.status} for row in rows],"verification_required":True,"instruction":"A different authenticated AI should find the pending words through the global vocabulary index, inspect the proposal, and call verifyTasteGraphV2ConceptFieldProposal if it agrees. The proposing client cannot self-verify.","manual_approval_url":f"{_base()}/development/concept-fields","experience_created":False}
        db.commit()
    except KeyError as exc:
        db.rollback(); raise HTTPException(422,f"Missing field: {exc.args[0]}")
    except ValueError as exc:
        db.rollback(); raise HTTPException(422,str(exc))
    return JSONResponse(body,status_code=201,headers=_headers())


@router.post("/concept-field-proposals/{proposal_id}/verify", operation_id="verifyTasteGraphV2ConceptFieldProposal", status_code=200)
def verify_concept_field(proposal_id: uuid.UUID, payload:dict | None = None, authorization:str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    client_id=f"capability:{cred.id}:v2"
    try:
        proposal, field = verify_field_proposal(db, proposal_id, verifier_client_id=client_id, reason=(payload or {}).get("rationale"))
        concept = db.get(Concept, proposal.concept_id)
    except ValueError as exc:
        db.rollback(); raise HTTPException(422,str(exc))
    return JSONResponse({"committed":True,"proposal_id":str(proposal.id),"status":proposal.status,"concept_path":concept.path if concept else None,"field_id":str(field.id),"canonical_name":field.canonical_name,"proposed_by":proposal.proposer_client_id,"verified_by":proposal.decision_by,"verification_reason":proposal.decision_reason},headers=_headers())


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
    if payload.get("user_approved") is not True: raise HTTPException(400,"Explicit user approval is required before saving a direct experience")
    try:
        key=payload["idempotency_key"]; relevant={k:v for k,v in payload.items() if k!="idempotency_key"}; client_id=f"capability:{cred.id}:v2"
        payload_hash,prior=begin_idempotent_write(db,client_id=client_id,key=f"experience:{key}",payload=relevant)
        if prior is not None: return JSONResponse(prior,status_code=201,headers=_headers())
        path=normalise_path(payload["concept_path"]); concept=db.scalar(select(Concept).where(Concept.path==path,Concept.status=="active"))
        if not concept: raise ValueError("Concept does not exist; search the vocabulary index, then propose fields and have another AI verify them before saving")
        subject=ensure_subject(db,SubjectEnsure(concept_path=concept.path,name=payload["subject_name"],canonical_key=payload["canonical_key"],identifiers=payload.get("identifiers",{}),attributes=payload.get("subject_attributes",{}),create_concept_if_missing=False),client_id)
        experience=create_experience(db,ExperienceCreate(owner_id=cred.user_id,subject_id=subject.id,headline=payload["headline"],summary=payload["summary"],raw_text=payload["raw_text"],structured_data=payload.get("structured_data",{}),visibility=payload.get("visibility","private"),user_approved=True,source_client=client_id),client_id)
        body={"saved":True,"experience_id":str(experience.id),"subject_id":str(subject.id),"concept_path":concept.path,"canonical_data":experience.structured_data,"normalization_log":experience.normalization_log,"alias_candidates":list_alias_candidates(db,concept),"read_back":f"{_base()}/actions-v2/experiences/{experience.id}"}
        finish_idempotent_write(db,client_id=client_id,key=f"experience:{key}",payload_hash=payload_hash,response_body=body)
    except (KeyError,ValueError) as exc:
        db.rollback(); raise HTTPException(422,exc.args[0] if exc.args else str(exc))
    return JSONResponse(body,status_code=201,headers=_headers())


@router.post("/assessments", operation_id="saveTasteGraphV2Assessment", status_code=201)
def assessment(payload:dict, authorization:str|None=Header(None,alias="Authorization"), db:Session=Depends(get_db)):
    cred=_auth(db,authorization)
    try:
        key=payload["idempotency_key"]; relevant={k:v for k,v in payload.items() if k!="idempotency_key"}; client_id=f"capability:{cred.id}:v2"
        payload_hash,prior=begin_idempotent_write(db,client_id=client_id,key=f"assessment:{key}",payload=relevant)
        if prior is not None: return JSONResponse(prior,status_code=201,headers=_headers())
        obj=create_assessment(db,AssessmentCreate(experience_id=payload["experience_id"],assessment_type=payload["assessment_type"],evidence=payload.get("evidence",{}),analysis=payload.get("analysis",{}),conclusion=payload.get("conclusion"),confidence=payload.get("confidence"),source_model=payload.get("source_model","chatgpt"),provenance=payload.get("provenance",{})),client_id=client_id,user_id=cred.user_id)
        body={"saved":True,"assessment_id":str(obj.id),"experience_id":str(obj.experience_id),"subject_id":str(obj.subject_id),"provenance":obj.provenance,"created_by_client":obj.created_by_client}
        finish_idempotent_write(db,client_id=client_id,key=f"assessment:{key}",payload_hash=payload_hash,response_body=body)
    except (KeyError,ValueError) as exc:
        db.rollback(); raise HTTPException(422,str(exc))
    return JSONResponse(body,status_code=201,headers=_headers())


@router.get("/openapi.json", include_in_schema=False)
def openapi():
    field={"type":"object","additionalProperties":False,"required":["submitted_name","canonical_name","json_schema"],"properties":{"submitted_name":{"type":"string"},"canonical_name":{"type":"string"},"json_schema":{"type":"object","additionalProperties":True,"description":"Complete JSON Schema for the proposed field, including object properties and array items."},"description":{"type":"string"},"aliases":{"type":"array","items":{"type":"string"}}}}
    concept_field_proposal={"type":"object","additionalProperties":False,"required":["concept_path","fields"],"properties":{"concept_path":{"type":"string","description":"Canonical dotted path after searching the vocabulary index and browsing the existing concept tree."},"concept_description":{"type":"string"},"fields":{"type":"array","minItems":1,"items":field}}}
    verification={"type":"object","additionalProperties":False,"properties":{"rationale":{"type":"string","description":"Why this independent AI agrees the proposed canonical field and JSON Schema are correct."}}}
    alias_proposal={"type":"object","additionalProperties":False,"required":["concept_path","alias","canonical_name"],"properties":{"concept_path":{"type":"string"},"alias":{"type":"string"},"canonical_name":{"type":"string"},"confidence":{"type":"number","minimum":0,"maximum":1},"rationale":{"type":"string"}}}
    idem={"type":"string","minLength":8,"maxLength":200,"description":"Stable key reused only when retrying the same write."}
    experience={"type":"object","additionalProperties":False,"required":["concept_path","subject_name","canonical_key","headline","summary","raw_text","user_approved","idempotency_key"],"properties":{"concept_path":{"type":"string"},"subject_name":{"type":"string"},"canonical_key":{"type":"string"},"identifiers":{"type":"object","additionalProperties":True},"subject_attributes":{"type":"object","additionalProperties":True},"headline":{"type":"string"},"summary":{"type":"string"},"raw_text":{"type":"string","minLength":1},"structured_data":{"type":"object","additionalProperties":True},"visibility":{"type":"string","enum":["private","unlisted","public","aggregate_only"]},"user_approved":{"type":"boolean"},"idempotency_key":idem}}
    assessment={"type":"object","additionalProperties":False,"required":["experience_id","assessment_type","idempotency_key"],"properties":{"experience_id":{"type":"string","format":"uuid","description":"Exact experience evaluated. Subject, owner and authenticated provenance are derived by TasteGraph."},"assessment_type":{"type":"string"},"evidence":{"type":"object","additionalProperties":True},"analysis":{"type":"object","additionalProperties":True},"conclusion":{"type":"string"},"confidence":{"type":"number","minimum":0,"maximum":1},"source_model":{"type":"string"},"provenance":{"type":"object","additionalProperties":True},"idempotency_key":idem}}
    return {"openapi":"3.1.0","info":{"title":"TasteGraph v2 ChatGPT Action","version":"2.4.0-alpha","description":"Cross-AI experience memory with a DNS-style canonical concept tree and a global word-to-position vocabulary index. Pending schema proposals are discoverable in the same index and require independent verification by a different authenticated AI before promotion."},"servers":[{"url":_base()}],"components":{"securitySchemes":{"bearerAuth":{"type":"http","scheme":"bearer"}},"schemas":{"ExperienceCreate":experience,"AssessmentCreate":assessment,"AliasProposal":alias_proposal,"ConceptFieldProposal":concept_field_proposal,"ConceptFieldVerification":verification}},"security":[{"bearerAuth":[]}],"paths":{"/actions-v2/experiences":{"get":{"operationId":"searchTasteGraphV2Experiences","summary":"Search experiences","responses":{"200":{"description":"Results"}}},"post":{"operationId":"saveTasteGraphV2Experience","summary":"Save approved direct experience","x-openai-isConsequential":True,"requestBody":{"required":True,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/ExperienceCreate"}}}},"responses":{"201":{"description":"Saved"}}}},"/actions-v2/experiences/{experience_id}":{"get":{"operationId":"fetchTasteGraphV2Experience","summary":"Fetch experience with its AI-derived assessments","parameters":[{"name":"experience_id","in":"path","required":True,"schema":{"type":"string","format":"uuid"}}],"responses":{"200":{"description":"Experience and linked assessments"}}}},"/actions-v2/concepts":{"get":{"operationId":"getTasteGraphV2Concept","summary":"Browse the canonical concept tree or fetch one concept with its parent, children and vocabulary","parameters":[{"name":"path","in":"query","required":False,"schema":{"type":"string"}}],"responses":{"200":{"description":"Concept root or concept node"}}}},"/actions-v2/vocabulary-index":{"get":{"operationId":"searchTasteGraphV2VocabularyIndex","summary":"Find every canonical or pending DNS-tree position containing a word; omit word for the full index","parameters":[{"name":"word","in":"query","required":False,"schema":{"type":"string"}}],"responses":{"200":{"description":"Word-to-position index results"}}}},"/actions-v2/concept-field-proposals":{"post":{"operationId":"proposeTasteGraphV2ConceptFields","summary":"Propose new canonical fields; pending words become globally discoverable for independent AI verification","x-openai-isConsequential":False,"requestBody":{"required":True,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/ConceptFieldProposal"}}}},"responses":{"201":{"description":"Pending proposal recorded"}}}},"/actions-v2/concept-field-proposals/{proposal_id}/verify":{"post":{"operationId":"verifyTasteGraphV2ConceptFieldProposal","summary":"Independently verify and commit another AI's pending field proposal","x-openai-isConsequential":False,"parameters":[{"name":"proposal_id","in":"path","required":True,"schema":{"type":"string","format":"uuid"}}],"requestBody":{"required":False,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/ConceptFieldVerification"}}}},"responses":{"200":{"description":"Proposal promoted to canonical vocabulary"},"422":{"description":"Invalid proposal or self-verification attempt"}}}},"/actions-v2/alias-proposals":{"post":{"operationId":"proposeTasteGraphV2Alias","summary":"Propose that a term means an existing canonical field","x-openai-isConsequential":False,"requestBody":{"required":True,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/AliasProposal"}}}},"responses":{"201":{"description":"Proposal recorded or consensus alias accepted"}}}},"/actions-v2/assessments":{"post":{"operationId":"saveTasteGraphV2Assessment","summary":"Save routine AI-derived analysis against an exact experience; linkage and provenance are automatic","x-openai-isConsequential":False,"requestBody":{"required":True,"content":{"application/json":{"schema":{"$ref":"#/components/schemas/AssessmentCreate"}}}},"responses":{"201":{"description":"Saved and automatically linked"}}}}}}
