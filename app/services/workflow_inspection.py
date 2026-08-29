from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.workflow import McpInteraction, WorkflowRun
from app.services.mcp_audit import compact_mcp_interaction_result
from app.services.workflows import workflow_body


def list_workflows(db: Session, *, owner_id, limit: int = 20) -> list[dict]:
    rows = list(db.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.owner_id == owner_id)
        .order_by(desc(WorkflowRun.updated_at), desc(WorkflowRun.created_at))
        .limit(max(1, min(int(limit), 100)))
    ).all())
    return [
        {
            **workflow_body(row),
            "subject_id": str(row.subject_id) if row.subject_id else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
        for row in rows
    ]


def list_mcp_interactions(db: Session, *, owner_id, limit: int = 50) -> list[dict]:
    rows = list(db.scalars(
        select(McpInteraction)
        .where(McpInteraction.user_id == owner_id)
        .order_by(desc(McpInteraction.created_at))
        .limit(max(1, min(int(limit), 200)))
    ).all())
    return [
        {
            "interaction_id": str(row.id),
            "request_id": row.request_id,
            "client_id": row.client_id,
            "source_model": row.source_model,
            "tool_name": row.tool_name,
            "workflow_run_id": str(row.workflow_run_id) if row.workflow_run_id else None,
            "workflow_step": row.workflow_step,
            "arguments_summary": row.arguments_summary,
            "result_summary": (
                compact_mcp_interaction_result(row.result_summary)
                if row.tool_name == "list_my_mcp_interactions"
                else row.result_summary
            ),
            "outcome": row.outcome,
            "latency_ms": row.latency_ms,
            "server_version": row.server_version,
            "build_sha": row.build_sha,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
