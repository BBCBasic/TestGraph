# Pre-Enrichment Classification Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block ordinary writes to an existing unsettled subject until its creator-reviewer classification workflow is confirmed or resolved.

**Architecture:** Add a shared workflow preflight that durably starts or resumes classification but does not consume the requested operation's idempotency key. Invoke it after read-only subject resolution and before mutation in enrichment, correction, location assertion, and existing-subject experience saves; retain finalization hooks for successful writes.

**Tech Stack:** Python 3, FastAPI MCP handlers, SQLAlchemy, pytest

**Spec:** `docs/superpowers/specs/2026-09-13-pre-enrichment-classification-gate-design.md`

## Global Constraints

- Apply the gate only to an already-existing primary subject.
- Allow new-subject creation to include classification-relevant identifiers and attributes.
- Preserve creator-reviewer agreement, disagreement, and resolver behaviour.
- Do not persist the blocked business mutation or its `IdempotencyRecord`.
- Permit retry with the same deterministic key after classification settlement.
- Add no domain-specific type or attribute rules.
- No database migration is required for the test dataset.

---

### Task 1: Specify the shared preflight contract with failing tests

**Files:**
- Create: `tests/test_pre_enrichment_classification_gate.py`
- Modify: `tests/test_subject_enrichment_integrity.py`
- Modify: `tests/test_location_assertions.py`

**Interfaces:**
- Exercises: `_enrich_subject`, `_correct_subject_fact`, `_save_experience`, and `_assert_location` through real in-memory SQLAlchemy sessions.
- Requires: blocked response fields `error_code`, `details.code`, `details.subject_id`, `details.classification_status`, and `details.workflow`.

- [ ] Write `test_existing_provisional_subject_blocks_enrichment_before_any_mutation` with a literal `colour: silver/grey` fixture; assert `classification_review_required`, unchanged subject JSON, no context rows, and no enrichment idempotency row.
- [ ] Write `test_independent_agreement_then_same_key_retry_enriches_once` using `affirm_classification`; assert confirmed/locked state, one successful mutation, a completed workflow, and one idempotency record after a repeated replay.
- [ ] Write `test_different_type_dispute_blocks_enrichment_until_resolved` using a strict descendant proposal; assert disputed workflow and unchanged attributes before resolver settlement.
- [ ] Write `test_confirmed_existing_subject_enriches_without_review` and assert the normal success contract.
- [ ] Write `test_new_subject_save_can_include_classification_evidence` and assert `save_experience` creates the subject and initial attributes.
- [ ] Write `test_colour_correction_waits_for_classification_then_retries_once` and assert the final value is `red`, one correction audit entry exists, and workflow is complete.
- [ ] Write focused existing-subject tests for `save_experience` and `assert_location` to prove both block before their business rows are written.
- [ ] Update unrelated enrichment/location fixture subjects to `classification_status="confirmed"` so those tests continue testing their named behaviour rather than the new prerequisite.
- [ ] Run `python -m pytest tests/test_pre_enrichment_classification_gate.py tests/test_subject_enrichment_integrity.py tests/test_location_assertions.py -q` and confirm failures occur because the preflight contract does not exist.

### Task 2: Implement the pre-mutation workflow gate

**Files:**
- Modify: `app/services/workflows.py`
- Modify: `app/api/mcp_v2.py`

**Interfaces:**
- Produce: `preflight_existing_subject_mutation(db, subject, *, owner_id, actor_client) -> dict | None`.
- Produce: a durable workflow body when `classification_status != "confirmed"`; otherwise `None`.
- Consume: `start_or_resume_enrichment_workflow` and `workflow_body`.

- [ ] Implement `preflight_existing_subject_mutation` so confirmed subjects pass and every unsettled state starts/resumes the workflow, commits only that workflow checkpoint, and returns its body.
- [ ] Add `_classification_prerequisite_error(subject, workflow)` in `mcp_v2.py` that maps independent review to `classification_review_required`, disputes to `classification_resolution_required`, and includes the complete workflow body.
- [ ] Add `_preflight_existing_subject_mutation(...)` at the API boundary and call it in `_enrich_subject` immediately after `_locate_subject_for_write`, before enrichment validation or mutation.
- [ ] Call the same gate in `_correct_subject_fact` and `_assert_location` after read-only subject resolution and before their business writes.
- [ ] In `_save_experience`, resolve the subject type and query the exact non-deleted `(subject_type_id, canonical_key)` match before enrichment/context validation; gate a match and leave a miss on the existing creation path.
- [ ] Ensure an idempotency replay is checked before the gate, while a first blocked attempt leaves no operation record and can reuse its key later.
- [ ] Register successful correction and location-assertion finalization scopes with enough `subject_id` information to return a completed workflow body.
- [ ] Run the focused test command and make the new tests pass without weakening their assertions.

### Task 3: Publish the ordering contract

**Files:**
- Modify: `app/services/mcp_v2_guidance_policy.py`
- Modify: `app/api/mcp_v2.py`
- Modify: `tests/test_enrichment_classification_handoff.py`
- Modify: `tests/test_location_assertions.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Update: covered tool descriptions state that existing provisional subjects return a prerequisite before mutation and clients must not report completion.
- Update: `SERVER_VERSION` from `3.23.0-alpha` to `3.24.0-alpha`.

- [ ] Add failing description assertions for `enrich_subject`, `correct_subject_fact`, `save_experience`, and `assert_location` covering pre-write classification and safe same-key retry.
- [ ] Run `python -m pytest tests/test_enrichment_classification_handoff.py tests/test_location_assertions.py -q` and confirm the contract assertions fail.
- [ ] Apply concise shared guidance to all four tool descriptions and retain the existing post-write `workflow.next_action` language.
- [ ] Bump the server version and update exact version assertions.
- [ ] Add a changelog entry describing the generic pre-enrichment gate, atomic failure behaviour, and unchanged new-subject creation.
- [ ] Run the focused description/version tests and confirm they pass.

### Task 4: Verify, review, integrate, and push

**Files:**
- Review all changed production, test, and documentation files.

**Interfaces:**
- Deployment target: `BBCBasic/TestGraph`, branch `master`.

- [ ] Run all focused workflow, convergence, enrichment, correction, save, location, and idempotency tests.
- [ ] Run `python -m pytest -q` and require zero failures.
- [ ] Run `git diff --check`, inspect `git status --short`, and compare the implementation against every requirement in the approved specification.
- [ ] Commit the implementation with a focused message.
- [ ] Perform an independent diff review within the current agent because this session does not have user authorization to dispatch subagents.
- [ ] Merge `feature/pre-enrichment-classification-gate` into local `master`, rerun the full suite on the merged tree, and push `master` without force.
- [ ] Confirm the remote `master` SHA matches the verified local commit.
