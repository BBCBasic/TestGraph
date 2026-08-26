from __future__ import annotations
import base64, hashlib, hmac, html, secrets, uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.security import issue_access_token
from app.db.session import get_db
from app.models.entities import CapabilityCredential, OAuthAuthorizationCode, OAuthClient, OAuthRefreshToken, User
from app.services.connection_audit import record_oauth_connection
from app.api.admin_connections import router as admin_connections_router
from app.api.account import _current_user
router=APIRouter(); ALLOWED_SCOPES={"reviews:read","reviews:write"}
def _now(): return datetime.now(timezone.utc)
def _hash(value:str)->str: return hashlib.sha256(value.encode()).hexdigest()
def _resource(path:str="/mcp")->str: return f"{get_settings().public_base_url.rstrip('/')}{path}"
def _allowed_resources()->set[str]: return {_resource("/mcp"),_resource("/mcp-v2")}
def _issuer()->str: return get_settings().public_base_url.rstrip("/")
def _aware(value:datetime)->datetime: return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
def _validate_scope(scope:str)->str:
 requested=set(scope.split()) if scope else {"reviews:read"}
 if not requested or not requested.issubset(ALLOWED_SCOPES): raise HTTPException(400,"Unsupported OAuth scope")
 return " ".join(sorted(requested))
def _validate_redirect_uri(uri:str):
 parsed=urlparse(uri)
 if parsed.scheme=="https" and parsed.netloc:return
 if parsed.scheme=="http" and parsed.hostname in {"127.0.0.1","localhost"}:return
 raise HTTPException(400,"redirect_uri must use HTTPS (localhost HTTP is allowed for development)")
def _client(db:Session,client_id:str,redirect_uri:str)->OAuthClient:
 obj=db.get(OAuthClient,client_id)
 if not obj or redirect_uri not in obj.redirect_uris:raise HTTPException(400,"Unknown OAuth client or redirect_uri")
 return obj
def _validate_authorize(db:Session,*,response_type:str,client_id:str,redirect_uri:str,code_challenge:str,code_challenge_method:str,resource:str,scope:str):
 if response_type!="code":raise HTTPException(400,"Only response_type=code is supported")
 _client(db,client_id,redirect_uri)
 if code_challenge_method!="S256" or not code_challenge:raise HTTPException(400,"PKCE S256 is required")
 if resource not in _allowed_resources():raise HTTPException(400,"Invalid OAuth resource")
 return _validate_scope(scope)
def _owner(db:Session)->User:
 raw=get_settings().oauth_owner_user_id
 if not raw:raise HTTPException(503,"OAUTH_OWNER_USER_ID is not configured")
 try:user_id=uuid.UUID(raw)
 except ValueError as exc:raise HTTPException(503,"OAUTH_OWNER_USER_ID is invalid") from exc
 user=db.get(User,user_id)
 if not user or user.deleted_at:raise HTTPException(503,"Configured TasteGraph owner does not exist")
 return user
def _extract_capability_key(value:str)->str:
 value=value.strip()
 if "/c/" in value:value=value.split("/c/",1)[1]
 return value.split("?",1)[0].split("#",1)[0].split("/",1)[0].strip()
def _user_from_capability(db:Session,supplied:str)->User|None:
 key=_extract_capability_key(supplied)
 if not key.startswith("tg_"):return None
 cred=db.scalar(select(CapabilityCredential).where(CapabilityCredential.key_hash==_hash(key),CapabilityCredential.revoked_at.is_(None)))
 if not cred:return None
 user=db.get(User,cred.user_id)
 return user if user and not user.deleted_at else None
def _connecting_user(db:Session,supplied:str)->User|None:
 user=_user_from_capability(db,supplied)
 if user:return user
 legacy=get_settings().oauth_connection_code
 return _owner(db) if legacy and hmac.compare_digest(supplied,legacy) else None
def _resource_metadata(resource:str)->dict:return {"resource":resource,"authorization_servers":[_issuer()],"scopes_supported":sorted(ALLOWED_SCOPES),"resource_documentation":f"{_issuer()}/integrations"}
@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata():return _resource_metadata(_resource("/mcp"))
@router.get("/.well-known/oauth-protected-resource/mcp-v2")
def protected_resource_metadata_v2():return _resource_metadata(_resource("/mcp-v2"))
@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
 issuer=_issuer();return {"issuer":issuer,"authorization_endpoint":f"{issuer}/oauth/authorize","token_endpoint":f"{issuer}/oauth/token","registration_endpoint":f"{issuer}/oauth/register","response_types_supported":["code"],"grant_types_supported":["authorization_code","refresh_token"],"code_challenge_methods_supported":["S256"],"token_endpoint_auth_methods_supported":["none"],"scopes_supported":sorted(ALLOWED_SCOPES)}
