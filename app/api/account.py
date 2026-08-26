from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import secrets
import time
import uuid
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import CapabilityCredential, ExternalIdentity, User, now_utc

router = APIRouter()
COOKIE = "testgraph_account"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode())


def _sign(value: str) -> str:
    return _b64(hmac.new(get_settings().app_secret.encode(), value.encode(), hashlib.sha256).digest())


def _token(payload: dict) -> str:
    raw = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return raw + "." + _sign(raw)


def _open(token: str) -> dict:
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        raise HTTPException(401, "Invalid account session")
    if not hmac.compare_digest(_sign(raw), sig):
        raise HTTPException(401, "Invalid account session")
    try:
        payload = json.loads(_unb64(raw))
    except Exception:
        raise HTTPException(401, "Invalid account session")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(401, "Account session expired")
    return payload


def _current_user(request: Request, db: Session) -> User:
    value = request.cookies.get(COOKIE)
    if not value:
        raise HTTPException(401, "Sign in required")
    payload = _open(value)
    try:
        user_id = uuid.UUID(payload["uid"])
    except Exception:
        raise HTTPException(401, "Invalid account session")
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(401, "Account not found")
    return user


def _safe_continue(value: str | None) -> str | None:
    if not value:
        return None
    # OAuth continuation is deliberately restricted to TestGraph's own authorization route.
    if not value.startswith("/oauth/authorize?"):
        raise HTTPException(400, "Invalid account continuation")
    return value


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f'''<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>{title} — TestGraph</title><style>body{{font:16px system-ui;max-width:760px;margin:50px auto;padding:0 20px;line-height:1.5;color:#172033}}a,button{{font:inherit}}.button{{display:inline-block;padding:10px 14px;background:#172033;color:white;text-decoration:none;border:0;border-radius:8px;cursor:pointer}}.button.secondary{{background:white;color:#172033;border:1px solid #aeb5c1}}code{{background:#f3f5f7;padding:3px 6px;border-radius:5px;word-break:break-all}}li{{margin:10px 0}}.note{{background:#f5f7fa;padding:14px;border-radius:8px}}.actions{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}}</style></head><body><p><a href="/">TestGraph</a></p><h1>{title}</h1>{body}</body></html>''',
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


@router.get("/account")
def account(
    request: Request,
    continue_url: str | None = Query(default=None, alias="continue"),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    next_url = _safe_continue(continue_url)
    try:
        user = _current_user(request, db)
    except HTTPException:
        google_href = "/account/google/start"
        if next_url:
            google_href += "?" + urlencode({"continue": next_url})
        google = (
            f'<a class="button" href="{html.escape(google_href, quote=True)}">Sign in with Google</a>'
            if settings.google_login_enabled
            else '<p>Google sign-in is not configured on this deployment.</p>'
        )
        fallback = '<p><a href="/capability/new">Or create a standalone capability without Google</a></p>'
        if next_url:
            separator = "&" if "?" in next_url else "?"
            key_url = next_url + separator + "use_key=1"
            fallback = (
                '<p class="note">Google is optional. You can still connect exactly as before with an existing '
                f'<code>tg_</code> capability.</p><div class="actions"><a class="button secondary" href="{html.escape(key_url, quote=True)}">Use an existing capability key</a></div>'
                '<p><a href="/capability/new">Create a new standalone capability instead</a></p>'
            )
        return _page(
            "Your TestGraph account",
            '<p>Use Google for a persistent TestGraph identity, or continue with the standalone capability-key method. Google is optional.</p>'
            f'<div class="actions">{google}</div>{fallback}',
        )

    identities = list(db.scalars(select(ExternalIdentity).where(ExternalIdentity.user_id == user.id)))
    creds = list(db.scalars(select(CapabilityCredential).where(CapabilityCredential.user_id == user.id).order_by(CapabilityCredential.created_at.desc())))
    active = sum(1 for c in creds if c.revoked_at is None)
    provider = ", ".join(i.provider for i in identities) or "account"
    continuation = ""
    if next_url:
        continuation = (
            '<div class="note"><strong>An AI is waiting to connect to this TestGraph account.</strong></div>'
            f'<div class="actions"><a class="button" href="{html.escape(next_url, quote=True)}">Continue connecting AI</a></div>'
        )
    return _page(
        "Your TestGraph account",
        f'<p>Signed in via {html.escape(provider)}. This persistent TestGraph identity has <strong>{active}</strong> active capability key(s).</p>'
        '<p>Raw capability keys are deliberately not stored and cannot be displayed again. Generate as many as you need; save any key you want to reuse.</p>'
        f'{continuation}'
        '<form method="post" action="/account/capabilities"><button class="button" type="submit">Generate new tg_ key</button></form>'
        '<p><a href="/account/logout">Sign out</a></p>',
    )


@router.get("/account/google/start")
def google_start(continue_url: str | None = Query(default=None, alias="continue")):
    s = get_settings()
    if not s.google_login_enabled:
        raise HTTPException(503, "Google sign-in is not configured")
    next_url = _safe_continue(continue_url)
    state_payload = {
        "purpose": "google-login",
        "exp": int(time.time()) + 600,
        "nonce": secrets.token_urlsafe(12),
    }
    if next_url:
        state_payload["continue"] = next_url
    state = _token(state_payload)
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": f"{s.public_base_url}/account/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), status_code=303)


