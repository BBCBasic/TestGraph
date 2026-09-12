# Typed Classification Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reversible typed classification mode that stores and traverses `is_a` and `part_of` independently while leaving legacy `belongs_to` behaviour unchanged.

**Architecture:** Reuse the existing stable subject-type dictionary and `subject_type_relationships` table. Central mode helpers define the active taxonomic relationship and supported edge types; semantic writes, bounded traversal, classification, search, MCP/REST contracts and Live View consume those helpers rather than branching on subject names.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic Settings, Alembic-managed SQLite/PostgreSQL, pytest, vanilla HTML/CSS/JavaScript.

**Spec:** `docs/superpowers/specs/2026-09-12-typed-classification-graph-design.md`

## Global Constraints

- `CLASSIFICATION_MODE=legacy` remains the default and retains current `belongs_to` behaviour.
- `CLASSIFICATION_MODE=typed` supports only `is_a` and `part_of` classification edges.
- Stable `SubjectType.id` values remain the identity boundary; do not duplicate nodes per path.
- Progressive navigation stays bounded and does not download the complete vocabulary.
- Production code and prompts contain no fixture-name or domain-specific classification rules.
- `part_of` does not imply taxonomic descent and does not expand subtype-scoped search.
- Do not migrate historical hierarchy data, seed the cleared service database or load the large benchmark dataset.

---

### Task 1: Mode Contract and Generic Relationship Semantics

**Files:**
- Create: `app/services/classification_mode.py`
- Modify: `app/core/config.py`
- Modify: `app/services/semantic.py`
- Modify: `app/services/v2.py`
- Test: `tests/test_typed_classification_mode.py`
- Test: `tests/test_semantic_resolution.py`

**Interfaces:**
- Produces: `classification_mode() -> Literal["legacy", "typed"]`
- Produces: `taxonomy_relationship() -> Literal["belongs_to", "is_a"]`
- Produces: `supported_classification_relationships() -> tuple[str, ...]`
- Produces: `validate_classification_relationship(relationship: str) -> str`
- Changes: `resolve_subject_hierarchy(...)` creates the active taxonomy edge and reports it.

- [ ] **Step 1: Write failing mode and edge-invariant tests**

```python
def test_legacy_is_default_and_typed_mode_is_valid(monkeypatch):
    monkeypatch.delenv("CLASSIFICATION_MODE", raising=False)
    assert Settings().classification_mode == "legacy"
    assert Settings(classification_mode="typed").classification_mode == "typed"

def test_typed_mode_keeps_distinct_edges_and_multiple_parents(db, typed_mode):
    edge_a = add_semantic_relationship(db, leaf, "is_a", parent_a, source="pytest")
    edge_b = add_semantic_relationship(db, leaf, "is_a", parent_b, source="pytest")
    membership = add_semantic_relationship(db, leaf, "part_of", system, source="pytest")
    assert {edge_a.status, edge_b.status, membership.status} == {"active"}
    assert db.scalar(select(func.count(SubjectType.id)).where(SubjectType.id == leaf.id)) == 1

def test_typed_mode_rejects_ambiguous_belongs_to(db, typed_mode):
    with pytest.raises(ValueError, match="choose 'is_a' or 'part_of'"):
        add_semantic_relationship(db, leaf, "belongs_to", parent, source="pytest")
```

- [ ] **Step 2: Run focused tests and confirm missing-mode failures**

Run: `pytest -q tests/test_typed_classification_mode.py tests/test_semantic_resolution.py`

Expected: new typed tests fail because settings/helpers and typed semantics do not exist; existing legacy tests pass.

- [ ] **Step 3: Implement the central mode helper and typed write invariants**

```python
ClassificationMode = Literal["legacy", "typed"]

def taxonomy_relationship() -> str:
    return "is_a" if get_settings().classification_mode == "typed" else "belongs_to"

def supported_classification_relationships() -> tuple[str, ...]:
    return ("is_a", "part_of") if get_settings().classification_mode == "typed" else ("belongs_to",)
```

