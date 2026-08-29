# Workflow Orchestration and MCP Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add durable server-side workflow orchestration and structured MCP interaction auditing, proving the architecture by orchestrating `enrich_subject` through classification convergence.

**Architecture:** Add generic workflow/audit persistence models and a migration, a focused workflow service that advances `enrich_subject` state from existing classification truth, and a best-effort MCP audit service with redaction. Existing enrichment and classification domain services remain authoritative; orchestration wraps them rather than duplicating their logic.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL/SQLite tests, MCP JSON-RPC, pytest.

**Spec:** `docs/superpowers/specs/2026-08-29-workflow-orchestration-and-mcp-audit-design.md`

## Global Constraints

- Server owns procedure; AI owns reasoning.
- No database transaction may remain open while awaiting another AI.
- Raw conversations are not stored by default.
- MCP audit logging must redact secrets and large free-text payloads.
- Audit failure must not block valid domain operations.
- Existing classification convergence tables remain authoritative.
- Existing idempotency and version-safety behaviour remains in force.

---

### Task 1: Persistence models and migration

**Files:**
- Modify: `app/models/v2.py`
- Create: `alembic/versions/0022_workflows_and_mcp_audit.py`
- Test: `tests/test_workflow_models.py`

**Interfaces:**
- Produces ORM models `WorkflowRun`, `WorkflowEvent`, `McpInteraction`.

- [ ] Write failing model tests that create a workflow run with events and an MCP interaction using SQLite metadata.
- [ ] Run `pytest tests/test_workflow_models.py -q` and confirm failure because models do not exist.
- [ ] Add the three focused ORM models with JSON portability via existing `JsonType` and UUID portability via existing `UuidType`.
- [ ] Add Alembic revision `0022` with foreign keys and indexes for workflow type/state, subject, tool/outcome and timestamps.
- [ ] Re-run the focused tests.
- [ ] Commit.

### Task 2: MCP audit redaction and persistence service

**Files:**
- Create: `app/services/mcp_audit.py`
- Test: `tests/test_mcp_audit.py`

**Interfaces:**
- Produces `redact_arguments(payload: dict) -> dict`.
- Produces `record_mcp_interaction(...) -> None` as best-effort telemetry.

- [ ] Write failing tests proving secrets (`authorization`, `token`, `password`, `api_key`, OAuth codes, `version_check`) are never retained.
- [ ] Write failing tests proving large/free-text fields are summarised by type/length rather than copied.
- [ ] Implement deterministic recursive redaction with safe scalar identifiers retained where useful.
- [ ] Implement interaction persistence with outcome, latency, server/build identity and optional workflow references.
- [ ] Ensure persistence exceptions are caught/rolled back without propagating into the domain operation.
- [ ] Run focused tests and commit.

### Task 3: Generic workflow service

**Files:**
- Create: `app/services/workflows.py`
- Test: `tests/test_enrich_subject_workflow.py`

**Interfaces:**
- Produces `start_or_resume_enrichment_workflow(db, subject, owner_id, actor_client) -> WorkflowRun`.
- Produces `sync_enrichment_classification_workflow(db, subject, actor_client, actor_model=None) -> WorkflowRun | None`.
- Produces `workflow_body(run) -> dict`.

- [ ] Write failing tests for confirmed subjects completing immediately after enrichment.
- [ ] Write failing tests for provisional subjects entering `classification_review_required`.
- [ ] Write failing tests for one current-round decision entering `awaiting_second_model`.
- [ ] Write failing tests for conflicting decisions entering `disputed`.
- [ ] Write failing tests for confirmed convergence entering `completed`.
- [ ] Implement the service by deriving workflow state from the existing authoritative classification state and current-version decisions.
- [ ] Append workflow events only on meaningful transitions.
- [ ] Run focused tests and commit.

### Task 4: Orchestrate `enrich_subject`

**Files:**
- Modify: `app/api/mcp_v2.py`
- Test: `tests/test_enrichment_classification_handoff.py`
- Test: `tests/test_enrich_subject_workflow.py`

**Interfaces:**
- `enrich_subject` response gains `workflow`.

- [ ] Extend tests so a successful enrichment returns a durable workflow ID/state/next action.
- [ ] Ensure the workflow is created in the same transaction as successful enrichment/idempotency completion.
- [ ] Return explicit next action: classification decision, second-model wait, or completed.
- [ ] Preserve all existing response fields and idempotency behaviour.
- [ ] Run focused tests and commit.

### Task 5: Resume workflow from classification writes

**Files:**
- Modify: `app/api/mcp_v2.py`
- Modify: `app/services/workflows.py`
- Test: `tests/test_enrich_subject_workflow.py`

**Interfaces:**
- Successful `affirm_subject_classification` / `propose_subject_reclassification` responses gain optional `workflow` when an active enrichment workflow exists.

- [ ] Write failing tests showing first decision -> `awaiting_second_model`.
- [ ] Write failing tests showing second agreeing model -> `completed` with confirmed/locked classification.
- [ ] Write failing tests showing disagreement -> `disputed`.
- [ ] Integrate workflow synchronisation immediately after successful classification domain writes.
- [ ] Run focused tests and commit.

### Task 6: Instrument MCP `tools/call`

**Files:**
- Modify: `app/api/mcp_v2.py`
- Test: `tests/test_mcp_audit.py`

**Interfaces:**
- Every authenticated `tools/call` attempt creates best-effort structured telemetry.

- [ ] Write failing tests for successful and validation-error tool calls.
- [ ] Add start timestamp before dispatch and outcome classification after dispatch.
- [ ] Resolve optional workflow metadata from response/arguments where available.
- [ ] Persist redacted request/result summaries after the domain call without changing the domain result.
- [ ] Verify telemetry failure does not change a successful tool response.
- [ ] Run focused tests and commit.

### Task 7: Full verification and PR

**Files:**
- Review all changed files.

- [ ] Run full pytest suite on Python 3.11/3.12 via CI.
- [ ] Run V2 smoke workflow including `compileall`, full pytest and clean `alembic upgrade head`.
- [ ] Inspect PR diff for unrelated changes and accidental raw-data logging.
- [ ] Merge only when CI and smoke are green.
