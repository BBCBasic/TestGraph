from __future__ import annotations
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from app.api.routes import router
from app.api.oauth import router as oauth_router
from app.api.mcp import router as mcp_router
from app.api.capability import router as capability_router
from app.api.integrations import router as integrations_router
from app.api.actions import router as actions_router
from app.core.config import get_settings

settings=get_settings()
REVIEWS_HTML = Path(__file__).parent / "static" / "reviews.html"
app=FastAPI(title="TasteGraph",version="1.3.0",description="AI-native structured experience storage and personalised review interpretation.")

# Railway readiness probes use a fixed internal hostname, while the public domain
# is injected at runtime. Add both automatically so deployment does not depend on
# duplicating Railway-managed hostnames in ALLOWED_HOSTS.
railway_public_domain=os.getenv("RAILWAY_PUBLIC_DOMAIN")
configured_public_domain=urlsplit(settings.public_base_url).hostname
trusted_hosts=list(dict.fromkeys(
    host for host in [
        *settings.allowed_hosts,
        "healthcheck.railway.app",
        railway_public_domain,
        configured_public_domain,
    ] if host
))
app.add_middleware(TrustedHostMiddleware,allowed_hosts=trusted_hosts)
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins,allow_methods=["*"],allow_headers=["*"],allow_credentials=False)

@app.middleware("http")
async def request_context(request:Request,call_next):
    request.state.request_id=request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
    content_length=request.headers.get("content-length")
    if content_length and int(content_length)>settings.max_request_bytes:
        return JSONResponse(status_code=413,content={"error":{"code":"request_too_large","message":"Request body exceeds the configured limit.","request_id":request.state.request_id}})
    try:
        response=await call_next(request)
    except Exception:
        raise
    response.headers["X-Request-ID"]=request.state.request_id
    return response

@app.exception_handler(ValueError)
async def value_error_handler(request:Request,exc:ValueError):
    return JSONResponse(status_code=400,content={"error":{"code":"invalid_request","message":str(exc),"details":[],"request_id":request.state.request_id}})

@app.get("/",response_class=HTMLResponse)
def home():
    return """<!doctype html><html><head><title>TasteGraph</title><style>body{font-family:system-ui;max-width:860px;margin:40px auto;padding:0 20px}code{background:#eee;padding:2px 5px}</style></head><body><h1>TasteGraph</h1><p>AI-native structured experience storage.</p><ul><li><a href='/integrations'>MCP / AI integration record</a></li><li><a href='/actions/openapi.json'>ChatGPT Actions schema</a></li><li><a href='/capability/new'>Create a private TasteGraph capability URL</a></li><li><a href='/reviews'>Browse reviews</a></li><li><a href='/docs'>API documentation</a></li><li><a href='/schemas'>Schema registry</a></li><li><a href='/.well-known/review-service.json'>AI discovery record</a></li><li><a href='/.well-known/oauth-protected-resource'>OAuth protected-resource metadata</a></li><li><a href='/health/ready'>Health</a></li></ul><p>Hosted-AI integrations: remote MCP at <code>/mcp</code> with OAuth, plus a private ChatGPT Actions API under <code>/actions</code> using a TasteGraph capability key as Bearer authentication.</p></body></html>"""

@app.get("/reviews", response_class=FileResponse, include_in_schema=False)
def review_browser():
    return FileResponse(REVIEWS_HTML)

app.include_router(router)
app.include_router(integrations_router)
app.include_router(capability_router)
app.include_router(oauth_router)
app.include_router(mcp_router)
app.include_router(actions_router)
