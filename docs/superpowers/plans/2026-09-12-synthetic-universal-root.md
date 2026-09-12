# Synthetic Universal Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the typed TestGraph `is_a` graph exactly one non-semantic root, `.`, with every semantic type reachable from it.

**Architecture:** Store `.` as one reserved `SubjectType` distinguished by a synthetic marker and stable UUID. A focused root service owns creation, topology maintenance, mutation guards, and integrity reporting; semantic writes and progressive navigation call that service while all non-`is_a` relationships remain independent. The live page consumes the real stored graph but visually promotes the root's children.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, pytest, vanilla JavaScript.

**Spec:** User-approved Work brief in the 2026-09-12 conversation, revised to require no existing-data backfill.

## Global Constraints

- `.` is infrastructure and has one stable reserved ID.
- No ordinary subject, classification decision, deliberation, alias, rename, or delete may target `.`.
- Every typed `is_a` semantic type must be reachable from `.`.
- `part_of` and other relationship meanings stay independent.
- Progressive traversal starts at `.` and remains bounded and paginated.
- Do not introduce named-type or domain-specific rules.
- Existing test data does not require migration or backfill.

---

### Task 1: Persistent root identity and lifecycle

**Files:**
- Create: `app/services/synthetic_root.py`
- Create: `alembic/versions/0024_synthetic_universal_root.py`
- Modify: `app/models/v2.py`
- Test: `tests/test_synthetic_root.py`

**Interfaces:**
- Produces: `SYNTHETIC_ROOT_ID`, `ensure_synthetic_root(db, commit=True)`, `is_synthetic_root(type_)`, and guard helpers.

- [ ] Write tests proving one stable synthetic root is created idempotently and cannot be used as ordinary vocabulary.
- [ ] Run the focused tests and verify they fail because the feature is absent.
- [ ] Add the schema marker, structural migration, constant, creation service, and guard helpers.
- [ ] Run the focused tests and verify they pass.

### Task 2: Taxonomy maintenance and integrity

**Files:**
- Modify: `app/services/synthetic_root.py`
- Modify: `app/services/semantic.py`
- Modify: `app/services/v2.py`
- Test: `tests/test_synthetic_root.py`
- Test: `tests/test_typed_classification_mode.py`

**Interfaces:**
- Produces: `attach_root_if_needed(db, type_, commit=True)` and `check_synthetic_root_integrity(db)`.
- Consumes: typed `is_a` relationship writes and `ensure_subject_type` creation.

- [ ] Write failing tests for automatic root attachment, semantic-parent replacement, unchanged descendants, typed multi-parent preservation, and orphan detection.
- [ ] Run the focused tests and verify the expected failures.
- [ ] Maintain the root edge transactionally at semantic-type creation and `is_a` edge changes.
- [ ] Implement bounded graph-integrity reporting from the reserved root.
- [ ] Run the focused tests and verify they pass.

### Task 3: Classification and progressive-navigation boundaries

**Files:**
- Modify: `app/services/vocabulary_navigation.py`
- Modify: `app/services/classification.py`
- Modify: `app/services/deliberation.py`
- Modify: `app/api/mcp_v2.py`
- Test: `tests/test_synthetic_root.py`
- Test: `tests/test_vocabulary_navigation.py`
- Test: `tests/test_progressive_vocabulary_workflows.py`
- Test: `tests/test_deliberations.py`

**Interfaces:**
- `list_root_subject_types` returns `.` as the sole typed taxonomy root.
- `list_child_subject_types('.', relationship='is_a')` returns only the immediate paginated branches.

- [ ] Write failing boundary tests rejecting subjects, decisions, and deliberations involving `.` and proving bounded traversal begins there.
- [ ] Run the focused tests and verify the expected failures.
- [ ] Apply root guards at classification and deliberation entry points and update MCP navigation guidance.
- [ ] Run focused classification, navigation, MCP, and deliberation tests.

### Task 4: Live hierarchy, documentation, and complete verification

**Files:**
- Modify: `app/static/live.html`
- Modify: `README.md`
- Modify: `docs/TASTEGRAPH_V2.md`
- Modify: `CHANGELOG.md`
- Test: `tests/test_live_page.py`

**Interfaces:**
- The API retains `.`, while `renderClassificationTree` promotes its direct taxonomy children and keeps branches collapsed by default.

- [ ] Write a failing live-page behaviour test for synthetic-root presentation and default collapse.
- [ ] Run the focused test and verify the expected failure.
- [ ] Update the live renderer and relevant architecture documentation.
- [ ] Run all focused tests, then `.venv/bin/pytest -q`.
- [ ] Review the diff against all ten requested invariants.
- [ ] Commit and push only after fresh full-suite verification succeeds.
