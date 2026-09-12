# TasteGraph v2 alpha

TasteGraph v2 replaces domain-specific storage schemas with a hierarchical concept registry and canonical vocabulary while keeping direct user experience, external evidence, and AI-derived assessment as separate provenance classes.

## Core semantic principle

**TasteGraph does not interpret language.**

Calling AI platforms do the semantic work they are already good at:

- interpret the user's language
- inspect the existing concept vocabulary
- reuse an existing canonical field when it expresses the same meaning
- propose that a new term is an alias of an existing canonical field
- propose a genuinely new canonical field only when no existing field fits

TasteGraph's role is deterministic coordination:

- store canonical concepts and fields
- record semantic proposals with authenticated client provenance
- ensure repeated proposals from one client count only once
- expose unresolved proposals to other clients
- detect disagreement
- promote a semantic alias only after independent clients agree
- preserve the audit trail and canonical stored data

The current promotion threshold is **two independent authenticated clients**. If different clients propose different canonical targets for the same term, the term remains in conflict and is not automatically promoted, even if one target has more votes.

## Compatibility

The existing integrations are deliberately unchanged:

- existing MCP: `/mcp`
- existing ChatGPT Action schema: `/actions/openapi.json`

The v2 alpha is exposed separately:

- v2 MCP: `/mcp-v2`
- v2 ChatGPT Action schema: `/actions-v2/openapi.json`
- direct v2 REST API: `/api/v2`

## Classification modes

V2 can run either classification model without copying subject types or changing their stable IDs:

- `CLASSIFICATION_MODE=legacy` is the default. Active `belongs_to` edges form the current single-parent classification tree, and a replacement parent retires the previous edge.
- `CLASSIFICATION_MODE=typed` enables a directed graph with explicit `is_a` and `part_of` edges. A type may have multiple valid `is_a` parents and zero or more `part_of` targets.

In typed mode, `is_a` answers what a type fundamentally is. `part_of` independently records a justified larger system, domain, structure or concept. Neither implies the other, and callers must not add `part_of` merely to complete a pattern.

The typed `is_a` topology has one synthetic universal root, `.`. It is analogous to the DNS root: infrastructure rather than a semantic category. Every semantic `is_a` type is reachable from it, and a type with no meaningful semantic parent receives an infrastructure `is_a -> .` edge. Adding a meaningful parent removes only that infrastructure edge; multiple valid semantic `is_a` parents remain supported.

The root has a reserved stable ID and cannot be renamed, deleted, aliased, classified, promoted or disputed. It does not privilege `entity` or any other named semantic type. Progressive discovery begins by listing `.`, then pages through its immediate children without loading the complete vocabulary. `part_of` and other relationship meanings remain independent of this topology.

`resolve_subject_hierarchy` remains a broad-to-specific taxonomy operation: it creates `belongs_to` in legacy mode and `is_a` in typed mode. `set_type_relationship` requires an explicit `is_a` or `part_of` in typed mode; ambiguous `belongs_to` writes are rejected.

Bounded root, child and path navigation defaults to the active taxonomic relationship and accepts an explicit relationship selector. Typed subtype search and classification specificity follow `is_a` only; `part_of` remains separately inspectable and does not silently broaden search.

Switching the environment variable changes which edge semantics the running service uses. It does not convert stored edges or rebuild historical classifications.

## Storage model

Core v2 tables:

- `concepts` — hierarchical canonical paths such as `product.electronics.camera.action_camera`
- `concept_fields` — accepted canonical dimensions for a concept
- `field_aliases` — accepted alternate terminology mapped to canonical fields
- `semantic_alias_proposals` — authenticated AI-client proposals awaiting agreement or recording conflict
- `v2_subjects` — real subjects attached to a concept
- `v2_experiences` — approved direct user experiences
- `sources` — external evidence/provenance
- `assessments` — AI-derived analysis, kept separate from direct experience

The original user wording and the AI's submitted field names are retained alongside canonical structured data so records can be reinterpreted later.

## Semantic write contract

Before a write, an AI should query the concept vocabulary.

If the AI wants to save `autofocus` and finds an existing canonical field `AF`, it should use its own language understanding to decide whether they mean the same thing. If so it should:

1. propose `autofocus -> AF`
2. save the current structured value using canonical `AF` until the alias is accepted

The first independent proposal remains pending. A second independent client proposing the same mapping promotes it to an accepted alias. Future clients can then submit `autofocus` directly and TasteGraph deterministically normalises it to `AF`.

If one client proposes `tracking -> AF.tracking` and another proposes `tracking -> AF`, TasteGraph records a conflict and does not choose between them.

For genuinely new dimensions, `proposed_fields` can introduce a new canonical field. The submitted term can be used for that one write, while its alternate names enter the same client-consensus process rather than becoming accepted aliases immediately.

## Concept creation

A concept need not exist in advance. The first valid write can create a hierarchy, for example:

`product.electronics.camera.action_camera`

Missing parent concepts are created automatically. Fields on parent concepts are inherited by descendants.

## Provenance rule

Do not collapse these into one review object:

1. direct user experience
2. external human evidence
3. AI-derived assessment

A direct user experience requires explicit user approval. An assessment may analyse external reviews or predict user fit, but it is stored as `ai_derived_assessment`, never as though the user personally experienced the subject.

## Retry safety

V2 MCP and ChatGPT Action experience/assessment writes require an `idempotency_key`. Reusing the same key for the same payload returns the previous write result. Reusing it for different content is rejected.

## Current test target

The test suite verifies:

- concept hierarchy creation
- new canonical fields do not silently create accepted aliases
- one AI proposal remains pending
- repeated proposals from the same client do not manufacture consensus
- a second independent client can promote a matching alias
- conflicting AI proposals block automatic promotion
- accepted aliases subsequently normalise deterministically
- rejection of unknown fields when the caller neither reuses canonical vocabulary nor proposes a genuinely new field
- preservation of original and canonical experience data
- v2 MCP and Action semantic proposal contracts
- OAuth resource isolation
- idempotency replay/conflict behaviour
- complete Alembic migration chain

## Next integrations

OSM/Mangrove federation is intentionally separate from semantic governance. External place/review evidence can later feed `sources` and `assessments` without making TasteGraph itself a language-reasoning system.