@router.get("/account/google/callback")
def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    s = get_settings()
    st = _open(state)
    if st.get("purpose") != "google-login":
        raise HTTPException(400, "Invalid login state")
    if not s.google_login_enabled:
        raise HTTPException(503, "Google sign-in is not configured")
    next_url = _safe_continue(st.get("continue"))
    with httpx.Client(timeout=10) as client:
        token_response = client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "redirect_uri": f"{s.public_base_url}/account/google/callback",
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code != 200:
            raise HTTPException(400, "Google token exchange failed")
        access = token_response.json().get("access_token")
        profile_response = client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access}"},
        )
        if profile_response.status_code != 200:
            raise HTTPException(400, "Google profile lookup failed")
        profile = profile_response.json()
    subject = str(profile.get("sub", ""))
    email = profile.get("email")
    name = profile.get("name") or "TestGraph user"
    if not subject:
        raise HTTPException(400, "Google identity did not contain a subject")
    identity = db.scalar(select(ExternalIdentity).where(ExternalIdentity.provider == "google", ExternalIdentity.provider_subject == subject))
    if identity:
        user = db.get(User, identity.user_id)
        identity.email = email
        identity.display_name = name
        identity.last_login_at = now_utc()
    else:
        user = User(display_name=name, profile_data={"created_via": "google"})
        db.add(user)
        db.flush()
        identity = ExternalIdentity(user_id=user.id, provider="google", provider_subject=subject, email=email, display_name=name)
        db.add(identity)
    db.commit()
    session = _token({"uid": str(user.id), "exp": int(time.time()) + s.google_account_session_hours * 3600})
    destination = next_url or "/account"
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(
        COOKIE,
        session,
        max_age=s.google_account_session_hours * 3600,
        httponly=True,
        secure=s.public_base_url.startswith("https://"),
        samesite="lax",
    )
    return response


@router.post("/account/capabilities")
def create_account_capability(request: Request, db: Session = Depends(get_db)):
    user = _current_user(request, db)
    raw = "tg_" + secrets.token_urlsafe(32)
    db.add(CapabilityCredential(user_id=user.id, key_hash=hashlib.sha256(raw.encode()).hexdigest()))
    db.commit()
    url = f"{get_settings().public_base_url}/c/{raw}"
    return _page(
        "New capability created",
        '<p class="note"><strong>Copy this now.</strong> TestGraph stores only its hash and cannot show this key again.</p>'
        f'<p><code>{html.escape(raw)}</code></p><p>Capability URL:</p><p><code>{html.escape(url)}</code></p>'
        '<p>This key belongs to your persistent TestGraph identity. You may use it with ChatGPT, Claude or another compatible client.</p>'
        '<p><a href="/account">Back to account</a></p>',
    )


@router.get("/account/logout")
def logout():
    response = RedirectResponse("/account", status_code=303)
    response.delete_cookie(COOKIE)
    return response
