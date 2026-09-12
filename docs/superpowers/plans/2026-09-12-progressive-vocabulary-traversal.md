# Progressive Vocabulary Traversal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add bounded hierarchical vocabulary navigation so normal write classification and retrieval never require the complete vocabulary index.

**Architecture:** Add stateless SQLAlchemy navigation queries for roots, immediate children and active parent paths, then expose them additively through MCP and REST. Keep semantic ranking in the connected AI, encode bounded best-first/fallback behaviour in server guidance, and retain the full index only as an administrative compatibility tool.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic 2, pytest, SQLite for deterministic tests/benchmark, PostgreSQL-compatible production queries.

**Spec:** `docs/superpowers/specs/2026-09-12-progressive-vocabulary-traversal-design.md`

## Global Constraints

- Keep `vocabulary_index` available but remove it from every normal classification and retrieval instruction.
- Add functionality without changing existing save, search, hierarchy, classification or stable-ID contracts.
- Return only active `belongs_to` structure from navigation endpoints.
- Bound and paginate root/child responses; bind cursors to their operation and parent.
- Preserve semantic-head validation, aliases, provisional/candidate/confirmed/locked convergence, independent-model agreement, disputes, provenance, cycle prevention and identity collision safeguards.
- The AI ranks candidate branches; the server remains deterministic and model-independent.
- The scale benchmark defaults to at least 10,000 nodes and supports 50,000.

---

### Task 1: Vocabulary navigation service

**Files:**
- Create: `app/services/vocabulary_navigation.py`
- Create: `tests/test_vocabulary_navigation.py`

**Interfaces:**
- Consumes: `SubjectType`, `SubjectTypeAlias`, `TypeRelationship`, and `resolve_subject_type`.
- Produces: `list_root_subject_types(db, *, limit=50, cursor=None) -> dict`, `list_child_subject_types(db, parent, *, limit=50, cursor=None) -> dict`, and `get_subject_type_paths(db, subject_type, *, max_depth=32, max_paths=20) -> dict`.
- Every list result contains `items`, `count`, `has_more`, and `next_cursor`; every item contains `id`, `canonical_name`, `status`, `description`, `aliases`, and `child_count`.

- [x] **Step 1: Write failing root, child, pagination and path tests**

```python
def test_roots_exclude_types_with_active_parent(db):
    _edge(db, "car", "vehicle")
    body = list_root_subject_types(db)
    assert [item["canonical_name"] for item in body["items"]] == ["vehicle"]

def test_children_are_immediate_and_paginated(db):
    _edge(db, "vehicle", "entity")
    _edge(db, "car", "vehicle")
    _edge(db, "bicycle", "vehicle")
    first = list_child_subject_types(db, "vehicle", limit=1)
    second = list_child_subject_types(db, "vehicle", limit=1, cursor=first["next_cursor"])
    assert {first["items"][0]["canonical_name"], second["items"][0]["canonical_name"]} == {"bicycle", "car"}
    assert all(item["canonical_name"] != "entity" for item in first["items"] + second["items"])

def test_path_is_root_to_leaf_and_cycle_safe(db):
    _edge(db, "electric car", "car")
    _edge(db, "car", "vehicle")
    body = get_subject_type_paths(db, "electric car")
    assert [[node["canonical_name"] for node in path] for path in body["paths"]] == [["vehicle", "car", "electric car"]]
```

- [x] **Step 2: Run the focused test and verify it fails because the module is absent**

Run: `python -m pytest tests/test_vocabulary_navigation.py -v`

Expected: collection error for `app.services.vocabulary_navigation`.

- [x] **Step 3: Implement compact serialization, cursor validation and SQLAlchemy navigation queries**

Implement these exact signatures:

- `list_root_subject_types(db: Session, *, limit: int = 50, cursor: str | None = None) -> dict`
- `list_child_subject_types(db: Session, parent: SubjectType | str, *, limit: int = 50, cursor: str | None = None) -> dict`
- `get_subject_type_paths(db: Session, subject_type: SubjectType | str, *, max_depth: int = 32, max_paths: int = 20) -> dict`

Use `NOT EXISTS` for roots, exact active-edge filtering for children, deterministic `(lower(canonical_name), id)` ordering, opaque base64url JSON cursors, batched alias/child-count lookups for each page, and an upward visited-ID traversal for paths. Reject cursors whose operation or parent fingerprint differs.

- [x] **Step 4: Run focused tests and formatting checks**

Run: `python -m pytest tests/test_vocabulary_navigation.py -v && git diff --check`

Expected: all navigation tests pass and no whitespace errors.

- [x] **Step 5: Commit the navigation service**

```bash
git add app/services/vocabulary_navigation.py tests/test_vocabulary_navigation.py
git commit -m "feat: add bounded vocabulary navigation"
```

