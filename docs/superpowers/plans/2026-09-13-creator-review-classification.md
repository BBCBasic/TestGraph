# Creator–Reviewer Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confirm or dispute a provisional subject after the first independent post-creation classification review, without returning to the creating AI.

**Architecture:** Store the creation proposal as immutable subject provenance, separate from formal decision rows. Classification settlement compares the first independent review against that proposal and uses server-observed OAuth client IDs as the enforceable independence boundary. Durable workflow metadata remains the handoff contract, with explicit follow-up guidance on both subject write tools.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Alembic, pytest, MCP tool schemas

**Spec:** `docs/superpowers/specs/2026-09-13-creator-review-classification-design.md`

## Global Constraints

- Creation is a proposal, not a formal `subject_classification_decisions` vote.
- One later independent review settles the classification: agreement confirms; disagreement disputes.
- OAuth client identity is server-resolved; `source_model` remains caller-reported audit metadata.
- Changing only `source_model` from the creator client must not establish independence.
- Existing test records may be backfilled from durable workflow evidence; no general historical migration guarantee is required.

---

### Task 1: Persist and expose creation proposals

**Files:**
- Create: `app/services/classification_proposals.py`
- Create: `alembic/versions/0023_backfill_classification_proposals.py`
- Modify: `app/services/v2.py`
- Modify: `app/api/mcp_v2.py`
- Test: `tests/test_subject_classification_convergence.py`

**Interfaces:**
- Produces: `record_classification_proposal(subject, *, source_client, source_model=None, identity_basis="authenticated_client") -> dict`
- Produces: `classification_proposal(subject) -> dict`
- Extends: `ensure_subject(..., source_model: str | None = None)`

- [ ] Write failing tests proving a new subject stores proposal type/client/model metadata while the decision table remains empty, and classification state exposes a separate `creation_proposal` object.
- [ ] Run `pytest tests/test_subject_classification_convergence.py -q` and confirm the new assertions fail because proposal metadata is absent.
- [ ] Implement the focused proposal helper and initialise it only when `ensure_subject` creates a subject.
- [ ] Add optional `source_model` to `save_experience` and `enrich_subject` schemas and thread it into subject creation without treating it as trusted identity.
- [ ] Add a data-only Alembic revision that fills missing proposal provenance from each subject's earliest workflow event client, marking the identity basis `workflow_backfill`; otherwise mark it `legacy_unknown`.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Settle on the first independent review

**Files:**
- Modify: `app/services/classification.py`
- Modify: `app/services/workflows.py`
- Test: `tests/test_subject_classification_convergence.py`
- Test: `tests/test_enrich_subject_workflow.py`

**Interfaces:**
- Consumes: `classification_proposal(subject)` from Task 1.
- Produces: first independent agreement -> `confirmed`; first independent disagreement -> `disputed`.

- [ ] Write failing tests for independent agreement, independent disagreement, creator self-review with a changed model label, and replay of an existing candidate decision after the lifecycle upgrade.
- [ ] Run the focused tests and confirm they fail with the old candidate/awaiting-second-model behaviour.
- [ ] Change classification settlement to compare formal decisions with the creation proposal and enforce a different `source_client` when proposal attribution is known.
- [ ] Mark an agreeing decision `confirmed`, lock the subject and type, and mark a disagreeing decision `candidate` while setting the subject `disputed`.
- [ ] Reconcile an idempotently replayed existing decision so Rob's already-recorded review can settle after deployment.
- [ ] Remove the obsolete normal transition to `awaiting_second_model`; retain compatibility when reading historical workflow rows.
- [ ] Run both focused test modules and confirm they pass.

### Task 3: Make post-save continuation explicit

**Files:**
- Modify: `app/services/mcp_v2_guidance_policy.py`
- Modify: `app/services/workflows.py`
- Test: `tests/test_enrichment_classification_handoff.py`
- Test: `tests/test_enrich_subject_workflow.py`

**Interfaces:**
- Produces: `workflow_action_required: bool` in every workflow body.
- Produces: matching mandatory follow-up descriptions for `save_experience` and `enrich_subject`.

- [ ] Write failing tests that both write tools instruct clients to follow `workflow.next_action` and that pending/completed workflow bodies expose true/false `workflow_action_required`.
- [ ] Run the focused tests and confirm `save_experience` and the boolean assertions fail.
- [ ] Apply the workflow guidance to both tool definitions and add the boolean to `workflow_body`.
- [ ] Run the focused tests and confirm they pass.

### Task 4: Verify, deploy, and validate Rob

**Files:**
- Modify if needed: `CHANGELOG.md`

**Interfaces:**
- Deployment target: GitHub `BBCBasic/TestGraph`, branch `master`.
- Live subject: `3cf17e47-1b6f-442e-aaba-8401d95d48df`.

- [ ] Add a concise changelog entry describing the creator–reviewer lifecycle and post-save handoff.
- [ ] Run `pytest -q` and require zero failures.
- [ ] Run `git diff --check` and inspect `git status --short`.
- [ ] Commit the implementation with a focused message and push `master` to GitHub.
- [ ] Poll `get_server_info` until `build_sha` matches the pushed commit.
- [ ] Have the actual `gpt-5.6-sol` execution inspect Rob and replay its existing affirmation through the live V2 tool.
- [ ] Fetch Rob's classification and workflow again; require `confirmed`, non-null `locked_at`, creation proposal attribution, the formal decision marked confirmed, and the workflow completed.
