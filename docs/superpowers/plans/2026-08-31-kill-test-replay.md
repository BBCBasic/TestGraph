# Kill Test Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay frozen blind TestGraph benchmark decisions through the real classification/dispute/resolver services in an isolated SQLite database and emit scoreable final server-state records.

**Architecture:** `benchmarks/replay.py` creates a benchmark-only SQLite database, materialises a generic benchmark root plus per-decision candidate subject types, and creates one namespaced subject per case. Frozen model decisions are submitted through `propose_reclassification`; the existing dispute hook is allowed to run normally. Final score records are derived from `classification_state`, never from model claims. `scripts/replay_kill_test.py` consumes the `testgraph-collect` JSONL file and writes scoreable `testgraph` JSONL plus an audit file.

**Tech Stack:** Python 3, SQLAlchemy, existing TestGraph service layer, pytest.

**Spec:** `benchmarks/KILL_TEST.md`

## Global Constraints

- Do not modify production API, MCP, resolver or classification behaviour.
- Do not write benchmark subjects into the normal application database.
- Use the existing classification and resolver services as the system under test.
- Treat unresolved disputes, semantic guard rejection and resolver failure as operational failures.
- Derive final type/status from server state only.

---

### Task 1: Replay engine

**Files:**
- Create: `benchmarks/replay.py`
- Test: `tests/test_kill_test_replay.py`

**Interfaces:**
- Consumes frozen `testgraph-collect` records.
- Produces `replay_case(db, case, collected) -> (score_record, audit)` and `replay_records(cases, collected_rows, database_url) -> (records, audits)`.

- [ ] Write failing tests for agreement, disagreement, semantic rejection and missing collection records.
- [ ] Verify tests fail because replay module is absent.
- [ ] Implement isolated subject/type materialisation and replay through `propose_reclassification`.
- [ ] Verify focused tests pass.

### Task 2: CLI and benchmark documentation

**Files:**
- Create: `scripts/replay_kill_test.py`
- Modify: `benchmarks/KILL_TEST.md`

**Interfaces:**
- Consumes collect JSONL and frozen cases.
- Produces scoreable TestGraph JSONL and audit JSONL.

- [ ] Add CLI validation and output writing.
- [ ] Document shakedown/full replay commands and isolated database behaviour.
- [ ] Run full CI and V2 smoke checks.
