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
from app.models.v2 import Concept, ConceptField, ConceptFieldProposal, FieldAlias
from app.services.v2 import approve_field_proposal, delete_field_proposal, reject_field_proposal
from app.services.vocabulary_governance import vocabulary_index
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


def _vocabulary_payload(db: Session) -> dict:
    concepts = list(db.scalars(select(Concept).order_by(Concept.path)).all())
    fields = list(db.scalars(select(ConceptField).order_by(ConceptField.created_at)).all())
    aliases = list(db.scalars(select(FieldAlias).order_by(FieldAlias.created_at)).all())
    proposals = list(db.scalars(select(ConceptFieldProposal).order_by(ConceptFieldProposal.created_at)).all())

    fields_by_concept: dict[str, list[dict]] = {}
    for field in fields:
        fields_by_concept.setdefault(str(field.concept_id), []).append({
            "name": field.canonical_name,
            "status": field.status,
            "type": field.data_type,
            "description": field.description,
        })

    aliases_by_concept: dict[str, list[dict]] = {}
    for alias in aliases:
        aliases_by_concept.setdefault(str(alias.concept_id), []).append({
            "alias": alias.alias,
            "field_id": str(alias.field_id),
        })

    proposals_by_concept: dict[str, list[dict]] = {}
    for proposal in proposals:
        proposals_by_concept.setdefault(str(proposal.concept_id), []).append({
            "id": str(proposal.id),
            "name": proposal.canonical_name,
            "status": proposal.status,
            "description": proposal.description,
            "schema": proposal.json_schema,
            "proposed_by": proposal.proposer_client_id,
        })

    concept_rows = []
    for concept in concepts:
        concept_rows.append({
            "id": str(concept.id),
            "path": concept.path,
            "name": concept.name,
            "parent_id": str(concept.parent_id) if concept.parent_id else None,
            "status": concept.status,
            "version": concept.version,
            "description": concept.description,
            "fields": fields_by_concept.get(str(concept.id), []),
            "aliases": aliases_by_concept.get(str(concept.id), []),
            "proposals": proposals_by_concept.get(str(concept.id), []),
        })

    return {"concepts": concept_rows, "words": vocabulary_index(db).get("index", [])}


@router.get("/vocabulary", response_class=HTMLResponse)
def vocabulary_browser(db: Session = Depends(get_db)) -> HTMLResponse:
    _require_enabled()
    payload = json.dumps(_vocabulary_payload(db), separators=(",", ":")).replace("</", "<\\/")
    response = HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TasteGraph DNS vocabulary</title>
