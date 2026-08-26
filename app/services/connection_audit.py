from __future__ import annotations

import os
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AuditEvent, OAuthClient


def record_oauth_connection(db: Session, *, user_id, client_id: str, resource: str, request_id: str | None = None) -> AuditEvent:
    """Record a successful fresh OAuth authorization-code connection.

    Refresh-token exchanges are intentionally not logged as new connections.
    No raw capability secret, OAuth token, authorization code or IP address is stored.
    """
    client = db.get(OAuthClient, client_id)
    build_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"
    deployment_id = os.getenv("RAILWAY_DEPLOYMENT_ID") or "unknown"
    event = AuditEvent(
        actor_id=str(user_id),
        client_id=client_id,
        action="oauth_connection",
        object_type="oauth_client",
        object_id=client_id,
        request_id=request_id or "oauth",
        details={
            "client_name": (client.client_name if client else None) or "MCP client",
            "resource": resource,
            "build_sha": build_sha,
            "deployment_id": deployment_id,
            "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT") or "unknown",
        },
    )
    db.add(event)
    return event


def recent_oauth_connections(db: Session, *, limit: int = 100) -> list[AuditEvent]:
    return list(db.scalars(
        select(AuditEvent)
        .where(AuditEvent.action == "oauth_connection")
        .order_by(AuditEvent.created_at.desc())
        .limit(max(1, min(limit, 500)))
    ))