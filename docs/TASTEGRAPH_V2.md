# TasteGraph v2 alpha

TasteGraph v2 replaces domain-specific storage schemas with a hierarchical concept registry and canonical vocabulary while keeping direct user experience, external evidence, and AI-derived assessment as separate provenance classes.

## Compatibility

The existing integrations are deliberately unchanged:

- existing MCP: `/mcp`
- existing ChatGPT Action schema: `/actions/openapi.json`

The v2 alpha is exposed separately:

- v2 MCP: `/mcp-v2`
- v2 ChatGPT Action schema: `/actions-v2/openapi.json`
- direct v2 REST API: `/api/v2`

This allows v2 to be tested without breaking the currently connected Claude/ChatGPT clients.

## Storage model

Core v2 tables:

- `concepts` — hierarchical canonical paths such as `product.electronics.camera.action_camera`
- `concept_fields` — accepted canonical dimensions for a concept
- `field_aliases` — alternate AI/user terminology mapped to canonical fields
- `v2_subjects` — real subjects attached to a concept
- `v2_experiences` — approved direct user experiences
- `sources` — external evidence/provenance
- `assessments` — AI-derived analysis, kept separate from direct experience

The original user wording and the AI's submitted field names are retained alongside canonical structured data so records can be reinterpreted later.

## Canonicalisation contract

Before a write, an AI should query the concept vocabulary. TasteGraph resolves submitted dimensions in this order:

1. known alias
2. existing canonical field
3. explicit new-field proposal
4. reject unknown field

Example canonical vocabulary:

- `autofocus` -> `AF`
- `auto_focus` -> `AF`
- `focus tracking` -> `AF.tracking`
- `subject_tracking` -> `AF.tracking`

AI clients can propose a genuinely new field, but TasteGraph owns the resulting canonical vocabulary and rejects alias collisions.

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

V2 MCP and ChatGPT Action write operations require an `idempotency_key`. Reusing the same key for the same payload returns the previous write result. Reusing it for different content is rejected.

## Current test target

The smoke suite verifies:

- concept hierarchy creation
- canonical field/alias convergence across different AI terminology
- rejection of unknown fields until explicitly proposed
- preservation of original and canonical experience data
- v2 MCP and Action write contracts
- idempotency replay/conflict behaviour
- complete Alembic migration chain

## Next integrations

OSM/Mangrove federation is intentionally not part of this first alpha. Once multi-AI concept writes are proven, place resolution and open-review evidence can be added as sources feeding `assessments` without changing the core model.