<style>
:root{{--ink:#18211c;--muted:#66756c;--paper:#f6f3eb;--card:#fffdf8;--line:#d9dfd8;--accent:#236b4b;--pending:#fff0bd;--active:#dff2e5;--rejected:#f7dcdc}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 system-ui,-apple-system,sans-serif}}a{{color:var(--accent)}}
main{{width:min(1400px,calc(100% - 24px));margin:24px auto 48px}}header{{display:flex;gap:16px;align-items:end;justify-content:space-between;flex-wrap:wrap}}
h1{{margin:.2rem 0;font:700 clamp(1.8rem,4vw,3rem)/1.05 Georgia,serif}}.muted{{color:var(--muted)}}
.controls{{display:grid;grid-template-columns:minmax(180px,1fr) auto auto;gap:8px;margin:18px 0}}input,button{{font:inherit;border:1px solid var(--line);border-radius:10px;padding:10px;background:white;color:var(--ink)}}button{{cursor:pointer}}button:hover{{border-color:var(--accent)}}
.layout{{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:16px}}.panel{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;min-width:0}}
.panel h2{{margin:0 0 12px;font-size:1.05rem}}#tree{{min-height:360px}}.node{{margin:3px 0}}.node-row{{display:flex;align-items:center;gap:7px;padding:7px 8px;border-radius:9px}}.node-row:hover{{background:#eef1ed}}.node-row.selected{{outline:2px solid var(--accent);background:#edf5f0}}
.toggle{{width:28px;padding:2px 5px;background:transparent;border:0}}.node-name{{font-weight:650;cursor:pointer;overflow-wrap:anywhere}}.path{{font-size:12px;color:var(--muted);margin-left:auto;overflow-wrap:anywhere;text-align:right}}.children{{margin-left:22px;border-left:1px solid var(--line);padding-left:8px}}.collapsed>.children{{display:none}}
.badge{{font-size:11px;border-radius:999px;padding:2px 7px;background:#eee}}.badge.active{{background:var(--active)}}.badge.pending{{background:var(--pending)}}.badge.rejected{{background:var(--rejected)}}
#details{{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}}.detail-list{{display:grid;gap:8px}}.detail-item{{border:1px solid var(--line);border-radius:10px;padding:9px}}.detail-item code{{font-size:12px;overflow-wrap:anywhere}}
.word-list{{display:grid;gap:6px;max-height:70vh;overflow:auto;padding-right:4px}}.word{{display:flex;justify-content:space-between;gap:10px;text-align:left;width:100%;background:white}}.word strong{{overflow-wrap:anywhere}}.count{{color:var(--muted)}}
.legend{{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 0}}.empty{{padding:20px;color:var(--muted);text-align:center}}
@media(max-width:850px){{.layout{{grid-template-columns:1fr}}.word-list{{max-height:none}}.controls{{grid-template-columns:1fr 1fr}}.controls input{{grid-column:1/-1}}}}
</style>
</head>
<body><main>
<header><div><a href="/">← TasteGraph</a><h1>DNS vocabulary</h1><div class="muted">Interactive concept tree plus the complete indexed word list. Click a concept for fields and proposals; click a word to highlight where it occurs.</div></div></header>
<div class="controls"><input id="search" type="search" placeholder="Filter concepts or words…" aria-label="Filter concepts or words"><button id="expand" type="button">Expand all</button><button id="collapse" type="button">Collapse all</button></div>
<div class="legend"><span class="badge active">active</span><span class="badge pending">pending</span><span class="badge rejected">rejected</span></div>
<div class="layout">
<section class="panel"><h2>Concept tree</h2><div id="tree"></div><div id="details" aria-live="polite"><span class="muted">Select a concept to inspect it.</span></div></section>
<aside class="panel"><h2>Words <span id="word-count" class="muted"></span></h2><div id="words" class="word-list"></div></aside>
</div>
<script id="vocabulary-data" type="application/json">{payload}</script>
<script>
const DATA=JSON.parse(document.getElementById('vocabulary-data').textContent);
const byId=new Map(DATA.concepts.map(c=>[c.id,c]));
const children=new Map();
for(const c of DATA.concepts){{const key=c.parent_id||'root';if(!children.has(key))children.set(key,[]);children.get(key).push(c)}}
for(const items of children.values())items.sort((a,b)=>a.path.localeCompare(b.path));
const tree=document.getElementById('tree'), words=document.getElementById('words'), details=document.getElementById('details'), search=document.getElementById('search');
let selectedId=null;
function nodeMatches(c,q){{if(!q)return true;const blob=[c.path,c.description,...c.fields.map(f=>f.name),...c.proposals.map(p=>p.name)].join(' ').toLowerCase();if(blob.includes(q))return true;return (children.get(c.id)||[]).some(ch=>nodeMatches(ch,q));}}
function renderTree(){{const q=search.value.trim().toLowerCase();tree.innerHTML='';const roots=children.get('root')||[];for(const c of roots){{if(nodeMatches(c,q))tree.appendChild(buildNode(c,q));}}if(!tree.children.length)tree.innerHTML='<div class="empty">No matching concepts.</div>';}}
function buildNode(c,q){{const wrap=document.createElement('div');wrap.className='node';wrap.dataset.id=c.id;const row=document.createElement('div');row.className='node-row'+(selectedId===c.id?' selected':'');const kids=(children.get(c.id)||[]).filter(ch=>nodeMatches(ch,q));const toggle=document.createElement('button');toggle.type='button';toggle.className='toggle';toggle.textContent=kids.length?'▾':'·';toggle.setAttribute('aria-label','Toggle '+c.path);row.appendChild(toggle);const name=document.createElement('span');name.className='node-name';name.textContent=c.name;name.addEventListener('click',()=>selectConcept(c.id));row.appendChild(name);const badge=document.createElement('span');badge.className='badge '+c.status;badge.textContent=c.status;row.appendChild(badge);const path=document.createElement('span');path.className='path';path.textContent=c.path;row.appendChild(path);wrap.appendChild(row);if(kids.length){{const group=document.createElement('div');group.className='children';for(const ch of kids)group.appendChild(buildNode(ch,q));wrap.appendChild(group);toggle.addEventListener('click',()=>{{wrap.classList.toggle('collapsed');toggle.textContent=wrap.classList.contains('collapsed')?'▸':'▾';}});}}return wrap;}}
function selectConcept(id){{selectedId=id;renderTree();const c=byId.get(id);if(!c)return;const fields=c.fields.length?c.fields.map(f=>`<div class="detail-item"><strong>${esc(f.name)}</strong> <span class="badge ${esc(f.status)}">${esc(f.status)}</span><br><code>${esc(f.type||'any')}</code>${f.description?`<div class="muted">${esc(f.description)}</div>`:''}</div>`).join(''):'<div class="muted">No canonical fields on this node.</div>';const proposals=c.proposals.length?c.proposals.map(p=>`<div class="detail-item"><strong>${esc(p.name)}</strong> <span class="badge ${esc(p.status)}">${esc(p.status)}</span>${p.description?`<div class="muted">${esc(p.description)}</div>`:''}</div>`).join(''):'<div class="muted">No proposals on this node.</div>';details.innerHTML=`<h3>${esc(c.path)}</h3><div class="muted">version ${c.version}${c.description?' · '+esc(c.description):''}</div><h4>Canonical fields</h4><div class="detail-list">${fields}</div><h4>Proposals</h4><div class="detail-list">${proposals}</div>`;}}
function renderWords(){{const q=search.value.trim().toLowerCase();const filtered=DATA.words.filter(w=>!q||w.word.includes(q)||w.locations.some(l=>(l.concept_path||'').toLowerCase().includes(q)));document.getElementById('word-count').textContent=`(${filtered.length}/${DATA.words.length})`;words.innerHTML='';for(const entry of filtered){{const b=document.createElement('button');b.type='button';b.className='word';b.innerHTML=`<strong>${esc(entry.word)}</strong><span class="count">${entry.locations.length}</span>`;b.addEventListener('click',()=>focusWord(entry));words.appendChild(b);}}if(!filtered.length)words.innerHTML='<div class="empty">No matching words.</div>';}}
function focusWord(entry){{const paths=[...new Set(entry.locations.map(l=>l.concept_path).filter(Boolean))];if(paths.length){{const c=DATA.concepts.find(c=>paths.includes(c.path));if(c)selectConcept(c.id);}}search.value=entry.word;renderTree();renderWords();}}
function esc(v){{return String(v??'').replace(/[&<>\"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[ch]));}}
search.addEventListener('input',()=>{{renderTree();renderWords();}});document.getElementById('expand').addEventListener('click',()=>document.querySelectorAll('.node').forEach(n=>n.classList.remove('collapsed')));document.getElementById('collapse').addEventListener('click',()=>document.querySelectorAll('.node').forEach(n=>n.classList.add('collapsed')));
renderTree();renderWords();
</script>
</main></body></html>""")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response
