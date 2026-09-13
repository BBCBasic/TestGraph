"""Repair legacy creator proposals from raw MCP audit identities.

Revision ID: 0026_repair_creator_identity
Revises: 0025_creator_reviewer
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026_repair_creator_identity"
down_revision = "0025_creator_reviewer"
branch_labels = None
depends_on = None


subjects = sa.table(
    "v2_subjects",
    sa.column("id", sa.Uuid()),
    sa.column("provenance_json", sa.JSON()),
)
workflow_runs = sa.table(
    "workflow_runs",
    sa.column("id", sa.Uuid()),
    sa.column("subject_id", sa.Uuid()),
)
mcp_interactions = sa.table(
    "mcp_interactions",
    sa.column("workflow_run_id", sa.Uuid()),
    sa.column("client_id", sa.String()),
    sa.column("tool_name", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def _repair_creator_identities() -> None:
    bind = op.get_bind()
    for subject in bind.execute(sa.select(subjects)).mappings():
        provenance = dict(subject["provenance_json"] or {})
        proposal = provenance.get("classification_proposal")
        if not isinstance(proposal, dict) or proposal.get("identity_basis") != "workflow_backfill":
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
        if not audited_creator:
            continue
        repaired = dict(proposal)
        repaired["source_client"] = audited_creator
        repaired["identity_basis"] = "workflow_audit_backfill"
        provenance["classification_proposal"] = repaired
        bind.execute(
            subjects.update().where(subjects.c.id == subject["id"]).values(
                provenance_json=provenance,
            )
        )


def upgrade() -> None:
    _repair_creator_identities()


def downgrade() -> None:
    pass
