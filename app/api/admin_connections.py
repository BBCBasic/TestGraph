from __future__ import annotations

import base64
import hmac
import html

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.api.account import router as account_router
from app.core.config import get_settings
from app.db.session import get_db
from app.services.connection_audit import recent_oauth_connections

router = APIRouter()


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        headers={
            "WWW-Authenticate": 'Basic realm="TestGraph admin", charset="UTF-8"',
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow, noarchive",
        },
    )


def _admin_authorized(request: Request) -> bool:
    expected = (get_settings().development_api_key or "").strip()
    if not expected:
        return False
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
        _username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return False
    return hmac.compare_digest(password, expected)


@router.get("/admin/connections", response_class=HTMLResponse, include_in_schema=False)
def admin_connections(request: Request, db: Session = Depends(get_db)):
    if not _admin_authorized(request):
        return _unauthorized()

    events = recent_oauth_connections(db, limit=200)
    rows = []
    for event in events:
        details = event.details or {}
        created = event.created_at.isoformat(sep=" ", timespec="seconds") if event.created_at else ""
        rows.append(
            "<tr>"
            f"<td>{html.escape(created)}</td>"
            f"<td>{html.escape(str(details.get('client_name') or 'MCP client'))}</td>"
            f"<td><code>{html.escape(event.client_id)}</code></td>"
            f"<td><code>{html.escape(str(details.get('resource') or ''))}</code></td>"
            f"<td><code>{html.escape(str(details.get('build_sha') or 'unknown')[:12])}</code></td>"
            f"<td><code>{html.escape(str(details.get('deployment_id') or 'unknown'))}</code></td>"
            f"<td>{html.escape(str(details.get('environment') or 'unknown'))}</td>"
            "</tr>"
        )

    body = "".join(rows) or '<tr><td colspan="7" class="empty">No OAuth connections have been recorded yet.</td></tr>'
    headers = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
    }
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TestGraph connection audit</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;margin:0;background:#f5f7fa;color:#172033}}
main{{max-width:1200px;margin:44px auto;padding:0 20px}}
h1{{margin-bottom:6px}}p{{color:#5f6877}}.card{{background:#fff;border:1px solid #dce1e8;border-radius:14px;overflow:auto}}
table{{border-collapse:collapse;width:100%;min-width:980px}}th,td{{padding:12px 14px;text-align:left;border-bottom:1px solid #e8ebef;font-size:14px;vertical-align:top}}
th{{background:#f7f8fa;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#596273;position:sticky;top:0}}
code{{font-size:12px}}.empty{{padding:30px;text-align:center;color:#6b7280}}.meta{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}
.pill{{background:#fff;border:1px solid #dce1e8;border-radius:999px;padding:6px 10px;font-size:13px}}a{{color:#2f5bea}}
</style></head><body><main>
<h1>TestGraph connection audit</h1>
<p>Successful fresh OAuth connections. Refresh-token exchanges are not recorded as new connections.</p>
<div class="meta"><span class="pill">Showing {len(events)} most recent</span><span class="pill">No IP addresses or OAuth secrets stored</span></div>
<div class="card"><table><thead><tr><th>Connected</th><th>Client</th><th>Client ID</th><th>MCP resource</th><th>Build</th><th>Deployment</th><th>Environment</th></tr></thead><tbody>{body}</tbody></table></div>
<p><a href="/">Back to TestGraph</a></p>
</main></body></html>""",
        headers=headers,
    )


# Account/login routes are part of the same human-facing OAuth surface.
router.include_router(account_router)
