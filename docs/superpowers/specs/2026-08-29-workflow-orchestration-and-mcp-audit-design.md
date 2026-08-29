# Workflow Orchestration and MCP Audit Design

## Goal

Move V2 procedure ownership into durable server-side workflows while keeping semantic judgment with AI models, and add a separate structured MCP interaction audit stream so AI/server behaviour can be analysed without storing whole conversations by default.

## Governing rule

**Server owns procedure; AI owns reasoning.**

The server decides which step is next, what prerequisites are required, which actor/model may contribute, when a contribution is valid, when another independent model is required, and when a workflow is complete. AI models decide semantic questions such as what enrichment to add or which classification is justified.

## Scope

This first implementation is deliberately narrow:

1. Add a generic durable workflow persistence layer.
2. Add a generic structured MCP interaction audit log.
3. Use `enrich_subject` as the first orchestrated workflow.
4. Preserve existing classification convergence logic rather than replacing it.
5. Do not convert every V2 tool into a workflow yet.

## Workflow persistence

Two tables provide the reusable substrate.

### `workflow_runs`

Stores current durable state:

- `id`
- `workflow_type`
- `owner_id`
- `subject_id`
- `state`
- `current_step`
- `required_actor`
- `context_json`
- `created_at`
- `updated_at`
- `completed_at`

For the first workflow, states are:

- `enrichment_applied`
- `classification_review_required`
- `awaiting_second_model`
- `disputed`
- `completed`
- `blocked`

No database transaction remains open while waiting for another model.

### `workflow_events`

Append-only event history:

- `id`
- `workflow_run_id`
- `event_type`
- `step`
- `actor_client`
- `actor_model`
- `details_json`
- `created_at`

Events record transitions and model contributions without replacing the authoritative classification audit already stored in `subject_classification_decisions`.

## First workflow: `enrich_subject`

Existing enrichment validation and persistence remain authoritative. After successful enrichment, the server creates or updates an `enrich_subject` workflow run for that subject and records `enrichment_applied`.

The workflow then inspects classification state:

- confirmed classification -> workflow completes;
- provisional/candidate/disputed classification -> workflow moves to `classification_review_required` or `awaiting_second_model` depending on existing current-round decisions.

The enrichment response returns a `workflow` object containing the durable run ID, state, current step and next required action. This replaces the current implicit expectation that the AI infer what to do from prose alone.

Classification writes continue to use `affirm_subject_classification` and `propose_subject_reclassification`. When either succeeds, the server synchronises the active workflow for that subject:

- one current-round model decision -> `awaiting_second_model`;
- two agreeing decisions and classification confirmed -> `completed`;
- conflicting current-round decisions -> `disputed`.

This keeps one model's contribution atomic while making the cross-model wait durable.

## MCP audit stream

MCP interaction telemetry is separate from workflow state.

### `mcp_interactions`

Stores one structured row per `tools/call` attempt:

- `id`
- `request_id`
- `user_id`
- `client_id`
- `source_model`
- `tool_name`
- `workflow_run_id` when known
- `workflow_step` when known
- `arguments_summary`
- `result_summary`
- `outcome`
- `latency_ms`
- `server_version`
- `build_sha`
- `created_at`

Outcomes include `success`, `validation_error`, `policy_block`, `stale_version`, `server_error` and `auth_error` where applicable.

### Privacy/redaction

Raw conversations are not stored by default. The server logs structured summaries of MCP traffic only. The redactor removes or replaces values for keys such as authorization tokens, secrets, passwords, API keys, OAuth codes and `version_check`. Large free-text fields such as `raw_text`, `summary`, `reason`, `evidence`, `provenance` and similar payloads are represented by metadata such as presence, type, length and selected safe identifiers rather than copied wholesale.

This is designed for operational analysis, not surveillance or duplicate content storage.

## Analysis questions enabled

The audit stream should support later analysis such as:

- Which tools generate the most validation failures?
- How often do models attempt writes in the wrong sequence?
- How many MCP calls does an enrichment workflow take?
- Where do models stop before completing required procedure?
- Do different models diverge on classification decisions?
- Does server-side orchestration reduce retries and errors over time?

No dashboard is included in this first change; the data model is designed so one can be added later.

## Compatibility

Existing MCP tool names remain available. `enrich_subject` gains workflow metadata in its response. Classification tool behaviour remains compatible except that successful decisions additionally advance an active workflow when one exists.

Existing idempotency and deployment-version checks remain in force.

## Failure behaviour

Workflow bookkeeping must never make an otherwise valid domain write disappear silently. If workflow synchronisation fails inside the same server transaction, the domain write and workflow update roll back together so the client can retry safely under existing idempotency rules.

Audit logging is best-effort and isolated: a telemetry insert failure must not block a valid MCP domain operation.
