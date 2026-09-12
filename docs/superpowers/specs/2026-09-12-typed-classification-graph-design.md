# Typed Classification Graph Design

## Goal

Add an experimental classification model that represents taxonomic identity and system membership independently, without deleting or changing the current single-parent behaviour. The experiment must determine whether an explicitly typed graph preserves more useful semantics than the legacy tree while retaining stable subject-type identities, bounded vocabulary navigation, current audit guarantees and existing client compatibility wherever the semantics are unambiguous.

The implementation is generic. Production code, prompts and policy contain no rules keyed to fixture names or particular domains.

## Modes

The service selects one classification model at process start through `CLASSIFICATION_MODE`:

- `legacy` is the default and preserves active `belongs_to` semantics exactly as they work now.
- `typed` enables explicit `is_a` and `part_of` type relationships.

Invalid values fail settings validation at startup rather than falling back silently. The active mode is returned by vocabulary-navigation responses so clients and Live View can explain the structure they are showing.

Both modes use the same subject-type dictionary and stable `SubjectType.id` values. Switching modes does not copy nodes or construct a parallel taxonomy.

## Semantic model

Typed classification separates two independent questions:

1. What fundamentally is this type?
2. What larger system, domain, structure or concept is it part of, if any?

An answer to the first question is represented by `is_a`. An answer to the second may be represented by `part_of`. Neither relationship implies the other, and no type is required to have both.

A type may have multiple active `is_a` parents when each is semantically valid, and zero or more active `part_of` targets. All paths refer to the same source and target IDs. Multiple paths never cause duplicate subject types.

Typed mode accepts only `is_a` and `part_of` for classification relationships. Legacy mode retains the existing open relationship storage and gives `belongs_to` its current single-parent classification behaviour.

## Storage and invariants

Reuse `subject_type_relationships`. It already provides:

- stable source and target type IDs;
- a relationship discriminator;
- a uniqueness constraint across source ID, relationship and target ID;
- active/retired status;
- creation source and retirement audit metadata.

No new taxonomy table or duplicate node hierarchy is added. No schema migration is expected unless implementation proves an invariant cannot be expressed safely in the service layer while preserving legacy data.

In typed mode:

- self-edges are rejected;
- duplicate active edges are idempotent;
- retired exact edges remain tombstoned and cannot be silently recreated;
- `is_a` cycles are rejected;
- `part_of` cycles are rejected;
- adding one valid parent does not retire another active typed edge.

Cycle checks follow only edges of the relationship being added. A mixed path does not imply a cycle because `is_a` and `part_of` have different meanings.

In legacy mode, the existing `belongs_to` cycle check and automatic previous-parent retirement remain unchanged.

## Semantic-head and modifiers

The existing server-owned semantic-head validation remains authoritative for proposed type nodes. It continues to distinguish a fundamental type from descriptive details and requires explicit semantic justification where an otherwise modifier-like phrase genuinely identifies a reusable class.

Typed classification guidance tells the connected model to separate the semantic head from brand, quantity, colour, material, state or condition, size, location and purpose or use. These details belong in attributes or suitable existing relationships unless they genuinely define a reusable type.

This remains a generic language-level policy. The implementation does not contain catalogues of brands, products or supplied fixture terms.

## Relationship writes and compatibility

`set_type_relationship` remains the minimum general write capability.

- In legacy mode its default remains `belongs_to`, with current behaviour unchanged.
- In typed mode the caller must explicitly send `is_a` or `part_of`.
- An explicit or defaulted `belongs_to` write in typed mode is rejected with a concise ambiguity error that directs the caller to choose the intended semantic relationship.

Silently translating an arbitrary `belongs_to` write would preserve the ambiguity this experiment is intended to remove.

`resolve_subject_hierarchy` is already defined as a broad-to-specific taxonomic path. In legacy mode it continues to create `belongs_to` edges. In typed mode it creates `is_a` edges. Its response reports the relationship type used. System membership is added independently with `set_type_relationship(..., relationship="part_of")` after both stable types have been resolved.

Relationship retirement remains exact: source ID, relationship type and target ID identify the edge. Existing MCP tool names and stable IDs are not changed.

## Bounded classification and traversal

The progressive vocabulary-navigation architecture remains the normal classification and retrieval path. The complete vocabulary index remains administrative/debugging compatibility functionality and is not restored to normal model context.

Mode-aware defaults are:

- legacy roots, children and taxonomic paths follow active `belongs_to` edges;
- typed roots, children and taxonomic paths follow active `is_a` edges.

Navigation endpoints and MCP tools gain an optional `relationship` selector. In typed mode it may select `is_a` or `part_of`; in legacy mode existing calls with no selector remain unchanged. Typed `part_of` traversal is separate from taxonomy traversal and is clearly labelled in responses.

Pagination limits, opaque branch-bound cursors, maximum depth, maximum path count and per-path visited-ID protection remain in force. Cursors include the selected relationship so a cursor cannot be replayed against another semantic traversal.

Type summaries report relationship-specific child counts rather than treating all edges as interchangeable. The path response reports its relationship and mode.

## Classification reasoning contract

MCP induction and tool descriptions instruct models in typed mode to make two independent assessments:

1. identify or create the most specific adequate semantic-head type through bounded `is_a` navigation;
2. only when justified, identify a larger system or domain through separate bounded navigation and add `part_of`.

