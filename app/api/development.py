from html import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from scripts.reset_user_data import reset_user_data

router = APIRouter(prefix="/development", include_in_schema=False)
settings = get_settings()


def _require_enabled():
    if not settings.enable_development_reset: raise HTTPException(404,"Not found")


def _page(message="",success=False):
    notice=f'<p class="notice">{escape(message)}</p>' if message else ""
    response=HTMLResponse(f"""<!doctype html><html><head><title>Reset TasteGraph to basics</title><style>body{{font:16px/1.5 system-ui;max-width:680px;margin:48px auto;padding:0 20px}}main{{border:1px solid #ddd;border-radius:16px;padding:28px}}button{{width:100%;padding:12px;background:#a52626;color:white;border:0;border-radius:10px;font-weight:700;cursor:pointer}}.notice{{background:#e1f2e7;padding:12px}}</style></head><body><main><h1>Reset database to basics</h1><p><strong>Development only.</strong> This permanently removes review, subject, vocabulary, assessment, source, profile and idempotency data.</p><p>Users, schemas, OAuth clients and tokens, and API/capability credentials are preserved.</p>{notice}<form method="post" action="/development/reset" onsubmit="return confirm('Permanently reset TasteGraph to basics? OAuth connections and API keys will be preserved.');"><button type="submit">Reset database to basics</button></form></main></body></html>""")
    response.headers["Cache-Control"]="no-store";response.headers["X-Robots-Tag"]="noindex, nofollow";return response


@router.get("/reset",response_class=HTMLResponse)
def reset_page(): _require_enabled();return _page()


@router.post("/reset",response_class=HTMLResponse)
def perform_reset():
    _require_enabled();counts=reset_user_data();return _page(f"Database reset to basics. Deleted {sum(counts.values())} records; OAuth connections and API credentials were preserved.",True)
