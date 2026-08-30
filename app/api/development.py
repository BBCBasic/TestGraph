from html import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.services.tg_ai_resolver import check_resolver_connectivity
from scripts.reset_user_data import reset_user_data

router = APIRouter(prefix="/development", include_in_schema=False)
settings = get_settings()


def reset_enabled() -> bool:
    """Return whether development controls may be exposed."""
    return settings.enable_development_reset and settings.environment.lower() != "production"


def _require_enabled():
    if not reset_enabled():
        raise HTTPException(404, "Not found")


def _page(message="", success=False):
    notice = f'<p class="notice">{escape(message)}</p>' if message else ""
    response = HTMLResponse(f"""<!doctype html><html><head><title>TestGraph development controls</title><style>body{{font:16px/1.5 system-ui;max-width:680px;margin:48px auto;padding:0 20px}}main{{border:1px solid #ddd;border-radius:16px;padding:28px}}form+form{{margin-top:18px}}button{{width:100%;padding:12px;border:0;border-radius:10px;font-weight:700;cursor:pointer}}.reset{{background:#a52626;color:white}}.probe{{background:#244f73;color:white}}.notice{{background:#e1f2e7;padding:12px;overflow-wrap:anywhere}}</style></head><body><main><h1>Development controls</h1><p><strong>Development only.</strong> The connectivity test makes one small OpenAI request and does not modify TestGraph data.</p>{notice}<form method="post" action="/development/tg-ai-connectivity"><button class="probe" type="submit">Test TG-AI connectivity</button></form><hr><p>Reset permanently removes review, subject, vocabulary, assessment, source, profile and idempotency data. Users, schemas, OAuth clients and tokens, and API/capability credentials are preserved.</p><form method="post" action="/development/reset" onsubmit="return confirm('Permanently reset TestGraph to basics? OAuth connections and API keys will be preserved.');"><button class="reset" type="submit">Reset database to basics</button></form></main></body></html>""")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("/reset", response_class=HTMLResponse)
def reset_page():
    _require_enabled()
    return _page()


@router.post("/tg-ai-connectivity", response_class=HTMLResponse)
def test_tg_ai_connectivity():
    _require_enabled()
    result = check_resolver_connectivity()
    if result.get("ok"):
        message = (
            "TG-AI connectivity OK. "
            f"Model: {result.get('model')}; response: {result.get('response_text')}; "
            f"response id: {result.get('response_id')}."
        )
        return _page(message, True)
    return _page(
        "TG-AI connectivity failed. "
        f"Enabled: {result.get('enabled')}; API key present: {result.get('api_key_present')}; "
        f"model: {result.get('model')}; error: {result.get('error', 'unknown error')}.",
        False,
    )


@router.post("/reset", response_class=HTMLResponse)
def perform_reset():
    _require_enabled()
    counts = reset_user_data()
    return _page(
        f"Database reset to basics. Deleted {sum(counts.values())} records; OAuth connections and API credentials were preserved.",
        True,
    )
