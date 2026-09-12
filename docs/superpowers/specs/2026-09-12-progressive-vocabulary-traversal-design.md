# Progressive Vocabulary Traversal Design

## Goal

Allow AI clients to classify subjects and scope retrieval without downloading the complete TestGraph classification vocabulary. Preserve `vocabulary_index` for administration and debugging while making bounded, progressive hierarchy traversal the normal MCP and API workflow.

## Current architecture and actual dependency

`app.services.v2.vocabulary_index` reads and serializes every subject type, alias, active type relationship and field. The save and retrieval services do not call it internally. The normal-client dependency is created by MCP initialization text, tool descriptions, induction guidance and unknown-type error instructions that tell clients to inspect the complete index.

The write path resolves a submitted type by canonical name or alias and rejects unknown types in the normal MCP save flow. Clients are expected to inspect the vocabulary and call `resolve_subject_hierarchy` before retrying. Classification convergence is independently enforced by `app.services.classification`.

The MCP retrieval path accepts an optional subject type. It resolves that type and, when `include_related` is true, searches subjects and experiences stored at that type or active descendants. The missing capability is an incremental way for the AI to choose the appropriate type or subtree.

## Chosen architecture

Add stateless, read-only vocabulary navigation services exposed through MCP and REST:

1. `list_root_subject_types` returns types with no active `belongs_to` parent.
2. `list_child_subject_types` returns only immediate active `belongs_to` children of one resolved type.
3. `get_subject_type_path` returns the resolved type, its immediate parents and its active root-to-type path or paths.

Each result uses a compact type summary containing the stable type ID, canonical name, status, optional description, aliases and immediate child count. Root and child lists are ordered deterministically and paginated with a bounded limit and opaque cursor so a pathologically wide level cannot force a whole-vocabulary response.

The services remain structural and deterministic. They do not embed a model, model-generated ranking or semantic similarity policy. The connected AI ranks the small visible candidate set and retains alternatives. This preserves TestGraph's model-independent design.

No database schema change is required. Existing indexes on normalized names and relationship source/target/status columns support the navigation queries.

## Traversal protocol

Normal clients use bounded best-first traversal rather than greedy descent:

1. Resolve the user's proposed term directly first, because a canonical or alias match is cheaper and more reliable than browsing.
2. If more context is needed, list roots and rank a small set of plausible candidates.
3. Follow the strongest candidate by listing only its immediate children while retaining other candidates as fallbacks.
4. Repeat recursively, subject to a small reasoning budget. Do not enumerate unrelated branches merely to prove completeness.
5. If the strongest branch produces no adequate existing classification or retrieval result, backtrack to the next retained candidate.
6. Stop at the most specific adequate existing type, or after enough bounded exploration to justify proposing a genuinely missing type.

The server returns structure and verification data; the AI supplies semantic ranking and the adequacy judgment. Tool descriptions and induction guidance will state this protocol explicitly.

## Write and classification flow

For a new subject or review:

1. Call `resolve_subject_type` with the natural candidate term.
2. If it resolves, reuse its stable ID and canonical name.
3. If it does not resolve, traverse roots and immediate children using the bounded fallback protocol.
4. If an existing type is adequate, save using that type.
5. Otherwise call `resolve_subject_hierarchy` with a broad-to-specific path containing the verified existing ancestors followed by only the genuinely missing semantic nodes.

For review of an existing provisional or candidate subject, `get_subject_classification` continues to return immediate children. The same progressive primitives may be used to inspect deeper descendants before affirmation or reclassification.

Navigation does not write or infer classification state. Existing semantic-head validation, alias-versus-concept policy, provisional/candidate/confirmed/locked transitions, independent-model agreement, dispute resolution, audit history, identity collision checks, stable IDs and cycle prevention remain in their existing authoritative services.

## Retrieval flow

Lexical search with the user's wording remains the first useful retrieval step. When a classification scope is useful:

1. Resolve an obvious type directly or progressively navigate roots and children.
2. Retain multiple plausible branches rather than committing irrevocably to the first.
3. Call the existing `search` tool for the selected type with descendant inclusion when appropriate.
4. If the result is inadequate, search the next retained branch.
5. Report absence only after the relevant bounded fallback branches have been tried; a miss in one branch is not global absence.