Add `classification_mode: Literal["legacy", "typed"] = "legacy"` to settings. Generalise cycle detection to the exact edge type. Preserve single-parent retirement only when mode is legacy and edge is `belongs_to`; never retire another typed parent automatically. Make hierarchy resolution use `taxonomy_relationship()` and include the chosen relationship in its result.

- [ ] **Step 4: Run focused tests and confirm green**

Run: `pytest -q tests/test_typed_classification_mode.py tests/test_semantic_resolution.py`

- [ ] **Step 5: Commit the mode and relationship layer**

```bash
git add app/core/config.py app/services/classification_mode.py app/services/semantic.py app/services/v2.py tests/test_typed_classification_mode.py tests/test_semantic_resolution.py
git commit -m "feat: add typed classification relationship semantics"
```

### Task 2: Mode-Aware Bounded Traversal, Classification and Search

**Files:**
- Modify: `app/services/vocabulary_navigation.py`
- Modify: `app/services/classification.py`
- Modify: `app/services/v2.py`
- Test: `tests/test_vocabulary_navigation.py`
- Test: `tests/test_subject_classification_convergence.py`
- Test: `tests/test_typed_classification_mode.py`

**Interfaces:**
- Changes: `list_root_subject_types(..., relationship: str | None = None) -> dict`
- Changes: `list_child_subject_types(..., relationship: str | None = None) -> dict`
- Changes: `get_subject_type_paths(..., relationship: str | None = None, max_depth=32, max_paths=20) -> dict`
- Responses add `classification_mode` and `relationship`.
- Classification and `descendant_type_ids` consume `taxonomy_relationship()`.

- [ ] **Step 1: Write failing traversal and search tests**

```python
def test_typed_traversal_separates_taxonomy_from_membership(db, typed_mode):
    add_semantic_relationship(db, leaf, "is_a", category, source="pytest")
    add_semantic_relationship(db, leaf, "part_of", system, source="pytest")
    assert names(list_child_subject_types(db, category)["items"]) == [leaf.canonical_name]
    assert names(list_child_subject_types(db, system)["items"]) == []
    assert names(list_child_subject_types(db, system, relationship="part_of")["items"]) == [leaf.canonical_name]

def test_cursor_is_bound_to_relationship(db, typed_mode):
    cursor = list_child_subject_types(db, parent, relationship="is_a", limit=1)["next_cursor"]
    with pytest.raises(ValueError, match="cursor does not match"):
        list_child_subject_types(db, parent, relationship="part_of", limit=1, cursor=cursor)

def test_part_of_does_not_expand_typed_descendant_search(db, typed_mode):
    assert category_member.id in descendant_type_ids(db, category)
    assert system_member.id not in descendant_type_ids(db, system)
```

- [ ] **Step 2: Run focused tests and confirm relationship separation failures**

Run: `pytest -q tests/test_vocabulary_navigation.py tests/test_subject_classification_convergence.py tests/test_typed_classification_mode.py`

- [ ] **Step 3: Generalise bounded traversal without changing its limits**

Bind opaque cursors to `operation`, `parent_id`, `relationship` and offset. Default to `taxonomy_relationship()`. Validate explicit selectors against the active mode. Filter roots, children, paths and child counts by the selected edge. Include mode and edge metadata in every response.

Replace hard-coded classification and descendant-search `belongs_to` filters with `taxonomy_relationship()`. Keep all depth, page and path caps unchanged. Do not traverse `part_of` for specificity or subtype search.

- [ ] **Step 4: Run focused tests and confirm green**

Run: `pytest -q tests/test_vocabulary_navigation.py tests/test_subject_classification_convergence.py tests/test_typed_classification_mode.py`

- [ ] **Step 5: Commit bounded typed traversal**

```bash
git add app/services/vocabulary_navigation.py app/services/classification.py app/services/v2.py tests/test_vocabulary_navigation.py tests/test_subject_classification_convergence.py tests/test_typed_classification_mode.py
git commit -m "feat: traverse typed classification edges independently"
```

### Task 3: REST and MCP Compatibility Contract

