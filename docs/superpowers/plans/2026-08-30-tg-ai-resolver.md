# TG-AI Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dormant OpenAI-backed resolver that activates only on classification disputes and can choose only among existing candidate types.

**Architecture:** The existing classification service detects disputes and remains authoritative. A new resolver service is called after a dispute is committed, sends a bounded case to OpenAI's Responses API via `httpx`, validates strict JSON, and feeds the chosen candidate back through the existing classification decision mechanism. Resolver failures are non-fatal and leave the subject disputed.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Pydantic Settings, httpx, pytest

**Spec:** `docs/superpowers/specs/2026-08-30-tg-ai-resolver-design.md`

## Global Constraints

- Resolver is disabled by default.
- `OPENAI_API_KEY` is never committed.
- V1 resolver may choose only among target types already proposed in the current dispute.
- Resolver failures must not fail the originating classification request.
- No public third MCP in V1.

---

### Task 1: Resolver configuration and response validation

**Files:**
- Modify: `app/core/config.py`
- Create: `app/services/tg_ai_resolver.py`
- Create: `tests/test_tg_ai_resolver.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `ResolverDecision` Pydantic model and `resolve_classification_dispute(db, subject) -> dict | None`.

- [ ] **Step 1: Write failing tests**

Add tests that prove the resolver returns `None` when disabled or keyless, rejects an answer selecting a type outside the supplied candidate set, and accepts a valid strict response. Patch `httpx.Client.post` so no network request occurs.

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_tg_ai_resolver.py -v`
Expected: FAIL because `app.services.tg_ai_resolver` and resolver settings do not exist.

- [ ] **Step 3: Implement minimal resolver**

Add settings:
`tg_ai_resolver_enabled: bool = False`, `openai_api_key: str | None = None`, `tg_ai_resolver_model: str = "gpt-5-mini"`, `tg_ai_resolver_timeout_seconds: float = 20.0`.

Implement a focused resolver service that gathers current-version candidate decisions, serializes subject name/current type/candidate types/reasons/evidence, calls `POST https://api.openai.com/v1/responses`, asks for JSON matching `{target_subject_type, confidence, reason, action}`, parses the returned output text, and verifies the target is one of the current candidates.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_tg_ai_resolver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit config, service, tests, and `.env.example` together.

### Task 2: Classification dispute trigger

**Files:**
- Modify: `app/services/classification.py`
- Modify: `tests/test_tg_ai_resolver.py`

**Interfaces:**
- Consumes: `resolve_classification_dispute(db, subject)`.
- Produces: automatic resolver invocation only after the subject has been persisted as `disputed`.

- [ ] **Step 1: Write failing integration test**

Create two child types below one provisional parent, submit two independent reclassification decisions to different children, and patch `app.services.tg_ai_resolver.resolve_classification_dispute`. Assert the second call leaves normal workflow semantics intact and invokes the resolver once with the disputed subject.

- [ ] **Step 2: Run test to verify RED**

Run the specific new test and confirm it fails because no resolver hook is called.

- [ ] **Step 3: Implement minimal hook**

In `propose_reclassification`, record whether the current transaction created a dispute. Commit/refresh as today. After the commit, if a new dispute was created, call the resolver in a guarded `try/except` and log exceptions without changing the API result.

If the resolver returns a valid candidate, submit it through `propose_reclassification` using source model `tg-ai:<configured-model>` and source client `testgraph-resolver`. Avoid recursively invoking the resolver for the resolver's own decision.

- [ ] **Step 4: Run targeted tests**

Run: `pytest tests/test_tg_ai_resolver.py tests/test_classification_specificity_guard.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit the classification hook and integration test.

### Task 3: Regression verification

**Files:**
- No production changes unless a regression is found.

**Interfaces:**
- Verifies the complete repository behaviour.

- [ ] **Step 1: Run full test suite**

Run: `pytest -q`
Expected: 0 failures.

- [ ] **Step 2: Inspect branch diff**

Confirm no API key, unrelated refactor, public MCP surface, or database migration was added.

- [ ] **Step 3: Open PR and merge only after CI is green**

Create a PR against `master`, inspect CI, and merge when all checks pass.