---

### Task 2: Additive MCP and REST navigation APIs

**Files:**
- Modify: `app/api/mcp_v2.py`
- Modify: `app/api/v2.py`
- Modify: `tests/test_oauth_mcp_v2.py`
- Create: `tests/test_vocabulary_navigation_api.py`

**Interfaces:**
- Consumes: Task 1 navigation functions.
- Produces MCP tools `list_root_subject_types`, `list_child_subject_types`, `get_subject_type_path` and REST routes `GET /api/v2/subject-types/roots`, `GET /api/v2/subject-types/{subject_type_id}/children`, `GET /api/v2/subject-types/{subject_type_id}/path`.

- [x] **Step 1: Write failing MCP surface and REST response tests**

```python
def test_mcp_lists_progressive_navigation_tools(client, token):
    tools = _rpc(client, "/mcp-v2", "tools/list", token=token).json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"list_root_subject_types", "list_child_subject_types", "get_subject_type_path"} <= names

def test_rest_navigation_reaches_deep_leaf(client):
    roots = client.get("/api/v2/subject-types/roots").json()
    root = next(item for item in roots["items"] if item["canonical_name"] == "entity")
    children = client.get(f"/api/v2/subject-types/{root['id']}/children").json()
    assert "object" in {item["canonical_name"] for item in children["items"]}
```

- [x] **Step 2: Run focused API tests and verify missing tools/routes fail**

Run: `python -m pytest tests/test_oauth_mcp_v2.py tests/test_vocabulary_navigation_api.py -v`

Expected: failures for the three absent MCP tools and REST routes.

- [x] **Step 3: Add tool schemas, dispatch handlers and REST endpoints**

MCP inputs:

```json
{"list_root_subject_types": {"limit": 50, "cursor": null}}
{"list_child_subject_types": {"parent": "vehicle", "limit": 50, "cursor": null}}
{"get_subject_type_path": {"subject_type": "electric car"}}
```

Return structured not-found errors for unknown parent/type terms, preserve read-only security annotations, and leave all existing tools unchanged.

- [x] **Step 4: Increment `SERVER_VERSION` and update exact tool-surface assertions**

Change `3.20.2-alpha` to `3.21.0-alpha` and make the OAuth test expect the three additive tools.

- [x] **Step 5: Run focused API tests**

Run: `python -m pytest tests/test_oauth_mcp_v2.py tests/test_vocabulary_navigation_api.py -v`

Expected: all pass.

- [x] **Step 6: Commit the API surface**

```bash
git add app/api/mcp_v2.py app/api/v2.py tests/test_oauth_mcp_v2.py tests/test_vocabulary_navigation_api.py
git commit -m "feat: expose progressive taxonomy navigation"
```

---

### Task 3: Replace full-index workflow guidance and prove fallback behaviour

**Files:**
- Modify: `app/services/guidance.py`
- Modify: `app/services/mcp_v2_semantic_policy.py`
- Modify: `app/services/semantic.py`
- Modify: `app/api/mcp_v2.py`
- Modify: `tests/test_guidance.py`
- Modify: `tests/test_semantic_naming_policy.py`
- Create: `tests/test_progressive_vocabulary_workflows.py`

**Interfaces:**
- Consumes: the three navigation tools and existing `resolve_subject_type`, `resolve_subject_hierarchy`, `search`, `affirm_subject_classification`, and `propose_subject_reclassification` contracts.
- Produces: normal write/retrieval guidance implementing bounded ranked candidates, fallback retention and backtracking without `vocabulary_index`.

- [x] **Step 1: Write failing guidance and workflow tests**

```python
def test_normal_guidance_never_requires_full_vocabulary():
    normal_text = initialize_instructions + classification_guidance + retrieval_guidance + unknown_type_instructions
    assert "inspect vocabulary_index" not in normal_text.casefold()
    assert "list_root_subject_types" in normal_text
    assert "fallback" in normal_text.casefold()
    assert "backtrack" in normal_text.casefold()

def test_first_ranked_branch_misses_then_fallback_retrieves_subject(db, principal):
    trace = run_controlled_progressive_retrieval(
        db, principal, query="deep target", ranked_roots=["wrong root", "correct root"]
    )
    assert trace["result"]["subject_name"] == "Deep target"
    assert trace["backtracks"] == 1

def test_missing_leaf_is_created_only_below_verified_existing_path(db):
    result = resolve_subject_hierarchy(db, ["entity", "object", "new instrument"], created_by="model-a")
    assert result["created_terms"] == ["new instrument"]
```

- [x] **Step 2: Run the focused tests and verify the legacy instructions fail**

Run: `python -m pytest tests/test_guidance.py tests/test_semantic_naming_policy.py tests/test_progressive_vocabulary_workflows.py -v`

