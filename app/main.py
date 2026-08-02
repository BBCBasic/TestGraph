from __future__ import annotations
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from app.api.routes import router
from app.core.config import get_settings

settings=get_settings()
app=FastAPI(title="TasteGraph",version="1.0.0",description="AI-native structured experience storage and personalised review interpretation.")
app.add_middleware(TrustedHostMiddleware,allowed_hosts=settings.allowed_hosts or ["*"])
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
    return """<!doctype html><html><head><title>TasteGraph</title><style>body{font-family:system-ui;max-width:860px;margin:40px auto;padding:0 20px}code{background:#eee;padding:2px 5px}</style></head><body><h1>TasteGraph</h1><p>AI-native structured experience storage.</p><ul><li><a href='/docs'>API documentation</a></li><li><a href='/schemas'>Schema registry</a></li><li><a href='/.well-known/review-service.json'>LLM discovery record</a></li><li><a href='/health/ready'>Health</a></li></ul></body></html>"""

app.include_router(router)