@router.post("/oauth/register",status_code=201)
async def register_client(request:Request,db:Session=Depends(get_db)):
 try:body=await request.json()
 except Exception as exc:raise HTTPException(400,"OAuth client registration body must be JSON") from exc
 if not isinstance(body,dict):raise HTTPException(400,"OAuth client registration body must be a JSON object")
 redirect_uris=body.get("redirect_uris") or []
 if not isinstance(redirect_uris,list) or not redirect_uris or len(redirect_uris)>10:raise HTTPException(400,"redirect_uris is required")
 if not all(isinstance(uri,str) and uri for uri in redirect_uris):raise HTTPException(400,"redirect_uris must contain non-empty strings")
 for uri in redirect_uris:_validate_redirect_uri(uri)
 method=body.get("token_endpoint_auth_method","none")
 if method!="none":raise HTTPException(400,"Only public OAuth clients are supported")
 grant_types=body.get("grant_types",["authorization_code","refresh_token"])
 if not isinstance(grant_types,list) or not grant_types or "authorization_code" not in grant_types or any(g not in {"authorization_code","refresh_token"} for g in grant_types):raise HTTPException(400,"Unsupported OAuth grant type")
 response_types=body.get("response_types",["code"])
 if not isinstance(response_types,list) or response_types!=["code"]:raise HTTPException(400,'Only response_types=["code"] is supported')
 requested_scope=body.get("scope");registered_scope=_validate_scope(str(requested_scope)) if requested_scope else None
 client_name=body.get("client_name","MCP client")
 if not isinstance(client_name,str) or not client_name.strip():client_name="MCP client"
 client=OAuthClient(client_id=f"tg_{secrets.token_urlsafe(24)}",redirect_uris=redirect_uris,client_name=client_name.strip(),token_endpoint_auth_method="none")
 db.add(client);db.commit();db.refresh(client)
 result={"client_id":client.client_id,"client_id_issued_at":int(client.created_at.timestamp()),"redirect_uris":redirect_uris,"client_name":client.client_name,"token_endpoint_auth_method":"none","grant_types":grant_types,"response_types":response_types}
 if registered_scope:result["scope"]=registered_scope
 if isinstance(body.get("application_type"),str):result["application_type"]=body["application_type"]
 return result
@router.get("/oauth/authorize",response_class=HTMLResponse)
def authorize_page(request:Request,response_type:str,client_id:str,redirect_uri:str,state:str,code_challenge:str,code_challenge_method:str="S256",resource:str="",scope:str="reviews:read reviews:write",use_key:bool=False,db:Session=Depends(get_db)):
 scope=_validate_authorize(db,response_type=response_type,client_id=client_id,redirect_uri=redirect_uri,code_challenge=code_challenge,code_challenge_method=code_challenge_method,resource=resource,scope=scope)
 hidden={"response_type":response_type,"client_id":client_id,"redirect_uri":redirect_uri,"state":state,"code_challenge":code_challenge,"code_challenge_method":code_challenge_method,"resource":resource,"scope":scope}
 fields="".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k,v in hidden.items())
 if not use_key:
  try:user=_current_user(request,db)
  except HTTPException:
   continuation="/oauth/authorize?"+urlencode({**hidden})
   return RedirectResponse("/account?"+urlencode({"continue":continuation}),status_code=303)
  client=_client(db,client_id,redirect_uri)
  client_name=html.escape(client.client_name or "AI client")
  return HTMLResponse(f'''<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>Connect TestGraph</title><style>body{{font:16px system-ui;max-width:700px;margin:50px auto;padding:0 20px;line-height:1.5;color:#172033}}button{{font:inherit;padding:11px 16px;background:#172033;color:white;border:0;border-radius:8px;cursor:pointer}}.note{{background:#f5f7fa;padding:14px;border-radius:8px}}</style></head><body><p><a href="/account">Your TestGraph account</a></p><h1>Connect {client_name} to TestGraph?</h1><p>This will connect the AI client to your existing TestGraph identity. The AI receives scoped OAuth tokens, not your Google credentials.</p><div class="note">Requested access: <strong>{html.escape(scope)}</strong></div><form method="post" action="/oauth/authorize">{fields}<input type="hidden" name="account_session" value="1"><p><button type="submit">Connect this AI</button></p></form><p><a href="/account">Cancel and return to account</a></p></body></html>''')
 return HTMLResponse(f'''<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>Connect TestGraph</title><style>body{{font:16px system-ui;max-width:700px;margin:50px auto;padding:0 20px;line-height:1.5;color:#172033}}label{{display:block;margin:18px 0}}input{{display:block;width:100%;max-width:620px;padding:10px;margin-top:7px}}button{{font:inherit;padding:11px 16px;background:#172033;color:white;border:0;border-radius:8px;cursor:pointer}}</style></head><body><p><a href="/account">TestGraph account</a></p><h1>Connect with a capability key</h1><p>This is the original TestGraph connection method. Google is not required.</p><form method="post" action="/oauth/authorize">{fields}<label>Your private TestGraph capability URL or key<input name="connection_secret" type="password" autocomplete="off" required></label><button type="submit">Connect TestGraph</button></form><p><small>The capability is used only by TestGraph to identify your account. The AI client receives OAuth tokens, not your capability key.</small></p></body></html>''')