**Files:**
- Modify: `app/schemas/v2.py`
- Modify: `app/api/v2.py`
- Modify: `app/api/mcp_v2.py`
- Modify: `app/services/guidance.py`
- Modify: `app/services/mcp_v2_semantic_policy.py`
- Test: `tests/test_vocabulary_navigation_api.py`
- Test: `tests/test_oauth_mcp_v2.py`
- Test: `tests/test_semantic_naming_policy.py`
- Test: `tests/test_typed_classification_mode.py`

**Interfaces:**
- REST roots, children and paths accept optional `relationship`.
- MCP root, child and path tools accept optional `relationship`.
- `set_type_relationship` exposes explicit typed choices in typed mode guidance while retaining its wire name.
- Vocabulary responses publish mode, taxonomy edge and supported classification relationships.

- [ ] **Step 1: Write failing REST/MCP contract tests**

```python
def test_rest_typed_navigation_reports_relationship(client, typed_mode):
    response = client.get("/api/v2/subject-types/roots", params={"relationship": "part_of"})
    assert response.json()["classification_mode"] == "typed"
    assert response.json()["relationship"] == "part_of"

def test_typed_mcp_guidance_asks_two_independent_questions(client, typed_mode):
    instructions, tools = initialise_mcp(client)
    assert "what fundamentally is this thing" in instructions.casefold()
    assert "what larger" in instructions.casefold()
    assert set(tool_schema(tools, "set_type_relationship")["relationship"]["enum"]) == {"is_a", "part_of"}
```

- [ ] **Step 2: Run focused tests and confirm contract failures**

Run: `pytest -q tests/test_vocabulary_navigation_api.py tests/test_oauth_mcp_v2.py tests/test_semantic_naming_policy.py tests/test_typed_classification_mode.py`

- [ ] **Step 3: Add mode-aware API parameters, metadata and semantic guidance**

Pass the optional selector through REST and MCP handlers. Add mode metadata to `vocabulary_index`. Build tool descriptions and input schemas from the active mode without renaming tools. In typed mode remove the `belongs_to` default and require explicit `is_a` or `part_of`; in legacy mode preserve the existing default and descriptions.

Update induction and semantic policy generically: separate fundamental identity from optional larger-system membership; treat modifiers as attributes/relationships; never require both edges; never infer one edge from the other.

- [ ] **Step 4: Run focused tests and confirm green**

Run: `pytest -q tests/test_vocabulary_navigation_api.py tests/test_oauth_mcp_v2.py tests/test_semantic_naming_policy.py tests/test_typed_classification_mode.py`

- [ ] **Step 5: Commit API and MCP support**

```bash
git add app/schemas/v2.py app/api/v2.py app/api/mcp_v2.py app/services/guidance.py app/services/mcp_v2_semantic_policy.py tests/test_vocabulary_navigation_api.py tests/test_oauth_mcp_v2.py tests/test_semantic_naming_policy.py tests/test_typed_classification_mode.py
git commit -m "feat: expose typed classification through API and MCP"
```

### Task 4: Typed Live View

**Files:**
- Modify: `app/static/live.html`
- Test: `tests/test_live_page.py`

**Interfaces:**
- Consumes: vocabulary metadata and explicit stored relationship labels from Task 3.
- Produces: an `is_a` hierarchy plus separately labelled `part_of` connections in typed mode.

- [ ] **Step 1: Write failing Live View structure tests**

```python
def test_live_page_distinguishes_typed_relationships(client):
    page = client.get("/live")
    assert "taxonomy_relationship" in page.text
    assert "Part of" in page.text
    assert "data-type-id" in page.text
```

- [ ] **Step 2: Run the page test and confirm it fails**

Run: `pytest -q tests/test_live_page.py`

- [ ] **Step 3: Render mode-aware taxonomy and membership sections**

Read `taxonomy_relationship` from the vocabulary payload. Parameterise path, parent and child helpers by that relationship. Put stable IDs on rendered node occurrences, preserve multi-parent occurrences with branch-local cycle guards, and label them as alternate paths. In typed mode render direct `part_of` targets/sources separately. In legacy mode retain existing labels and collapsed hierarchy behaviour.

