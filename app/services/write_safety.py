from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AuditEvent, IdempotencyRecord
from app.services.core import request_hash


class IdempotencyKeyConflictError(ValueError):
    """Raised when an idempotency key is already bound to another payload."""

    def __init__(self, key: str):
        self.operation_scope = key.split(":", 1)[0]
        super().__init__("Idempotency key is already bound to different content")


def _record_idempotency_event(
    db: Session,
    *,
    client_id: str,
    key: str,
    payload_hash: str,
    action: str,
    details: dict,
) -> None:
    db.add(AuditEvent(
        actor_id=client_id,
        client_id=client_id,
        action=action,
        object_type="idempotency_key",
        object_id=key,
        request_id=payload_hash,
        details=details,
    ))
    db.commit()


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
            _record_idempotency_event(
                db,
                client_id=client_id,
                key=key,
                payload_hash=payload_hash,
                action="idempotency.conflict",
                details={
                    "error_code": "IDEMPOTENCY_KEY_CONFLICT",
                    "operation_scope": key.split(":", 1)[0],
                    "original_request_hash": existing.request_hash,
                    "conflicting_request_hash": payload_hash,
                },
            )
            raise IdempotencyKeyConflictError(key)
        _record_idempotency_event(
            db,
            client_id=client_id,
            key=key,
            payload_hash=payload_hash,
            action="idempotency.replay",
            details={
                "operation_scope": key.split(":", 1)[0],
                "response_status": existing.response_status,
            },
        )
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
