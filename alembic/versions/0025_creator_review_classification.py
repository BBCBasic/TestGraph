"""Backfill creator proposals and key classification decisions by OAuth client.

Revision ID: 0025_creator_reviewer
Revises: 0024_synthetic_universal_root
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0025_creator_reviewer"
down_revision = "0024_synthetic_universal_root"
branch_labels = None
depends_on = None


subjects = sa.table(
    "v2_subjects",
    sa.column("id", sa.Uuid()),
    sa.column("subject_type_id", sa.Uuid()),
    sa.column("provenance_json", sa.JSON()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
workflow_runs = sa.table(
    "workflow_runs",
    sa.column("id", sa.Uuid()),
    sa.column("subject_id", sa.Uuid()),
)
workflow_events = sa.table(
    "workflow_events",
    sa.column("workflow_run_id", sa.Uuid()),
    sa.column("actor_client", sa.String()),
    sa.column("actor_model", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
mcp_interactions = sa.table(
    "mcp_interactions",
    sa.column("workflow_run_id", sa.Uuid()),
    sa.column("client_id", sa.String()),
    sa.column("tool_name", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
classification_decisions = sa.table(
    "subject_classification_decisions",
    sa.column("id", sa.Uuid()),
    sa.column("subject_id", sa.Uuid()),
    sa.column("classification_version", sa.Integer()),
    sa.column("source_model", sa.String()),
    sa.column("source_client", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


_KNOWN_PROTOCOL_SUFFIXES = frozenset({"v1", "v2", "v3"})


def _legacy_decision_client_identity(source_client: str | None) -> str | None:
    """Remove the one transport tag added by the legacy decision writer.

    This helper is deliberately limited to the decision column populated by that
    writer. Workflow actors already contain the raw authenticated principal ID.
    """
    if source_client is None:
        return None
    client_id, separator, suffix = source_client.rpartition(":")
    if separator and client_id and suffix.casefold() in _KNOWN_PROTOCOL_SUFFIXES:
        return client_id
    return source_client


def _remove_duplicate_decisions(identity_column) -> None:
    """Keep the earliest durable decision for each round and trusted identity."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            classification_decisions.c.id,
            classification_decisions.c.subject_id,
            classification_decisions.c.classification_version,
            identity_column,
        ).order_by(
            classification_decisions.c.created_at,
            classification_decisions.c.id,
        )
    ).mappings()
    seen = set()
    duplicate_ids = []
    for row in rows:
        key = (row["subject_id"], row["classification_version"], row[identity_column.name])
        if key in seen:
            duplicate_ids.append(row["id"])
        else:
            seen.add(key)
    if duplicate_ids:
        bind.execute(
            classification_decisions.delete().where(classification_decisions.c.id.in_(duplicate_ids))
        )


def _backfill_creation_proposals() -> None:
    bind = op.get_bind()
    for subject in bind.execute(sa.select(subjects)).mappings():
        provenance = dict(subject["provenance_json"] or {})
        if isinstance(provenance.get("classification_proposal"), dict):
            continue

        audited_creator = bind.execute(
            sa.select(mcp_interactions.c.client_id)
            .select_from(mcp_interactions.join(
                workflow_runs,
                mcp_interactions.c.workflow_run_id == workflow_runs.c.id,
            ))
            .where(
                workflow_runs.c.subject_id == subject["id"],
                mcp_interactions.c.client_id.is_not(None),
                mcp_interactions.c.tool_name.in_(("save_experience", "enrich_subject")),
            )
            .order_by(mcp_interactions.c.created_at, mcp_interactions.c.workflow_run_id)
            .limit(1)
        ).scalar_one_or_none()
        earliest_event = bind.execute(
            sa.select(workflow_events.c.actor_client, workflow_events.c.actor_model)
            .join(workflow_runs, workflow_runs.c.id == workflow_events.c.workflow_run_id)
            .where(
                workflow_runs.c.subject_id == subject["id"],
                workflow_events.c.actor_client.is_not(None),
            )
            .order_by(workflow_events.c.created_at, workflow_events.c.workflow_run_id)
            .limit(1)
        ).mappings().first()
        source_client = audited_creator or (earliest_event["actor_client"] if earliest_event else None)
        provenance["classification_proposal"] = {
            "proposed_type_id": str(subject["subject_type_id"]),
            "source_client": source_client,
            "source_model": earliest_event["actor_model"] if earliest_event else None,
            "identity_basis": (
                "workflow_audit_backfill" if audited_creator
                else "workflow_backfill" if source_client
                else "legacy_unknown"
            ),
            "proposed_at": subject["created_at"].isoformat(),
        }
        bind.execute(
            subjects.update().where(subjects.c.id == subject["id"]).values(
                provenance_json=provenance,
            )
        )


def _normalize_legacy_decision_clients() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.select(
        classification_decisions.c.id,
        classification_decisions.c.source_client,
    )).mappings()
    for row in rows:
        normalized = _legacy_decision_client_identity(row["source_client"])
        if normalized != row["source_client"]:
            bind.execute(
                classification_decisions.update()
                .where(classification_decisions.c.id == row["id"])
                .values(source_client=normalized)
            )


def upgrade() -> None:
    _backfill_creation_proposals()
    _normalize_legacy_decision_clients()
    _remove_duplicate_decisions(classification_decisions.c.source_client)
    with op.batch_alter_table("subject_classification_decisions") as batch:
        batch.drop_constraint("uq_subject_classification_model_version", type_="unique")
        batch.create_unique_constraint(
            "uq_subject_classification_client_version",
            ["subject_id", "classification_version", "source_client"],
        )


def downgrade() -> None:
    _remove_duplicate_decisions(classification_decisions.c.source_model)
    with op.batch_alter_table("subject_classification_decisions") as batch:
        batch.drop_constraint("uq_subject_classification_client_version", type_="unique")
        batch.create_unique_constraint(
            "uq_subject_classification_model_version",
            ["subject_id", "classification_version", "source_model"],
        )
    # Proposal provenance is intentionally preserved as durable audit data.
