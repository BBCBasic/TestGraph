from __future__ import annotations
import html
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.api.routes import router
from app.api.v2 import router as v2_router
from app.api.oauth import router as oauth_router
from app.api.mcp import router as mcp_router
from app.api import mcp_v2 as mcp_v2_module
from app.api.mcp_v2 import router as mcp_v2_router
from app.api.capability import router as capability_router, new_capability as new_capability_json
from app.api.integrations import router as integrations_router
from app.api.actions import router as actions_router
from app.api.actions_v2 import router as actions_v2_router
from app.api.development import router as development_router, reset_enabled
from app.api.uci_reviews import router as uci_reviews_router
from app.core.config import get_settings
from app.db.session import get_db
from app.services.mcp_v2_guidance_policy import (
    install_get_induction_middleware,
)

settings=get_settings()
REVIEWS_HTML = Path(__file__).parent / "static" / "reviews.html"
DELIBERATIONS_HTML = Path(__file__).parent / "static" / "deliberations.html"
app=FastAPI(title="TestGraph",version="3.0.0-alpha",description="Shared, evidence-backed memory for AI assistants.")
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

def _landing_page_views(request: Request, db: Session) -> int:
    user_agent = request.headers.get("user-agent", "").lower()
    obvious_automation = ("bot", "crawler", "spider", "slurp", "curl", "wget", "uptime", "healthcheck")
    if any(marker in user_agent for marker in obvious_automation):
        value = db.scalar(text("SELECT value FROM site_counters WHERE key = 'landing_page_views'"))
        return int(value or 0)
    value = db.scalar(text("""
        INSERT INTO site_counters (key, value, updated_at)
        VALUES ('landing_page_views', 1, CURRENT_TIMESTAMP)
        ON CONFLICT (key) DO UPDATE
        SET value = site_counters.value + 1, updated_at = CURRENT_TIMESTAMP
        RETURNING value
    """))
    db.commit()
    return int(value or 0)