@router.post("/oauth/authorize")
async def authorize_submit(request:Request,db:Session=Depends(get_db)):
 form=await request.form();values={k:str(form.get(k,"")) for k in ("response_type","client_id","redirect_uri","state","code_challenge","code_challenge_method","resource","scope")}
 scope=_validate_authorize(db,response_type=values["response_type"],client_id=values["client_id"],redirect_uri=values["redirect_uri"],code_challenge=values["code_challenge"],code_challenge_method=values["code_challenge_method"],resource=values["resource"],scope=values["scope"])
 if str(form.get("account_session",""))=="1":
  try:user=_current_user(request,db)
  except HTTPException:return HTMLResponse("<h1>Connection refused</h1><p>Your TestGraph account session has expired. Return to /account and sign in again.</p>",status_code=401)
 else:
  supplied=str(form.get("connection_secret",""));user=_connecting_user(db,supplied)
 if not user:return HTMLResponse("<h1>Connection refused</h1><p>The TestGraph capability URL/key was not recognised.</p>",status_code=401)
 raw_code=secrets.token_urlsafe(48);db.add(OAuthAuthorizationCode(code_hash=_hash(raw_code),client_id=values["client_id"],user_id=user.id,redirect_uri=values["redirect_uri"],code_challenge=values["code_challenge"],scope=scope,resource=values["resource"],expires_at=_now()+timedelta(minutes=10)));db.commit()
 return RedirectResponse(f'{values["redirect_uri"]}?{urlencode({"code":raw_code,"state":values["state"]})}',status_code=303)
def _new_refresh(db:Session,*,client_id:str,user_id:uuid.UUID,scope:str,resource:str)->str:
 raw=secrets.token_urlsafe(48);db.add(OAuthRefreshToken(token_hash=_hash(raw),client_id=client_id,user_id=user_id,scope=scope,resource=resource,expires_at=_now()+timedelta(days=get_settings().oauth_refresh_token_days)));return raw
@router.post("/oauth/token")
async def token(request:Request,db:Session=Depends(get_db)):
 form=await request.form();grant_type=str(form.get("grant_type",""));client_id=str(form.get("client_id",""));resource=str(form.get("resource",""))
 if resource not in _allowed_resources():return JSONResponse({"error":"invalid_target"},status_code=400)
 if not db.get(OAuthClient,client_id):return JSONResponse({"error":"invalid_client"},status_code=400)
 fresh_connection=False
 if grant_type=="authorization_code":
  code=db.scalar(select(OAuthAuthorizationCode).where(OAuthAuthorizationCode.code_hash==_hash(str(form.get("code","")))))
  if not code or code.used_at or _aware(code.expires_at)<=_now():return JSONResponse({"error":"invalid_grant"},status_code=400)
  if code.client_id!=client_id or code.redirect_uri!=str(form.get("redirect_uri","")) or code.resource!=resource:return JSONResponse({"error":"invalid_grant"},status_code=400)
  verifier=str(form.get("code_verifier",""));challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
  if not hmac.compare_digest(challenge,code.code_challenge):return JSONResponse({"error":"invalid_grant"},status_code=400)
  code.used_at=_now();user_id=code.user_id;scope=code.scope;fresh_connection=True
 elif grant_type=="refresh_token":
  old=db.scalar(select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash==_hash(str(form.get("refresh_token","")))))
  if not old or old.revoked_at or _aware(old.expires_at)<=_now() or old.client_id!=client_id or old.resource!=resource:return JSONResponse({"error":"invalid_grant"},status_code=400)
  old.revoked_at=_now();user_id=old.user_id;scope=old.scope
 else:return JSONResponse({"error":"unsupported_grant_type"},status_code=400)
 access_token,expires_in=issue_access_token(user_id=user_id,client_id=client_id,scope=scope,resource=resource);refresh_token=_new_refresh(db,client_id=client_id,user_id=user_id,scope=scope,resource=resource)
 if fresh_connection:record_oauth_connection(db,user_id=user_id,client_id=client_id,resource=resource,request_id=request.headers.get("X-Request-ID"))
 db.commit();return {"access_token":access_token,"token_type":"Bearer","expires_in":expires_in,"refresh_token":refresh_token,"scope":scope}

router.include_router(admin_connections_router)