Expected: guidance assertions fail because current text directs clients to `vocabulary_index`.

- [x] **Step 3: Update initialization, baseline induction, tool descriptions and error instructions**

The shared protocol text must instruct models to resolve an obvious term first; otherwise list roots, rank a small candidate set, retain fallbacks, descend through immediate children, backtrack after an inadequate classification or retrieval result, and stop without enumerating the entire taxonomy once an adequate type is found or a new type is justified.

Describe `vocabulary_index` as an administrative/debugging export not intended for normal AI workflows. Replace messages in `_resolve`, subject-context validation, save errors and `resolve_subject_hierarchy` with navigation-tool instructions.

- [x] **Step 4: Add a deterministic controlled traversal helper inside the workflow test**

The helper calls the real navigation service and real `_search`; only branch ranking/adequacy is supplied by the test fixture. It must retain a priority-ordered fallback list, record visited node IDs and backtrack when the first branch search is empty.

- [x] **Step 5: Run workflow tests plus convergence/dispute regressions**

Run: `python -m pytest tests/test_guidance.py tests/test_semantic_naming_policy.py tests/test_progressive_vocabulary_workflows.py tests/test_subject_classification_convergence.py tests/test_tg_ai_resolver.py tests/test_semantic_resolution.py -v`

Expected: all pass, including confirmed locking and disputed resolver cases.

- [x] **Step 6: Commit workflow guidance**

```bash
git add app/services/guidance.py app/services/mcp_v2_semantic_policy.py app/services/semantic.py app/api/mcp_v2.py tests/test_guidance.py tests/test_semantic_naming_policy.py tests/test_progressive_vocabulary_workflows.py
git commit -m "refactor: guide clients through bounded taxonomy search"
```

---

### Task 4: Configurable scale benchmark

**Files:**
- Create: `benchmarks/vocabulary_traversal.py`
- Create: `scripts/benchmark_vocabulary_traversal.py`
- Create: `tests/test_vocabulary_traversal_benchmark.py`

**Interfaces:**
- Consumes: `vocabulary_index`, Task 1 navigation services, and the MCP `_search` implementation.
- Produces: `run_vocabulary_traversal_benchmark(node_count: int = 10_000, branching_factor: int = 10) -> dict` and a CLI supporting `--nodes`, `--branching-factor`, and `--json-output`.

- [x] **Step 1: Write a failing small deterministic benchmark test**

```python
def test_progressive_benchmark_is_accurate_and_bounded():
    report = run_vocabulary_traversal_benchmark(node_count=1_000, branching_factor=10)
    assert report["full_vocabulary"]["classification_success"] is True
    assert report["progressive"]["classification_success"] is True
    assert report["full_vocabulary"]["retrieval_success"] is True
    assert report["progressive"]["retrieval_success"] is True
    assert report["progressive"]["taxonomy_nodes_returned"] < report["total_taxonomy_nodes"] * 0.05
    assert report["progressive"]["backtracks"] >= 1
```

- [x] **Step 2: Run the benchmark test and verify the module is absent**

Run: `python -m pytest tests/test_vocabulary_traversal_benchmark.py -v`

Expected: collection error for `benchmarks.vocabulary_traversal`.

- [x] **Step 3: Implement fast synthetic taxonomy generation and both measured strategies**

Generate deterministic UUID-backed types and active `belongs_to` edges in batches. Put known subjects/reviews at deep leaves. Serialize every service result with compact JSON, estimate tokens as `ceil(payload_bytes / 4)`, time with `perf_counter`, and report classification/retrieval success, nodes returned, percentage inspected, calls, bytes, approximate tokens, elapsed milliseconds and backtracks.

The progressive strategy follows the known expected path but deliberately tries one plausible wrong sibling first, producing a real empty scoped search and one backtrack. The full strategy serializes the unchanged full index before resolving and searching the same target.

- [x] **Step 4: Add the CLI and stable JSON/text output**

```bash
python scripts/benchmark_vocabulary_traversal.py --nodes 10000 --branching-factor 10
python scripts/benchmark_vocabulary_traversal.py --nodes 50000 --branching-factor 10 --json-output /tmp/testgraph-vocabulary-50000.json
```

- [x] **Step 5: Run the deterministic benchmark test**

Run: `python -m pytest tests/test_vocabulary_traversal_benchmark.py -v`

Expected: all pass.

- [x] **Step 6: Commit the benchmark**

```bash
git add benchmarks/vocabulary_traversal.py scripts/benchmark_vocabulary_traversal.py tests/test_vocabulary_traversal_benchmark.py
git commit -m "test: benchmark progressive vocabulary traversal"
```

---

