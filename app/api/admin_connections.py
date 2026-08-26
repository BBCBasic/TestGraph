from __future__ import annotations

import hmac
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import AuditEvent

router = APIRouter(prefix="/admin", include_in_schema=False)


def _render(events: list[AuditEvent], *, error: str = "") -> HTMLResponse:
    rows = []
    for event in events:
        details = event.details or {}
        rows.append(
            "<tr>"
            f"<td>{escape(event.created_at.isoformat(sep=' ', timespec='seconds'))}</td>"
            f"<td>{escape(str(details.get('connection_kind', 'unknown')))}</td>"
            f"<td>{escape(str(details.get('client_name', event.client_id)))}</td>"
            f"<td><code>{escape(event.client_id)}</code></td>"
            f"<td>{escape(str(details.get('resource', '')))}</td>"
            f"<td>{escape(str(details.get('server_version', '')))}</td>"
            f"<td><code>{escape(str(details.get('build_sha', 'unknown')))[:12]}</code></td>"
            f"<td>{escape(str(details.get('user_agent', '')))[:120]}</td>"
            "</tr>"
        )
    body = "".join(rows) or '<tr><td colspan="8">No connection events recorded yet.</td></tr>'
    notice = f'<p class="error">{escape(error)}</p>' if error else ""
    response = HTMLResponse(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>TestGraph connections</title><style>
body{{font:15px/1.45 system-ui;margin:32px;color:#20231f}}h1{{margin-bottom:6px}}p{{color:#5b625d}}table{{border-collapse:collapse;width:100%;margin-top:22px}}th,td{{border-bottom:1px solid #ddd;padding:9px;text-align:left;vertical-align:top}}th{{background:#f5f5f5}}code{{font-size:12px}}.error{{color:#a52626}}input,button{{padding:10px;border-radius:8px;border:1px solid #bbb}}button{{background:#20231f;color:white;border:0;font-weight:700}}form{{display:flex;gap:8px;max-width:520px}}
</style></head><body><h1>TestGraph connection audit</h1><p>Successful OAuth authorisations. No raw IP address is stored.</p>{notice}
<table><thead><tr><th>Time</th><th>Type</th><th>Client</th><th>Client ID</th><th>Resource</th><th>Server</th><th>Build</th><th>User agent</th></tr></thead><tbody>{body}</tbody></table></body></html>""")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("/connections", response_class=HTMLResponse)
def connections_login():
    response = HTMLResponse("""<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>TestGraph connections</title><style>body{font:16px/1.5 system-ui;max-width:560px;margin:8vh auto;padding:0 20px}main{border:1px solid #ddd;border-radius:16px;padding:28px}input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:12px;border-radius:9px}input{border:1px solid #bbb}button{border:0;background:#20231f;color:white;font-weight:700}</style></head><body><main><h1>Connection audit</h1><p>Enter the TestGraph admin API key to view successful OAuth connection events.</p><form method="post"><input type="password" name="admin_key" autocomplete="off" required><button type="submit">View connections</button></form></main></body></html>""")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.post("/connections", response_class=HTMLResponse)
def connections_page(admin_key: str = Form(...), db: Session = Depends(get_db)):
    expected = get_settings().development_api_key
    if not expected or not hmac.compare_digest(admin_key, expected):
        raise HTTPException(401, "Invalid admin key")
    events = list(db.scalars(
        select(AuditEvent)
        .where(AuditEvent.action == "oauth.connected")
        .order_by(desc(AuditEvent.created_at))
        .limit(500)
    ).all())
    return _render(events)
