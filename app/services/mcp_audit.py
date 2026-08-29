from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import OAuthClient
from app.models.workflow import McpInteraction


_SECRET_KEYS = {
    "authorization", "token", "access_token", "refresh_token", "api_key", "apikey",
    "password", "secret", "client_secret", "connection_secret", "oauth_code", "code",
    "version_check",
}
_LARGE_TEXT_KEYS = {
    "raw_text", "summary", "headline", "reason", "conclusion", "analysis", "prompt",
    "message", "content", "provenance", "evidence", "submitted_data", "structured_data",
}
_SAFE_TEXT_LIMIT = 160


def _key(name: Any) -> str:
    return str(name or "").strip().lower()


def _summarise_text(value: str) -> dict:
    return {"redacted_text": True, "type": "string", "length": len(value)}


def _summarise_payload(value: Any) -> dict:
    if isinstance(value, dict):
        return {"redacted_payload": True, "type": "object", "keys": sorted(str(k) for k in value.keys())[:30]}
    if isinstance(value, list):
        return {"redacted_payload": True, "type": "array", "length": len(value)}
    if isinstance(value, str):
        return _summarise_text(value)
    return {"redacted_payload": True, "type": type(value).__name__}


def compact_mcp_interaction_result(summary: Any) -> dict:
    """Return a fixed-size summary for interaction-log inspection results."""
    if not isinstance(summary, dict):
        return {"type": type(summary).__name__}
    items = summary.get("items")
    if isinstance(items, list):
        item_count = len(items)
    elif isinstance(items, dict) and items.get("type") == "array":
        item_count = int(items.get("length") or 0)
    else:
        item_count = 0
    return {
        "count": summary.get("count", item_count),
        "items": {"redacted_payload": True, "type": "array", "length": item_count},
        "privacy": summary.get("privacy"),
    }


def redact_arguments(payload: Any, *, parent_key: str | None = None) -> Any:
    if isinstance(payload, dict):
        result = {}
        for key, value in payload.items():
            normalised = _key(key)
            if normalised in _SECRET_KEYS or normalised.endswith("_token") or normalised.endswith("_secret"):
                result[str(key)] = "[redacted]"
            elif normalised in _LARGE_TEXT_KEYS:
                result[str(key)] = _summarise_payload(value)
            else:
                result[str(key)] = redact_arguments(value, parent_key=normalised)
        return result
    if isinstance(payload, list):
        if len(payload) > 30:
            return {"redacted_payload": True, "type": "array", "length": len(payload)}
        return [redact_arguments(item, parent_key=parent_key) for item in payload]
    if isinstance(payload, str) and len(payload) > _SAFE_TEXT_LIMIT:
        return _summarise_text(payload)
    return payload


def _result_summary(result: Any, *, tool_name: str | None = None) -> dict:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        if tool_name == "list_my_mcp_interactions":
            return compact_mcp_interaction_result(structured)
        return redact_arguments(structured)
    summary = {}
    if result.get("isError"):
        summary["isError"] = True
    content = result.get("content")
    if isinstance(content, list):
        summary["content_items"] = len(content)
    return summary


def _normalise_client_family(client_name: str | None) -> str | None:
    value = str(client_name or "").strip().casefold()
    if not value:
        return None
    if "chatgpt" in value or "openai" in value:
        return "chatgpt"
    if "claude" in value or "anthropic" in value:
        return "claude"
    if "cursor" in value:
        return "cursor"
    if "copilot" in value or "github" in value:
        return "github_copilot"
    return value[:120]


def _attribution_summary(db: Session, *, client_id: str | None, source_model: str | None) -> dict:
    client_name = None
    if client_id:
        client = db.get(OAuthClient, client_id)
        if client is not None:
            client_name = client.client_name
    family = _normalise_client_family(client_name)
    return {
        "oauth_client_id": client_id,
        "oauth_client_name": client_name,
        "client_family": family,
        "source_model": source_model,
        "source_model_source": "tool_argument" if source_model else "not_reported",
        "model_attribution_status": "reported" if source_model else "unknown",
        "note": (
            "Exact model identity is caller-reported; OAuth client identity is server-resolved."
            if source_model
            else "OAuth client identity is server-resolved; this call did not report an exact model identity."
        ),
    }


def record_mcp_interaction(
    db: Session,
    *,
    request_id: str | None,
    user_id,
    client_id: str | None,
    source_model: str | None,
    tool_name: str,
    arguments: dict,
    result: Any,
    outcome: str,
    latency_ms: int | None,
    server_version: str | None,
    build_sha: str | None,
    workflow_run_id=None,
    workflow_step: str | None = None,
) -> None:
    """Best-effort structured telemetry; failures must never block the domain call."""
    try:
        arguments_summary = redact_arguments(arguments)
        if not isinstance(arguments_summary, dict):
            arguments_summary = {"payload": arguments_summary}
        arguments_summary["_attribution"] = _attribution_summary(
            db, client_id=client_id, source_model=source_model
        )
        row = McpInteraction(
            request_id=request_id,
            user_id=user_id,
            client_id=client_id,
            source_model=source_model,
            tool_name=tool_name,
            workflow_run_id=workflow_run_id,
            workflow_step=workflow_step,
            arguments_summary=arguments_summary,
            result_summary=_result_summary(result, tool_name=tool_name),
            outcome=outcome,
            latency_ms=latency_ms,
            server_version=server_version,
            build_sha=build_sha,
        )
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
