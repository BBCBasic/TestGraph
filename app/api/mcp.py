from __future__ import annotations
import json
import uuid
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.security import Principal, TokenError, principal_from_authorization
from app.db.session import get_db
from app.models.entities import Experience, IdempotencyRecord, Subject
from app.schemas.common import Consent, ExperienceCreate, ExperienceRead, Provenance
from app.services.core import create_experience, publish_experience, request_hash

router = APIRouter()
PROTOCOL_VERSION = "2025-06-18"

READ_SECURITY = [{"type":"oauth2","scopes":["reviews:read"]}]
WRITE_SECURITY = [{"type":"oauth2","scopes":["reviews:write"]}]

TOOLS = [
    {"name":"search","title":"Search TasteGraph reviews",
     "description":"Use this when the user asks what they have reviewed, wants prior experiences, or needs taste evidence for a recommendation.",
     "inputSchema":{"type":"object","properties":{"query":{"type":"string","description":"Words from the place, dish, headline or review."},"subject_type":{"type":"string","enum":["recipe","restaurant"]},"limit":{"type":"integer","minimum":1,"maximum":20,"default":10}},"required":["query"],"additionalProperties":False},
     "securitySchemes":READ_SECURITY,"annotations":{"readOnlyHint":True,"destructiveHint":False,"openWorldHint":False}},
    {"name":"fetch","title":"Fetch a TasteGraph review",
     "description":"Use this when a prior search returned a review the user or assistant needs to inspect in full.",
     "inputSchema":{"type":"object","properties":{"id":{"type":"string","description":"TasteGraph experience ID returned by search."}},"required":["id"],"additionalProperties":False},
     "securitySchemes":READ_SECURITY,"annotations":{"readOnlyHint":True,"destructiveHint":False,"openWorldHint":False}},
    {"name":"save_review","title":"Save an approved TasteGraph review",
     "description":"Use this only after the user has explicitly approved the completed review. It resolves or creates the subject, saves the review, publishes it, and safely handles retries.",
     "inputSchema":{"type":"object","properties":{
         "subject_type":{"type":"string","enum":["recipe","restaurant"]},
         "subject_name":{"type":"string","minLength":1,"maxLength":240},
         "canonical_key":{"type":"string","minLength":1,"maxLength":300,"description":"Stable lowercase identifier, including place where useful, such as le-regent-dieppe."},
         "canonical_identifiers":{"type":"object","default":{}},"subject_metadata":{"type":"object","default":{}},
         "headline":{"type":"string","minLength":1,"maxLength":240},"summary":{"type":"string","minLength":1},
         "common_data":{"type":"object"},"domain_data":{"type":"object"},
         "visibility":{"type":"string","enum":["private","unlisted","public","aggregate_only"],"default":"private"},
         "user_approved":{"type":"boolean","description":"Must be true only after explicit approval in the conversation."},
         "idempotency_key":{"type":"string","minLength":8,"maxLength":200,"description":"Stable unique key for this approved review so a retry cannot create a duplicate."}
       },"required":["subject_type","subject_name","canonical_key","headline","summary","common_data","domain_data","user_approved","idempotency_key"],"additionalProperties":False},
     "securitySchemes":WRITE_SECURITY,"annotations":{"readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False}},
]

def _base(): return get_settings().public_base_url.rstrip("/")
def _text(payload: dict): return [{"type":"text","text":json.dumps(payload,default=str,separators=(",",":"))}]

def _auth_result(message: str, scope: str):
    challenge = f'Bearer resource_metadata="{_base()}/.well-known/oauth-protected-resource", error="insufficient_scope", error_description="{message}"'
    return {"content":[{"type":"text","text":f"Authentication required: {message}."}],"isError":True,
            "_meta":{"mcp/www_authenticate":[challenge]}}

def _principal(request: Request, scope: str) -> Principal:
    return principal_from_authorization(request.headers.get("authorization"), scope)

def _search(db: Session, principal: Principal, args: dict):
    query = str(args.get("query", "")).strip()
    limit = max(1, min(int(args.get("limit", 10)), 20))
    q = select(Experience, Subject).join(Subject, Experience.subject_id==Subject.id).where(
        Experience.owner_id==principal.user_id, Experience.deleted_at.is_(None), Subject.deleted_at.is_(None))
    if args.get("subject_type"): q=q.where(Experience.subject_type==args["subject_type"])
    if query:
        pattern=f"%{query}%"
        q=q.where(or_(Subject.name.ilike(pattern), Subject.canonical_key.ilike(pattern),
                      Experience.headline.ilike(pattern), Experience.summary.ilike(pattern)))
    rows=db.execute(q.order_by(Experience.created_at.desc()).limit(limit)).all()
    results=[{"id":str(exp.id),"title":f"{subject.name} — {exp.headline}",
              "url":f"{_base()}/api/v1/experiences/{exp.id}"} for exp,subject in rows]
    return {"content":_text({"results":results})}

def _fetch(db: Session, principal: Principal, args: dict):
    try: experience_id=uuid.UUID(str(args.get("id", "")))
    except ValueError: return {"content":_text({"error":"Invalid review ID"}),"isError":True}
    row=db.execute(select(Experience,Subject).join(Subject,Experience.subject_id==Subject.id).where(
        Experience.id==experience_id,Experience.owner_id==principal.user_id,Experience.deleted_at.is_(None))).first()
    if not row: return {"content":_text({"error":"Review not found"}),"isError":True}
    exp,subject=row
    payload={"id":str(exp.id),"title":f"{subject.name} — {exp.headline}",
             "text":exp.summary,"url":f"{_base()}/api/v1/experiences/{exp.id}",
             "metadata":{"subject":{"id":str(subject.id),"type":subject.subject_type,"name":subject.name,
                         "canonical_key":subject.canonical_key,"identifiers":subject.canonical_identifiers,"metadata":subject.metadata_json},
                         "headline":exp.headline,"common_data":exp.common_data,"domain_data":exp.domain_data,
                         "visibility":exp.visibility,"publication_status":exp.publication_status,
                         "created_at":exp.created_at.isoformat()}}
    return {"content":_text(payload)}

def _save(db: Session, principal: Principal, args: dict, request_id: str):
    if args.get("user_approved") is not True:
        return {"content":_text({"error":"Explicit user approval is required before saving"}),"isError":True}
    idempotency_key=str(args.get("idempotency_key", ""))
    relevant={k:v for k,v in args.items() if k!="idempotency_key"}
    p_hash=request_hash(relevant)
    existing=db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.client_id==principal.client_id,
                                                        IdempotencyRecord.key==idempotency_key))
    if existing:
        if existing.request_hash!=p_hash:
            return {"content":_text({"error":"Idempotency key was reused for different review content"}),"isError":True}
        return {"content":_text(existing.response_body),"structuredContent":existing.response_body}
    subject=db.scalar(select(Subject).where(Subject.subject_type==args["subject_type"],
                                             Subject.canonical_key==args["canonical_key"],Subject.deleted_at.is_(None)))
    if not subject:
        subject=Subject(subject_type=args["subject_type"],name=args["subject_name"],canonical_key=args["canonical_key"],
                        canonical_identifiers=args.get("canonical_identifiers",{}),metadata_json=args.get("subject_metadata",{}))
        db.add(subject);db.commit();db.refresh(subject)
    payload=ExperienceCreate(owner_id=principal.user_id,subject_id=subject.id,subject_type=args["subject_type"],schema_version="1.0",
        visibility=args.get("visibility","private"),headline=args["headline"],summary=args["summary"],
        common_data=args["common_data"],domain_data=args["domain_data"],
        provenance=Provenance(source_method="llm_conversation",source_client="chatgpt"),
        consent=Consent(user_approved=False))
    exp=create_experience(db,payload,client_id=principal.client_id,auth_subject=principal.subject,request_id=request_id)
    exp=publish_experience(db,exp,1,actor_id=principal.subject,client_id=principal.client_id,request_id=request_id)
    body={"saved":True,"experience_id":str(exp.id),"subject_id":str(subject.id),"subject_name":subject.name,
          "publication_status":exp.publication_status,"headline":exp.headline,"url":f"{_base()}/api/v1/experiences/{exp.id}"}
    db.add(IdempotencyRecord(client_id=principal.client_id,key=idempotency_key,request_hash=p_hash,
                             response_status=200,response_body=body));db.commit()
    return {"content":_text(body),"structuredContent":body}