- [ ] **Step 4: Run the page and navigation tests**

Run: `pytest -q tests/test_live_page.py tests/test_vocabulary_navigation_api.py`

- [ ] **Step 5: Commit Live View support**

```bash
git add app/static/live.html tests/test_live_page.py
git commit -m "feat: distinguish typed edges in live view"
```

### Task 5: Generic Fixtures, Controlled Comparison and Final Verification

**Files:**
- Create: `scripts/compare_classification_modes.py`
- Create: `tests/test_typed_classification_examples.py`
- Modify: `docs/TASTEGRAPH_V2.md`

**Interfaces:**
- Comparison script creates temporary databases only and prints JSON summaries for the same legacy/typed inputs.
- Tests cover supplied fixtures plus an unrelated domain and modifier handling through generic APIs.

- [ ] **Step 1: Write failing generic-fixture tests**

```python
@pytest.mark.parametrize("leaf,parent,system", REPRESENTATIVE_PATTERNS)
def test_one_stable_type_supports_taxonomy_and_membership(db, typed_mode, leaf, parent, system):
    resolved = resolve_fixture(db, leaf, parent, system)
    assert resolved["node_ids"][leaf] == resolved["taxonomy_leaf_id"]
    assert resolved["node_ids"][leaf] == resolved["membership_source_id"]
    assert resolved["relationships"] == {"is_a", "part_of"}

def test_modifier_descriptions_do_not_create_modifier_nodes(db, typed_mode):
    result = classify_described_fixture(db, description="fixture description")
    assert result["type_count"] == result["semantic_head_type_count"]
    assert result["modifiers"] == result["stored_attributes"]
```

Include an unrelated fixture whose semantics have the same category/member pattern but none of the supplied example terms.

- [ ] **Step 2: Run new tests and confirm the comparison support is missing**

Run: `pytest -q tests/test_typed_classification_examples.py`

- [ ] **Step 3: Implement the temporary-database comparison and concise documentation**

Use `tempfile.TemporaryDirectory`, one fresh SQLAlchemy database per mode, and existing public service functions. Print mode, node IDs, counts, active edges, taxonomy paths, membership edges and modifier outcomes. Do not connect to `DATABASE_URL`, call an external model or seed historical data.

Document mode selection, edge meanings, API usage and the fact that switching modes does not convert stored edges.

- [ ] **Step 4: Run the same small experiment in both modes**

Run: `python scripts/compare_classification_modes.py --format json`

Expected: legacy output contains a single active `belongs_to` choice per fixture; typed output preserves distinct `is_a` and justified `part_of` edges with stable IDs and no modifier nodes.

- [ ] **Step 5: Scan production code for accidental fixture rules**

Run: `rg -n -i "model railway|model train|car tyre|camera lens|chess piece|trix|blunt needle|\btrack\b|\btoy\b" app --glob '*.py' --glob '*.html'`

Expected: zero matches introduced by this implementation; any pre-existing match must be inspected and shown unrelated to production classification branching.

- [ ] **Step 6: Run both complete suites and static checks**

Run legacy: `CLASSIFICATION_MODE=legacy pytest -q`

Run typed: `CLASSIFICATION_MODE=typed pytest -q`

Run compile check: `python -m compileall -q app scripts`

Run whitespace check: `git diff --check`

- [ ] **Step 7: Commit experiment and documentation**

```bash
git add scripts/compare_classification_modes.py tests/test_typed_classification_examples.py docs/TASTEGRAPH_V2.md
git commit -m "test: compare legacy and typed classification modes"
```

- [ ] **Step 8: Re-read the specification and record requirement evidence**

Map each requested delivery item to changed files, fresh test output, comparison JSON and special-case scan results. Explicitly document any effects on search, classification or Live View and decide whether larger cleared-database population is safe.

- [ ] **Step 9: Push the verified commit series**

```bash
git push origin master
```