The existing server-side descendant expansion remains internal and does not expose taxonomy nodes to the model. Selecting a deep or appropriately narrow subtree keeps that expansion bounded in ordinary use.

## Compatibility

- Keep `vocabulary_index` unchanged for administrative and debugging clients, but describe it as unsuitable for normal classification or retrieval.
- Add the three MCP tools without removing or renaming existing tools.
- Add equivalent REST read endpoints under `/api/v2/subject-types`.
- Keep existing save, hierarchy, classification and search request/response contracts intact.
- Increment the MCP server version because the advertised tool surface and induction contract change.
- Update exact-tool-list and guidance tests.
- Replace every normal-workflow instruction to inspect `vocabulary_index` with progressive navigation instructions.

The legacy ChatGPT Actions vocabulary endpoint remains available. Its write endpoint is compatibility-only and is not promoted as the normal MCP classification workflow.

## Error handling and limits

- Unknown parent/type terms return a normal not-found result through MCP and HTTP 404 through REST.
- Invalid or mismatched cursors are rejected and never silently restarted.
- Pagination cursors bind to the operation and parent so they cannot be reused on another branch.
- Active `belongs_to` edges alone define roots, children and paths.
- Legacy multiple-parent data returns all valid active paths rather than guessing one.
- Cycle-safe path traversal records visited IDs and refuses unbounded recursion even if legacy corrupt data exists.
- Default and maximum page limits keep each response bounded.

## Testing

Add focused unit and MCP/API integration tests for:

- root discovery;
- immediate-child discovery;
- deterministic pagination and cursor validation;
- multi-level descent;
- ranked fallback and backtracking in a deterministic traversal harness;
- reuse of an existing type;
- proposal/creation of a genuinely missing leaf below a verified path;
- deep retrieval through a selected classification branch;
- first-branch retrieval miss followed by fallback success;
- normal guidance and error responses containing no dependency on `vocabulary_index`;
- continued availability of `vocabulary_index` for compatibility;
- unchanged confirmation, locking and dispute behaviour.

Run the complete existing suite after the focused tests.

## Scale benchmark

Add a deterministic benchmark that creates a configurable balanced synthetic taxonomy with at least 10,000 nodes by default and support for 50,000. It places known subjects and experiences at deep leaves and executes both classification lookup and retrieval cases.

Compare:

- full-index baseline: serialize `vocabulary_index`, then select the known target;
- progressive traversal: list roots, list immediate children along ranked candidate branches, deliberately exercise at least one fallback, then search the selected scope.

Record for each approach and operation:

- correct classification or retrieval;
- taxonomy nodes returned to the simulated model;
- percentage of total nodes returned;
- tool/service calls;
- serialized payload bytes;
- approximate context tokens using a documented byte-to-token estimate;
- elapsed wall-clock time;
- backtrack count.

The benchmark uses deterministic rankings and known expected paths rather than an external AI API. It measures the architectural scaling effect without mixing in model latency or nondeterminism.

## Expected files

- Add `app/services/vocabulary_navigation.py`.
- Modify `app/api/mcp_v2.py`.
- Modify `app/api/v2.py`.
- Modify `app/services/guidance.py`.
- Modify `app/services/mcp_v2_semantic_policy.py`.
- Add focused navigation/integration tests.
- Add a benchmark module and command-line script.
- Update compatibility assertions and concise documentation where required.

## Remaining design risks

An AI can still ignore tool guidance or choose poor candidate rankings. The server can bound each structural response and preserve write safeguards, but it cannot guarantee semantic search quality without embedding a ranking model. The benchmark therefore proves payload scaling and correct fallback mechanics under controlled rankings, not universal model behaviour.

Very broad subtree retrieval can still make the server inspect many descendant IDs internally. This does not consume model context and is outside the immediate full-vocabulary-download problem, but recursive SQL or closure-table indexing may become a later database optimization if production measurements show it is necessary.
