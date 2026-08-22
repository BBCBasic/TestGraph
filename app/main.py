from __future__ import annotations
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from app.api.routes import router
from app.api.v2 import router as v2_router
from app.api.oauth import router as oauth_router
from app.api.mcp import router as mcp_router
from app.api import mcp_v2 as mcp_v2_module
from app.api.mcp_v2 import router as mcp_v2_router
from app.api.capability import router as capability_router
from app.api.integrations import router as integrations_router
from app.api.actions import router as actions_router
from app.api.actions_v2 import router as actions_v2_router
from app.api.development import router as development_router
from app.api.uci_reviews import router as uci_reviews_router
from app.core.config import get_settings
from app.services.mcp_v2_policy import apply_chain_ingest_policy
from app.services.mcp_v2_guidance_policy import (
    apply_guidance_tool_policy,
    install_get_induction_middleware,
)

settings=get_settings()
mcp_v2_module.SERVER_VERSION = "3.18.0-alpha"
apply_chain_ingest_policy(mcp_v2_module.TOOLS)
apply_guidance_tool_policy(mcp_v2_module.TOOLS)
REVIEWS_HTML = Path(__file__).parent / "static" / "reviews.html"
app=FastAPI(title="TasteGraph",version="3.0.0-alpha",description="Standardised review storage with stable subject-type IDs, aliases and flexible relationships.")
install_get_induction_middleware(app, mcp_v2_module)

railway_public_domain=os.getenv("RAILWAY_PUBLIC_DOMAIN")
configured_public_domain=urlsplit(settings.public_base_url).hostname
trusted_hosts=list(dict.fromkeys(host for host in [*settings.allowed_hosts,"healthcheck.railway.app",railway_public_domain,configured_public_domain] if host))
app.add_middleware(TrustedHostMiddleware,allowed_hosts=trusted_hosts)
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins,allow_methods=["*"],allow_headers=["*"],allow_credentials=False)

@app.middleware("http")
async def request_context(request:Request,call_next):
    request.state.request_id=request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
    content_length=request.headers.get("content-length")
    if content_length and int(content_length)>settings.max_request_bytes:
        return JSONResponse(status_code=413,content={"error":{"code":"request_too_large","message":"Request body exceeds the configured limit.","request_id":request.state.request_id}})
    response=await call_next(request);response.headers["X-Request-ID"]=request.state.request_id;return response

@app.exception_handler(ValueError)
async def value_error_handler(request:Request,exc:ValueError):
    return JSONResponse(status_code=400,content={"error":{"code":"invalid_request","message":str(exc),"details":[],"request_id":request.state.request_id}})

@app.get("/",response_class=HTMLResponse)
def home():
    reset_control = ""
    if settings.enable_development_reset:
        reset_control = """<section class="reset"><h2>Development reset</h2><p>Remove reviews, subjects and discovered vocabulary while preserving users, schemas, OAuth connections and API/capability credentials.</p><form method="post" action="/development/reset" onsubmit="return confirm('Permanently reset TasteGraph to basics? OAuth connections and API keys will be preserved.');"><button type="submit">Reset database to basics</button></form></section>"""
    return f"""<!doctype html><html><head><title>TasteGraph</title><style>body{{font-family:system-ui;max-width:860px;margin:40px auto;padding:0 20px}}code{{background:#eee;padding:2px 5px}}.reset{{margin-top:32px;padding:20px;border:1px solid #e2bcbc;border-radius:12px;background:#fff8f8}}.reset h2{{margin-top:0}}.reset button{{padding:11px 16px;background:#a52626;color:white;border:0;border-radius:9px;font-weight:700;cursor:pointer}}</style></head><body><h1>TasteGraph</h1><p>Standardised review storage with flexible input.</p><ul><li><a href="/api/v2/vocabulary">Standard vocabulary</a></li><li><a href="/mcp-v2">MCP endpoint</a></li><li><a href="/docs">API documentation</a></li><li><a href="/capability/new">Create a private TasteGraph capability URL</a></li><li><a href="/reviews">Legacy review browser</a></li><li><a href="/health/ready">Health</a></li></ul><p>Reviews store stable subject-type IDs. Aliases standardise varied language; editable relationships support broad searches without becoming storage paths.</p>{reset_control}</body></html>"""

@app.get("/reviews", response_class=FileResponse, include_in_schema=False)
def review_browser(): return FileResponse(REVIEWS_HTML)

@app.get("/tools/recipe-reviews", include_in_schema=False)
def recipe_review_browser(): return RedirectResponse(url="/tools/recipe-reviews/view", status_code=307)

app.include_router(router);app.include_router(v2_router);app.include_router(integrations_router);app.include_router(capability_router)
app.include_router(oauth_router);app.include_router(mcp_router);app.include_router(mcp_v2_router);app.include_router(actions_router)
app.include_router(actions_v2_router);app.include_router(development_router);app.include_router(uci_reviews_router)