@router.post("/mcp")
async def mcp(request: Request, db: Session = Depends(get_db)):
    body=await request.json(); request_id=body.get("id"); method=body.get("method")
    if method and method.startswith("notifications/"): return Response(status_code=202)
    if method=="initialize":
        result={"protocolVersion":PROTOCOL_VERSION,"capabilities":{"tools":{"listChanged":False}},
                "serverInfo":{"name":"TasteGraph","version":"1.1.0"},
                "instructions":"Search and retrieve the connected user's TasteGraph reviews. Save only a completed review that the user explicitly approved."}
    elif method=="ping": result={}
    elif method=="tools/list": result={"tools":TOOLS}
    elif method=="tools/call":
        params=body.get("params") or {}; name=params.get("name"); args=params.get("arguments") or {}
        required="reviews:write" if name=="save_review" else "reviews:read"
        try: principal=_principal(request,required)
        except TokenError as exc: result=_auth_result(str(exc),required)
        else:
            if name=="search": result=_search(db,principal,args)
            elif name=="fetch": result=_fetch(db,principal,args)
            elif name=="save_review": result=_save(db,principal,args,request.state.request_id)
            else: return JSONResponse({"jsonrpc":"2.0","id":request_id,"error":{"code":-32602,"message":"Unknown tool"}})
    else:
        return JSONResponse({"jsonrpc":"2.0","id":request_id,"error":{"code":-32601,"message":"Method not found"}})
    return JSONResponse({"jsonrpc":"2.0","id":request_id,"result":result})

@router.get("/mcp")
def mcp_get():
    return JSONResponse({"error":"Use Streamable HTTP POST for MCP"},status_code=405,
                        headers={"Allow":"POST"})
