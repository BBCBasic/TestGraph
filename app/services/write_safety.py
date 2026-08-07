from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import IdempotencyRecord
from app.services.core import request_hash


def begin_idempotent_write(db: Session, *, client_id: str, key: str, payload: dict):
    if not key or len(key) < 8 or len(key) > 200:
        raise ValueError("idempotency_key must be 8-200 characters")
    payload_hash = request_hash(payload)
    existing = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.client_id == client_id,
        IdempotencyRecord.key == key,
    ))
    if existing:
        if existing.request_hash != payload_hash:
            raise ValueError("Idempotency key was reused for different content")
        return payload_hash, existing.response_body
    return payload_hash, None


def finish_idempotent_write(db: Session, *, client_id: str, key: str, payload_hash: str, response_body: dict, response_status: int = 201):
    db.add(IdempotencyRecord(
        client_id=client_id,
        key=key,
        request_hash=payload_hash,
        response_status=response_status,
        response_body=response_body,
    ))
    db.commit()