### Task 5: Complete verification and release documentation

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/plans/2026-09-12-progressive-vocabulary-traversal.md`

**Interfaces:**
- Consumes: the completed implementation and benchmark CLI.
- Produces: documented public tools, recorded verified commands/results and a clean push-ready branch.

- [x] **Step 1: Document normal progressive classification and retrieval**

Add a concise README section showing `resolve_subject_type` followed by roots/children/path navigation, retained fallbacks, scoped search and new-type hierarchy resolution. State that `vocabulary_index` is for administration/debugging.

- [x] **Step 2: Install dependencies in an isolated local virtual environment and run focused verification**

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest tests/test_vocabulary_navigation.py tests/test_vocabulary_navigation_api.py tests/test_progressive_vocabulary_workflows.py tests/test_vocabulary_traversal_benchmark.py -v
```

Expected: all focused tests pass.

- [x] **Step 3: Run the complete existing and new suite**

Run: `.venv/bin/python -m pytest -q`

Expected: zero failures.

- [x] **Step 4: Run the required 10,000-node scale benchmark**

Run: `.venv/bin/python scripts/benchmark_vocabulary_traversal.py --nodes 10000 --branching-factor 10`

Expected: both approaches classify and retrieve successfully; progressive traversal returns less than 5% of taxonomy nodes and records at least one successful backtrack.

- [x] **Step 5: Run the optional 50,000-node benchmark when practical**

Run: `.venv/bin/python scripts/benchmark_vocabulary_traversal.py --nodes 50000 --branching-factor 10`

Expected: both approaches remain correct and progressive traversal still returns less than 5% of nodes. If runtime makes this impractical, report the measured limitation without claiming it passed.

- [x] **Step 6: Check repository integrity and inspect the final diff**

Run: `git diff --check && git status --short && git log --oneline --decorate -8`

Expected: only intentional documented changes remain; no generated database, benchmark output or `.venv` files are tracked.

- [x] **Step 7: Record actual test and benchmark results in the plan execution notes**

Append exact commands, pass counts, node counts, payload metrics, latency and remaining risks. Do not insert projected values.

- [x] **Step 8: Commit documentation and verified execution notes**

```bash
git add README.md CHANGELOG.md docs/superpowers/plans/2026-09-12-progressive-vocabulary-traversal.md
git commit -m "docs: explain progressive vocabulary traversal"
```

- [ ] **Step 9: Push only after every required verification passes**

```bash
git push origin master
```

If any required test or the 10,000-node benchmark fails, do not push. Report the exact blocker and preserve the local commits for repair.

## Execution notes

Completed on 2026-09-12 against `b939599` from `origin/master`.

- Untouched baseline: `.venv/bin/python -m pytest -q` — 241 passed, 11 pre-existing warnings.
- Final suite: `.venv/bin/python -m pytest -q` — 256 passed, 11 pre-existing warnings.
- Compilation: `.venv/bin/python -m compileall -q app benchmarks scripts` — passed.
- Repository whitespace validation: `git diff --check` — passed.
- Remote divergence before final commit: `origin/master` was 0 commits ahead and this branch was 6 commits ahead.

10,000-node benchmark (`branching_factor=10`, target depth 4):

| Metric | Full vocabulary | Progressive |
| --- | ---: | ---: |
| Classification success | true | true |
| Retrieval success | true | true |
| Taxonomy nodes returned | 10,000 | 60 |
| Taxonomy returned | 100% | 0.6% |
| Tool calls | 2 | 8 |
| Payload bytes | 2,500,145 | 11,819 |
| Approximate context tokens | 625,037 | 2,955 |
| Latency | 392.136 ms | 38.149 ms |
| Backtracks | 0 | 1 |

50,000-node benchmark (`branching_factor=10`, target depth 5):

| Metric | Full vocabulary | Progressive |
| --- | ---: | ---: |
| Classification success | true | true |
| Retrieval success | true | true |
| Taxonomy nodes returned | 50,000 | 80 |
| Taxonomy returned | 100% | 0.16% |
| Tool calls | 2 | 10 |
| Payload bytes | 12,500,145 | 15,407 |
| Approximate context tokens | 3,125,037 | 3,852 |
| Latency | 2,214.194 ms | 172.010 ms |
| Backtracks | 0 | 1 |

The token estimate is `ceil(serialized UTF-8 payload bytes / 4)`. Timings are single-process SQLite measurements and should be treated as comparative local results, not production latency guarantees.

Remaining risks: semantic branch ranking still depends on the connected AI following the advertised protocol; very broad scoped searches can still expand many descendant IDs inside the server; and offset cursors can observe taxonomy changes between pages. These do not cause full taxonomy transfer to the model, but production telemetry may justify recursive SQL/closure indexing and snapshot-aware cursors later.
