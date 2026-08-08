from __future__ import annotations

from html import escape
import json
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.v2 import Concept, ConceptFieldProposal
from app.services.v2 import approve_field_proposal, delete_field_proposal, reject_field_proposal
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



def _proposal_page(db: Session, message: str = "") -> HTMLResponse:
    rows = db.execute(
        select(ConceptFieldProposal, Concept)
        .join(Concept, ConceptFieldProposal.concept_id == Concept.id)
        .order_by(Concept.path, ConceptFieldProposal.created_at)
    ).all()
    cards = []
    for proposal, concept in rows:
        schema = escape(json.dumps(proposal.json_schema, indent=2, sort_keys=True))
        actions = ""
        if proposal.status == "pending":
            actions = f"""
            <div class="actions">
              <form method="post" action="/development/concept-fields/{proposal.id}/approve"><button class="approve">Approve</button></form>
              <form method="post" action="/development/concept-fields/{proposal.id}/reject"><button class="reject">Reject</button></form>
              <form method="post" action="/development/concept-fields/{proposal.id}/delete" onsubmit="return confirm('Delete this proposal?');"><button class="delete">Delete</button></form>
            </div>"""
        elif proposal.status == "rejected":
            actions = f"""<form method="post" action="/development/concept-fields/{proposal.id}/delete" onsubmit="return confirm('Delete this rejected proposal?');"><button class="delete">Delete</button></form>"""
        cards.append(f"""
        <article>
          <div class="status {escape(proposal.status)}">{escape(proposal.status)}</div>
          <h2>{escape(concept.path)} · {escape(proposal.canonical_name)}</h2>
          <p>{escape(proposal.description or "No description")}</p>
          <p><strong>Submitted as:</strong> {escape(proposal.submitted_name)} · <strong>Proposed by:</strong> {escape(proposal.proposer_client_id)}</p>
          <pre>{schema}</pre>
          {actions}
        </article>""")
    body = "".join(cards) or "<p>No field proposals yet.</p>"
    notice = f'<p class="notice">{escape(message)}</p>' if message else ""
    response = HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Concept field proposals</title><style>
:root{{color-scheme:light;--ink:#18211c;--paper:#f6f3eb;--card:#fffdf8;--line:#d9dfd8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.45 system-ui,sans-serif}}
main{{width:min(980px,calc(100% - 32px));margin:40px auto}}article{{position:relative;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px;margin:16px 0}}
pre{{overflow:auto;background:#eef1ed;padding:14px;border-radius:10px}}.status{{position:absolute;right:18px;top:18px;padding:4px 9px;border-radius:999px;background:#eee}}
.pending{{background:#fff0bd}}.approved{{background:#dff2e5}}.rejected{{background:#f7dcdc}}.actions{{display:flex;gap:10px}}.actions form{{flex:1}}button{{width:100%;padding:10px;border:0;border-radius:9px;font-weight:700;cursor:pointer}}
.approve{{background:#28734f;color:white}}.reject,.delete{{background:#a52626;color:white}}a{{color:#236b4b}}.notice{{background:#dff2e5;padding:12px;border-radius:10px}}
</style></head><body><main><a href="/">← TasteGraph</a><h1>Concept field proposals</h1>
<p>AI clients may propose fields here, but only your approval turns one into a canonical field.</p>{notice}{body}</main></body></html>""")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("/concept-fields", response_class=HTMLResponse)
def concept_fields_page(db: Session = Depends(get_db)) -> HTMLResponse:
    _require_enabled()
    return _proposal_page(db)


def _proposal_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(404, "Field proposal not found") from exc


@router.post("/concept-fields/{proposal_id}/approve")
def approve_concept_field(proposal_id: str, db: Session = Depends(get_db)):
    _require_enabled()
    try:
        approve_field_proposal(db, _proposal_id(proposal_id))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    return RedirectResponse("/development/concept-fields", status_code=303)


@router.post("/concept-fields/{proposal_id}/reject")
def reject_concept_field(
    proposal_id: str,
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_enabled()
    try:
        reject_field_proposal(db, _proposal_id(proposal_id), reason=reason or None)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    return RedirectResponse("/development/concept-fields", status_code=303)


@router.post("/concept-fields/{proposal_id}/delete")
def delete_concept_field(proposal_id: str, db: Session = Depends(get_db)):
    _require_enabled()
    try:
        delete_field_proposal(db, _proposal_id(proposal_id))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    return RedirectResponse("/development/concept-fields", status_code=303)
