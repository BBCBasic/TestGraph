# Kill-Test Model Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repeatable model-execution layer for the frozen TestGraph kill-test corpus without changing production TestGraph behavior.

**Architecture:** Keep provider calls and benchmark regime logic under `benchmarks/`. OpenAI and Anthropic are called through `httpx` using environment-provided API keys/model IDs. The runner executes `single` and `simple` end-to-end and collects the two blind first decisions needed for later isolated TestGraph replay; it never writes benchmark subjects into the normal graph.

**Tech Stack:** Python 3, `httpx`, dataclasses, argparse, pytest.

**Spec:** `benchmarks/KILL_TEST.md`

## Global Constraints

- Do not modify production API, database, MCP, workflow, resolver, or guidance behavior.
- Do not change the frozen answer key in `benchmarks/kill_cases.json`.
- Independent first decisions must not see one another.
- Provider secrets come only from environment variables.
- Raw provider responses are retained separately from scored JSONL output.
- A 10-case shakedown is for runner validation only, not for judging TestGraph.

---

### Task 1: Provider-neutral decision interface

**Files:**
- Create: `benchmarks/providers.py`
- Test: `tests/test_kill_test_runner.py`

**Interfaces:**
- Produces: `ModelReply`, `ModelProvider`, `parse_classification_reply`, `OpenAIProvider`, `AnthropicProvider`.

- [ ] Write tests for strict/tolerant JSON parsing and provider response extraction.
- [ ] Verify tests fail while the module is absent.
- [ ] Implement the minimal provider-neutral types and HTTP adapters.
- [ ] Verify targeted tests pass.

### Task 2: Regime runner

**Files:**
- Create: `benchmarks/runner.py`
- Test: `tests/test_kill_test_runner.py`

**Interfaces:**
- Consumes: `BenchmarkCase`, `ModelProvider`.
- Produces: `run_single_case`, `run_simple_case`, `collect_testgraph_case`, `run_cases`.

- [ ] Write tests proving blind independence, agreement behavior, resolver behavior, failure accounting and deterministic shakedown selection.
- [ ] Verify tests fail before implementation.
- [ ] Implement the minimal regime functions.
- [ ] Verify targeted tests pass.

### Task 3: CLI and audit output

**Files:**
- Create: `scripts/run_kill_test.py`
- Modify: `benchmarks/KILL_TEST.md`
- Test: `tests/test_kill_test_runner.py`

**Interfaces:**
- Produces scored JSONL for `single`/`simple`, raw JSONL decision bundles for `testgraph-collect`, plus an audit JSONL containing provider responses and usage.

- [ ] Add CLI argument/serialization tests.
- [ ] Implement model/environment validation, `--limit`, `--case-id`, and `--shakedown 10`.
- [ ] Document exact commands and environment variables.
- [ ] Run targeted tests and full CI before merge.
