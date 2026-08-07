from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.capability import (
    CapabilityReviewCreate,
    _base,
    _credential,
    _hash_key,
    _make_commit_token,
    _open_commit_token,
    _save_review,
)
from app.db.session import get_db
from app.models.entities import CapabilityCredential, User

router = APIRouter()


@router.get("/capability/test-start", response_class=HTMLResponse, include_in_schema=False)
def capability_test_start(db: Session = Depends(get_db)):
    """Temporary browser-driven GET-only self-test. Creates a disposable capability and prepares one fixed review."""
    raw_key = "tg_" + secrets.token_urlsafe(32)
    user = User(display_name="TasteGraph GET self-test", profile_data={"created_via": "get_self_test"})
    db.add(user)
    db.flush()
    cred = CapabilityCredential(user_id=user.id, key_hash=_hash_key(raw_key))
    db.add(cred)
    db.commit()
    db.refresh(cred)

    payload = CapabilityReviewCreate(
        subject_type="recipe",
        subject_name="TasteGraph GET Capability Test Recipe",
        canonical_key=f"tastegraph-get-capability-test-{user.id}",
        headline="[TEST] GET capability write verification",
        summary="Fictional review created solely to verify that a GET-only AI client can commit and read back a TasteGraph review.",
        common_data={},
        domain_data={},
        visibility="private",
        user_approved=True,
        idempotency_key=f"get-self-test-{user.id}",
        source_client="chatgpt-web-fetch-self-test",
    ).model_dump(mode="json")
    token = _make_commit_token(payload, cred.id)
    href = f"{_base()}/capability/test-commit/{raw_key}/{token}"
    return HTMLResponse(
        f"<!doctype html><html><body><h1>TasteGraph GET write self-test</h1>"
        f"<p>No review has been saved yet.</p>"
        f"<p><a href=\"{href}\">Commit the prepared test review</a></p></body></html>",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow, noarchive"},
    )


@router.get("/capability/test-commit/{key}/{token}", response_class=HTMLResponse, include_in_schema=False)
def capability_test_commit(key: str, token: str, request: Request, db: Session = Depends(get_db)):
    """Temporary self-test wrapper around the real signed-token commit path."""
    cred = _credential(db, key)
    raw_payload = _open_commit_token(token, cred.id)
    payload = CapabilityReviewCreate.model_validate(raw_payload)
    body, _ = _save_review(db, cred, key, payload, request.state.request_id)
    href = body["url"]
    return HTMLResponse(
        f"<!doctype html><html><body><h1>Review saved</h1>"
        f"<p>Experience ID: {body['experience_id']}</p>"
        f"<p>Headline: {body['headline']}</p>"
        f"<p><a href=\"{href}\">Read the stored review back</a></p></body></html>",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow, noarchive"},
    )
