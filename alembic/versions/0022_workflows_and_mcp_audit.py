"""Add durable workflows and MCP interaction audit.

Revision ID: 0022_workflows_and_mcp_audit
Revises: 0021_subject_classification_convergence
"""
from alembic import op
import sqlalchemy as sa


revision = "0022_workflows_and_mcp_audit"
down_revision = "0021_subject_classification_convergence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("workflow_type", sa.String(80), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("v2_subjects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("current_step", sa.String(80), nullable=False),
        sa.Column("required_actor", sa.String(80), nullable=True),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, columns in (
        ("ix_workflow_runs_workflow_type", ["workflow_type"]),
        ("ix_workflow_runs_owner_id", ["owner_id"]),
        ("ix_workflow_runs_subject_id", ["subject_id"]),
        ("ix_workflow_runs_state", ["state"]),
    ):
        op.create_index(name, "workflow_runs", columns)

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("step", sa.String(80), nullable=False),
        sa.Column("actor_client", sa.String(200), nullable=True),
        sa.Column("actor_model", sa.String(160), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflow_events_workflow_run_id", "workflow_events", ["workflow_run_id"])
    op.create_index("ix_workflow_events_event_type", "workflow_events", ["event_type"])
    op.create_index("ix_workflow_events_created_at", "workflow_events", ["created_at"])

    op.create_table(
        "mcp_interactions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("request_id", sa.String(200), nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("client_id", sa.String(200), nullable=True),
        sa.Column("source_model", sa.String(160), nullable=True),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("workflow_step", sa.String(80), nullable=True),
        sa.Column("arguments_summary", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("server_version", sa.String(80), nullable=True),
        sa.Column("build_sha", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_mcp_interactions_request_id", ["request_id"]),
        ("ix_mcp_interactions_user_id", ["user_id"]),
        ("ix_mcp_interactions_client_id", ["client_id"]),
        ("ix_mcp_interactions_source_model", ["source_model"]),
        ("ix_mcp_interactions_tool_name", ["tool_name"]),
        ("ix_mcp_interactions_workflow_run_id", ["workflow_run_id"]),
        ("ix_mcp_interactions_outcome", ["outcome"]),
        ("ix_mcp_interactions_created_at", ["created_at"]),
    ):
        op.create_index(name, "mcp_interactions", columns)


def downgrade() -> None:
    op.drop_table("mcp_interactions")
    op.drop_table("workflow_events")
    op.drop_table("workflow_runs")