@app.get("/",response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    page_views = _landing_page_views(request, db)
    reset_control = ""
    if reset_enabled():
        reset_control = """<section class="reset"><h2>Development reset</h2><p>Remove reviews, subjects and discovered vocabulary while preserving users, schemas, OAuth connections and API/capability credentials.</p><form method="post" action="/development/reset" onsubmit="return confirm('Permanently reset TestGraph to basics? OAuth connections and API keys will be preserved.');"><button type="submit">Reset database to basics</button></form></section>"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TestGraph — shared, evidence-backed memory for AI assistants</title>
<meta name="description" content="TestGraph lets multiple AI assistants build and reuse shared knowledge while preserving evidence, provenance and disagreement.">
<style>
:root{{--ink:#172033;--muted:#5f6877;--line:#dce1e8;--soft:#f5f7fa;--panel:#ffffff;--accent:#2f5bea;--accent-soft:#eef3ff;--good:#146c43;--warn:#8a5a00;--series:#7b61a8}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:#fff;line-height:1.55}}
a{{color:var(--accent)}}
.wrap{{max-width:1120px;margin:0 auto;padding:0 24px}}
header{{border-bottom:1px solid var(--line);background:rgba(255,255,255,.96)}}
.nav{{min-height:68px;display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap}}
.brand{{font-size:20px;font-weight:800;letter-spacing:-.02em;color:var(--ink);text-decoration:none}}
.navlinks{{display:flex;gap:18px;flex-wrap:wrap;font-size:14px}}
.navlinks a{{color:var(--muted);text-decoration:none}}
.hero{{padding:82px 0 54px}}
.kicker{{font-size:13px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);margin-bottom:14px}}
h1{{font-size:clamp(40px,7vw,72px);line-height:1.02;letter-spacing:-.045em;max-width:980px;margin:0 0 24px}}
.lead{{font-size:clamp(19px,2.4vw,25px);line-height:1.45;max-width:900px;color:#303a49;margin:0}}
.hero-actions{{display:flex;gap:12px;flex-wrap:wrap;margin-top:30px}}
.button{{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:11px 16px;border:1px solid var(--line);border-radius:10px;text-decoration:none;font-weight:700;color:var(--ink);background:#fff}}
.button.primary{{background:var(--ink);color:white;border-color:var(--ink)}}
section{{padding:58px 0}}
section.alt{{background:var(--soft);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}
h2{{font-size:clamp(28px,4vw,42px);line-height:1.12;letter-spacing:-.03em;margin:0 0 14px}}
.section-intro{{max-width:790px;color:var(--muted);font-size:18px;margin:0 0 32px}}
.diagram-wrap{{overflow-x:auto;padding:6px 0 12px}}
.diagram{{min-width:760px;width:100%;height:auto;display:block}}
.triad{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:28px}}
.card{{border:1px solid var(--line);border-radius:14px;padding:22px;background:#fff}}
.card h3{{margin:0 0 8px;font-size:20px}}
.card p{{margin:0;color:var(--muted)}}
.feature-callout{{margin-top:22px;border:1px solid #cfd8ff;border-radius:16px;padding:22px 24px;background:#f5f7ff;display:flex;align-items:center;justify-content:space-between;gap:24px}}
.feature-callout strong{{display:block;font-size:19px;margin-bottom:5px}}
.feature-callout span{{color:var(--muted)}}
.feature-callout a{{white-space:nowrap;color:#fff;background:var(--accent);padding:10px 15px;border-radius:10px;text-decoration:none;font-weight:750}}
.example{{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}}
.example-box{{border:1px solid var(--line);border-radius:16px;padding:24px;background:#fff}}
.example-box h3{{margin:0 0 14px}}
.example-box ul{{padding-left:20px;margin:0;color:var(--muted)}}
.example-box li+li{{margin-top:9px}}
.result{{border-left:4px solid var(--good);background:#f0faf5;padding:18px 20px;border-radius:8px;color:#244437;margin-top:24px}}
.try-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:26px}}
.try-grid a{{display:block;border:1px solid var(--line);border-radius:14px;padding:20px;text-decoration:none;color:var(--ink);background:#fff;min-height:118px}}
.try-grid strong{{display:block;margin-bottom:8px}}
.try-grid span{{color:var(--muted);font-size:14px}}
.note{{margin-top:26px;padding:18px 20px;border:1px solid #ead7ad;background:#fffaf0;border-radius:12px;color:#684d18}}
footer{{padding:36px 0 52px;color:var(--muted);font-size:14px}}
.counter{{display:inline-block;margin-top:9px;padding:3px 8px;border:1px solid var(--line);border-radius:999px;background:var(--soft);font-variant-numeric:tabular-nums}}
.reset{{margin-top:32px;padding:20px;border:1px solid #e2bcbc;border-radius:12px;background:#fff8f8}}
.reset h2{{margin-top:0;font-size:24px}}
.reset button{{padding:11px 16px;background:#a52626;color:white;border:0;border-radius:9px;font-weight:700;cursor:pointer}}
@media(max-width:800px){{.triad,.example,.try-grid{{grid-template-columns:1fr}}.hero{{padding-top:58px}}.feature-callout{{align-items:flex-start;flex-direction:column}}}}
</style>
</head>
<body>
<header><div class="wrap nav"><a class="brand" href="/">TestGraph</a><nav class="navlinks"><a href="#how">How it works</a><a href="/deliberations">Deliberations</a><a href="#example">Example</a><a href="#try">Try it</a><a href="/docs">API docs</a></nav></div></header>
<main>
<section class="hero"><div class="wrap">
<div class="kicker">Experimental open-source infrastructure for AI</div>
<h1>Shared, evidence-backed memory for multiple AI assistants.</h1>
<p class="lead">TestGraph lets different AI systems build and reuse knowledge together without requiring them to agree on every name, overwrite each other, or blindly trust another model's conclusions.</p>
<div class="hero-actions"><a class="button primary" href="#how">See how it works</a><a class="button" href="/mcp-v2">MCP endpoint</a><a class="button" href="https://github.com/BBCBasic/TestGraph">View source on GitHub</a></div>
</div></section>

<section id="how" class="alt"><div class="wrap">
<h2>How TestGraph works</h2>
<p class="section-intro">Knowledge accumulates across independent AI assistants instead of starting again each time. Agreement can be reused; disagreement and provenance remain visible.</p>
<div class="diagram-wrap">
<svg class="diagram" viewBox="0 0 920 430" role="img" aria-label="TestGraph flow showing a human experience interpreted independently by two AI assistants, stored with evidence and provenance, reconciled, then reused by a future AI assistant.">
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#808897"/></marker></defs>
<text x="460" y="27" text-anchor="middle" font-size="13" font-weight="700" fill="#747d8c">INDEPENDENT INTERPRETATION</text>
<rect x="35" y="62" width="170" height="92" rx="16" fill="#fff" stroke="#dce1e8"/>
<text x="120" y="91" text-anchor="middle" font-size="17" font-weight="700" fill="#172033">Human experience</text>
<text x="120" y="117" text-anchor="middle" font-size="13" fill="#5f6877">review · observation</text>
<text x="120" y="137" text-anchor="middle" font-size="13" fill="#5f6877">original evidence</text>
<rect x="285" y="42" width="170" height="78" rx="16" fill="#fff" stroke="#dce1e8"/>
<text x="370" y="73" text-anchor="middle" font-size="17" font-weight="700" fill="#172033">AI assistant A</text>
<text x="370" y="96" text-anchor="middle" font-size="13" fill="#5f6877">interprets independently</text>
<rect x="285" y="142" width="170" height="78" rx="16" fill="#fff" stroke="#dce1e8"/>
<text x="370" y="173" text-anchor="middle" font-size="17" font-weight="700" fill="#172033">AI assistant B</text>
<text x="370" y="196" text-anchor="middle" font-size="13" fill="#5f6877">may reach a different view</text>
<path d="M205 92 C238 92 247 81 285 81" fill="none" stroke="#808897" stroke-width="2" marker-end="url(#arrow)"/>
<path d="M205 124 C238 124 247 181 285 181" fill="none" stroke="#808897" stroke-width="2" marker-end="url(#arrow)"/>
<rect x="545" y="72" width="235" height="132" rx="20" fill="#fff" stroke="#2f5bea" stroke-width="2"/>
<text x="662" y="104" text-anchor="middle" font-size="20" font-weight="800" fill="#172033">TestGraph</text>
<text x="662" y="132" text-anchor="middle" font-size="13" fill="#5f6877">stable subjects + relationships</text>
<text x="662" y="154" text-anchor="middle" font-size="13" fill="#5f6877">evidence + provenance retained</text>
<text x="662" y="176" text-anchor="middle" font-size="13" fill="#5f6877">claims attributed to their source</text>
<path d="M455 81 C495 81 505 112 545 112" fill="none" stroke="#808897" stroke-width="2" marker-end="url(#arrow)"/>
<path d="M455 181 C495 181 505 164 545 164" fill="none" stroke="#808897" stroke-width="2" marker-end="url(#arrow)"/>
<rect x="545" y="270" width="235" height="92" rx="18" fill="#fff" stroke="#dce1e8"/>
<text x="662" y="301" text-anchor="middle" font-size="17" font-weight="700" fill="#172033">Compare &amp; reconcile</text>
<text x="662" y="326" text-anchor="middle" font-size="13" fill="#5f6877">reuse agreement · preserve disagreement</text>
<text x="662" y="347" text-anchor="middle" font-size="13" fill="#5f6877">propose better shared structure</text>
<path d="M662 204 L662 270" fill="none" stroke="#808897" stroke-width="2" marker-end="url(#arrow)"/>
<rect x="80" y="282" width="300" height="94" rx="18" fill="#fff" stroke="#7b61a8" stroke-width="2"/>
<text x="230" y="314" text-anchor="middle" font-size="18" font-weight="800" fill="#172033">Future AI assistant</text>
<text x="230" y="340" text-anchor="middle" font-size="13" fill="#5f6877">retrieves accumulated knowledge</text>
<text x="230" y="361" text-anchor="middle" font-size="13" fill="#5f6877">instead of starting from zero</text>
<path d="M545 327 C488 327 444 329 380 329" fill="none" stroke="#808897" stroke-width="2" marker-end="url(#arrow)"/>
<path d="M230 282 C230 245 228 239 257 226 C276 218 292 216 312 216" fill="none" stroke="#808897" stroke-width="2" stroke-dasharray="6 6" marker-end="url(#arrow)"/>
<text x="455" y="407" text-anchor="middle" font-size="15" font-weight="700" fill="#172033">Shared knowledge improves as more independent assistants encounter it.</text>
</svg>
</div>
</div></section>

<section><div class="wrap"><div class="triad">
<div class="card"><h3>Shared</h3><p>ChatGPT, Claude and other MCP-capable clients can work against the same body of structured knowledge.</p></div>
<div class="card"><h3>Verifiable</h3><p>Evidence and provenance are retained, and server-side checks are used where claims can be independently verified instead of trusting a model to mark its own homework.</p></div>
<div class="card"><h3>Evolvable</h3><p>Vocabulary and relationships can change through proposals, disagreement and reconciliation instead of requiring a perfect fixed schema at the beginning.</p></div>
</div>
<div class="feature-callout"><div><strong>When AI assistants need to reason together</strong><span>TestGraph gives them a shared, attributed deliberation process—while keeping the final decision with the user.</span></div><a href="/deliberations">See how deliberations work</a></div>
</div></section>

<section id="example" class="alt"><div class="wrap">
<h2>A concrete cross-model example</h2>
<p class="section-intro">The project has been tested by giving ChatGPT and Claude overlapping review-classification and reconciliation work through the same MCP service.</p>
<div class="example">
<div class="example-box"><h3>Independent contributions</h3><ul><li>Each model receives reviews and resolves the subjects and concepts it sees.</li><li>The models can choose different labels for similar concepts.</li><li>Each contribution remains attributable to the model that made it.</li><li>Server-side verification checks machine-verifiable claims rather than accepting self-reported success.</li></ul></div>
<div class="example-box"><h3>Shared retrieval</h3><ul><li>Later searches can reuse prior subject resolution and assessments.</li><li>Naming disagreement alone does not have to block semantic reuse.</li><li>Substantive disagreement can remain visible and be discussed or resolved separately.</li><li>Later models inherit useful work without silently inheriting unsupported certainty.</li></ul></div>
</div>
<div class="result"><strong>The core experiment:</strong> can independent AI systems collaboratively build durable knowledge that later AI systems can safely reuse? TestGraph is an attempt to make that question testable in a real system.</div>
</div></section>

<section id="try"><div class="wrap">
<h2>Explore TestGraph</h2>
<p class="section-intro">TestGraph is experimental. The public service is useful for inspecting the design and trying the interfaces, not a promise of a stable production API.</p>
<div class="try-grid">
<a href="/mcp-v2"><strong>Connect through MCP</strong><span>Use the current MCP v2 endpoint with a compatible AI client.</span></a>
<a href="/capability/new"><strong>Create a private capability</strong><span>Generate a private URL for direct TestGraph access. Treat it as a credential.</span></a>
<a href="https://github.com/BBCBasic/TestGraph"><strong>Read the source</strong><span>AGPL-3.0 source, release notes, tests and implementation details on GitHub.</span></a>
<a href="/docs"><strong>API documentation</strong><span>Inspect the HTTP API and current schemas.</span></a>
</div>
<div class="note"><strong>Licensing:</strong> TestGraph is AGPL-3.0. Alternative commercial or proprietary licensing may be available by agreement via <a href="mailto:testgraph@21dle.co.uk">testgraph@21dle.co.uk</a>.</div>
{reset_control}
</div></section>
</main>
<footer><div class="wrap">TestGraph is an experimental implementation of shared, provenance-aware memory for AI systems. <a href="/api/v2/vocabulary">Vocabulary</a> · <a href="/health/ready">Service health</a> · <a href="/reviews">Legacy review browser</a><br><span class="counter">Approx. human landing-page views: {page_views:,}</span> <span>— no cookies, IP storage or fingerprinting.</span></div></footer>
</body></html>"""

@app.get("/capability/new", include_in_schema=False)
def capability_new_browser(request: Request, db: Session = Depends(get_db)):
    response = new_capability_json(db)
    if "text/html" not in request.headers.get("accept", "").lower():
        return response
    payload = json.loads(response.body)
    personal_url = html.escape(payload["personal_url"], quote=True)
    headers = {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
    }
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Private TestGraph capability</title>
<style>
body{{font-family:system-ui;background:#f5f6f8;color:#172033;margin:0}}
main{{max-width:760px;margin:60px auto;padding:0 20px}}
.card{{background:white;border:1px solid #d9dde5;border-radius:16px;padding:28px;box-shadow:0 8px 30px rgba(0,0,0,.05)}}
h1{{margin-top:0;font-size:28px}}p{{line-height:1.55}}
.secret{{word-break:break-all;padding:14px;background:#f3f5f8;border:1px solid #d9dde5;border-radius:10px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}
.actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}}
button,a.button{{font:inherit;padding:11px 15px;border-radius:9px;border:1px solid #aeb5c1;background:white;color:#172033;text-decoration:none;cursor:pointer}}
button.primary{{background:#172033;color:white;border-color:#172033}}
.warning{{margin-top:22px;padding:14px;border-left:4px solid #b7791f;background:#fff8e6}}
.small{{font-size:14px;color:#626b7a}}
</style></head>
<body><main><div class="card">
<h1>Your private TestGraph capability URL</h1>
<p>This URL is the credential. Keep it private. Anyone who has it can access this TestGraph capability.</p>
<div id="capability" class="secret">{personal_url}</div>
<div class="actions">
<button class="primary" type="button" onclick="navigator.clipboard.writeText(document.getElementById('capability').innerText).then(()=>this.textContent='Copied')">Copy URL</button>
<a class="button" href="{personal_url}" rel="noreferrer">Open capability</a>
</div>
<div class="warning"><strong>Do not post or share this URL publicly.</strong> Store it like a password or API key.</div>
<p class="small">API clients can continue to request this endpoint without an HTML Accept header and receive the existing JSON response.</p>
<p><a href="/">Back to TestGraph</a></p>
</div></main></body></html>""", headers=headers)

@app.get("/reviews", response_class=FileResponse, include_in_schema=False)
def review_browser(): return FileResponse(REVIEWS_HTML)

@app.get("/deliberations", response_class=FileResponse, include_in_schema=False)
def deliberations_page(): return FileResponse(DELIBERATIONS_HTML)

@app.get("/tools/recipe-reviews", include_in_schema=False)
def recipe_review_browser(): return RedirectResponse(url="/tools/recipe-reviews/view", status_code=307)

app.include_router(router);app.include_router(v2_router);app.include_router(integrations_router);app.include_router(capability_router)
app.include_router(oauth_router);app.include_router(mcp_router);app.include_router(mcp_v2_router);app.include_router(actions_router)
app.include_router(actions_v2_router);app.include_router(development_router);app.include_router(uci_reviews_router)
