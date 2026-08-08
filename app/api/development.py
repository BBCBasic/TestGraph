from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from scripts.reset_user_data import reset_user_data


router = APIRouter(prefix="/development", include_in_schema=False)
settings = get_settings()


def _require_enabled() -> None:
    if not settings.enable_development_reset:
        raise HTTPException(status_code=404, detail="Not found")


def _page(message: str = "", *, success: bool = False) -> HTMLResponse:
    notice = ""
    if message:
        role = "status" if success else "alert"
        css_class = "success" if success else "error"
        notice = f'<p class="notice {css_class}" role="{role}">{escape(message)}</p>'

    response = HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Empty TasteGraph user data</title>
  <style>
    :root{{color-scheme:light;--ink:#18211c;--muted:#607067;--paper:#f6f3eb;--card:#fffdf8;--line:#d9dfd8;--danger:#a52626;--danger-dark:#791b1b}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,-apple-system,sans-serif}}
    main{{width:min(680px,calc(100% - 32px));margin:48px auto}}.card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:28px;box-shadow:0 7px 24px #203b2b0d}}
    h1{{margin-top:8px;font:700 clamp(2rem,7vw,3.5rem)/1.05 Georgia,serif}}p{{color:var(--muted)}}
    button{{width:100%;font:inherit;border:1px solid var(--danger);border-radius:12px;padding:12px;margin-top:22px;background:var(--danger);color:white;font-weight:750;cursor:pointer}}button:hover{{background:var(--danger-dark)}}
    .warning{{border-left:4px solid var(--danger);padding:10px 14px;background:#fff1f1;color:var(--ink)}}.notice{{padding:12px 14px;border-radius:10px;color:var(--ink)}}.success{{background:#e1f2e7}}.error{{background:#fff1f1}}a{{color:#236b4b}}
  </style>
</head>
<body><main><a href="/">← TasteGraph</a><section class="card">
  <h1>Empty user data</h1>
  <p class="warning"><strong>Development only.</strong> This permanently removes all v1 and v2 reviews, subjects, concepts, assessments, sources, aliases, profile signals, idempotency records and audit history.</p>
  <p>User accounts, schemas, OAuth connections and capability credentials are preserved.</p>
  {notice}
  <form method="post" action="/development/reset" onsubmit="return confirm('Permanently empty all TasteGraph user data?');">
    <button type="submit">Empty user data</button>
  </form>
</section></main></body></html>"""
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("/reset", response_class=HTMLResponse)
def reset_page() -> HTMLResponse:
    _require_enabled()
    return _page()


@router.post("/reset", response_class=HTMLResponse)
def perform_reset() -> HTMLResponse:
    _require_enabled()
    counts = reset_user_data()
    deleted = sum(counts.values())
    return _page(f"User data is empty. Deleted {deleted} records.", success=True)