The model must not infer one answer mechanically from the other and must not manufacture a domain edge merely to make the graph look complete. Semantic judgments stay with the connected AI; the server supplies generic primitives, validation, stable storage and audit.

Subject classification convergence continues to operate on the subject's stable `subject_type_id`. Specificity checks and descendant relations use the mode's taxonomic edge (`belongs_to` in legacy, `is_a` in typed). `part_of` does not make one subject type a taxonomic descendant of another and therefore does not drive subject reclassification or subtype-scoped search.

## Retrieval and search

Typed descendant expansion follows `is_a` only. This preserves the meaning of a type-scoped search: requesting a type includes its subtypes, not everything that is a component or member of that domain.

`part_of` is available for explicit relationship inspection and bounded conceptual navigation but is not silently folded into descendant search. A later experiment may evaluate relationship-aware search, but it is outside this change.

Legacy search continues to follow `belongs_to` as it does now.

## Vocabulary index and REST API

The vocabulary response retains its existing `subject_types`, aliases, relationships and fields. It additionally reports:

- `classification_mode`;
- `taxonomy_relationship` (`belongs_to` or `is_a`);
- supported classification relationship types for the active mode.

Relationships keep their explicit stored discriminator. No compatibility projection rewrites typed edges as `belongs_to`.

REST navigation endpoints accept the same optional relationship selector as MCP. Invalid relationship/mode combinations produce a validation response rather than an empty result.

## Live View

Live View reads the active mode from the API response.

In legacy mode it retains the current collapsible `belongs_to` hierarchy and record wording.

In typed mode:

- the main collapsible hierarchy follows `is_a` only;
- direct `part_of` edges appear in a separately labelled section;
- relationship labels are shown explicitly;
- a stable node may render in multiple taxonomic branches, but every rendered occurrence carries the same stable ID and is described as another path to the same type;
- path traversal is cycle-safe and bounded;
- `is_a` and `part_of` are never styled or described as equivalent.

The page does not attempt to turn the directed graph into one canonical visual tree. It renders taxonomic paths for navigation and direct non-taxonomic connections for explanation.

## Test-first implementation

Focused tests are added before each production change and observed failing for the missing typed behaviour. Tests cover generic invariants rather than only fixture strings:

- mode parsing, default legacy behaviour and invalid-mode rejection;
- unchanged single-parent replacement in legacy mode;
- distinct storage of `is_a` and `part_of` edges between stable IDs;
- multiple active typed parents without node duplication;
- idempotent duplicate-edge handling and exact retired-edge tombstones;
- per-relationship cycle rejection;
- typed hierarchy resolution producing `is_a` and legacy resolution producing `belongs_to`;
- explicit rejection of ambiguous `belongs_to` writes in typed mode;
- separate bounded traversal for `is_a` and `part_of`, including cursor binding and truncation limits;
- taxonomic classification and retrieval following `is_a` but not `part_of`;
- modifier-like input remaining attributes or being rejected as unjustified type nodes;
- mode-aware MCP instructions, schemas and REST responses;
- Live View relationship separation and stable-ID reuse.

Representative fixtures include the user-supplied examples and at least one unrelated semantic pattern. Fixture names may appear in tests, experiment scripts and test reports only.

## Controlled comparison experiment

A small deterministic experiment runs the same inputs against two newly created temporary databases:

- legacy mode records the best available single `belongs_to` parent under current behaviour;
- typed mode records independently justified `is_a` and optional `part_of` edges.

The experiment reports stable type IDs, node count, active edges, root-to-type taxonomic paths, separate membership edges, modifier handling and any rejected relationships. It does not call an external model or claim to measure universal classifier quality; it compares the two storage/traversal semantics using controlled decisions that exercise the public service interfaces.

It uses only a small fixture set. It does not seed historical classifications or populate the cleared V2 service database.

## Special-case inspection

Before completion, scan production source, prompt and static UI files for every supplied example term and close variants. Matches are permitted only in tests, experimental fixtures and documentation. Also inspect changed control flow to confirm decisions branch on mode and relationship semantics, never canonical type names or IDs.

## Expected files

The exact list may narrow after test design, but the change is expected to touch:

- `app/core/config.py` for validated mode selection;
- `app/services/semantic.py` for mode-aware relationship invariants;
- `app/services/vocabulary_navigation.py` for typed bounded traversal;
- `app/services/classification.py` and descendant-search helpers for mode-aware taxonomy semantics;
- `app/services/v2.py` for mode metadata and hierarchy resolution;
- `app/api/mcp_v2.py`, `app/api/v2.py` and schemas for compatible typed contracts;
- `app/static/live.html` for mode-aware graph presentation;
- focused service, API, MCP, search and UI tests;
- a small comparison script or test report;
- concise V2 documentation.

No migration or seeding script is expected.

## Completion criteria

The experiment is ready for a larger cleared-database comparison only when:

- the full existing suite and all new tests pass in both relevant mode configurations;
- legacy fixtures demonstrate unchanged behaviour;
- typed fixtures demonstrate stable identity across multiple semantic paths;
- the unrelated fixture demonstrates the same mechanism outside the supplied examples;
- the complete production-code special-case scan is clean;
- the controlled comparison output is captured;
- known effects on search, classification and Live View are documented;
- no large dataset has been loaded.

Typed mode is not predetermined to be superior. The final report must identify semantic improvements, regressions and unresolved operational costs.
