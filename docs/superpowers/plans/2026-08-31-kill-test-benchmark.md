# TestGraph Kill-Test Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a frozen, reproducible benchmark and scorer that compares TestGraph with simpler model-decision regimes without changing production behavior.

**Architecture:** Keep the benchmark as pure Python/data beside the application. The scorer consumes externally collected result records, compares them to a frozen answer key, calculates safety/efficiency metrics, and applies the precommitted kill threshold.

**Tech Stack:** Python standard library, pytest, JSON/JSONL.

**Spec:** `docs/superpowers/specs/2026-08-31-kill-test-benchmark-design.md`

## Global Constraints

- Do not change production API, persistence, workflow, resolver, or MCP behavior.
- Corpus contains exactly 120 frozen cases, 30 in each of four categories.
- Primary pass criterion is >=50% relative reduction in wrong canonicalisation versus `simple`.
- A zero-error simple baseline cannot establish superiority and therefore yields `ARCHIVE`.

---

### Task 1: Pure scoring core

**Files:**
- Create: `benchmarks/__init__.py`
- Create: `benchmarks/kill_test.py`
- Test: `tests/test_kill_test.py`

**Interfaces:**
- `load_cases(path) -> list[BenchmarkCase]`
- `evaluate_results(cases, records) -> dict[str, dict]`
- `build_report(regime_summaries) -> dict`
- `kill_verdict(simple_summary, testgraph_summary, reduction_target=0.5) -> dict`

- [x] Write failing tests for allowed types, aggregate rates, duplicate IDs, grouping/counters, compact corpus loading, and verdict threshold.
- [x] Run tests and verify missing-module/functions fail.
- [x] Implement minimal pure-Python scoring functions.
- [x] Run targeted tests and verify they pass.

### Task 2: Frozen corpus and protocol

**Files:**
- Create: `benchmarks/kill_cases.json`
- Create: `benchmarks/KILL_TEST.md`

- [x] Add exactly 30 easy controls.
- [x] Add exactly 30 semantic-head/modifier traps.
- [x] Add exactly 30 legitimate compounds that punish over-normalisation.
- [x] Add exactly 30 genuine ambiguity cases with explicit acceptable alternatives where justified.
- [x] Document independence, no-mid-run-repair, server-observed TestGraph state, and JSONL result contract.

### Task 3: Report CLI

**Files:**
- Create: `scripts/score_kill_test.py`

- [x] Parse the frozen case file and JSONL result records.
- [x] Produce machine-readable JSON to stdout and optional output file.
- [x] Include the precommitted kill verdict when both `simple` and `testgraph` results are present.

### Task 4: Verification

- [x] Run isolated benchmark tests locally.
- [x] Validate corpus size and category balance.
- [x] Run Python bytecode compilation on benchmark and CLI files.
- [ ] Run repository CI on the branch/PR.
