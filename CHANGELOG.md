# Changelog

All notable changes to TestGraph will be documented in this file.

## Unreleased

### Fixed

- Fixed the live Explorer's Public Subjects panel so it derives from every current public, published review rather than only the 100 newest reviews. Subjects now appear and disappear immediately with review visibility changes and remain deduplicated by stable subject ID.

### Added

- A creator–reviewer classification lifecycle: each subject's creation proposal is retained, one review from a different authenticated client confirms agreement or opens a dispute, and saving enrichment hands off immediately into the durable classification workflow.
- Cross-model vocabulary convergence at typed hierarchy write time: a model proposing a new peer beside another client's existing types must explicitly reuse an equivalent stable type as an alias or justify creating a distinct type.
- A stable synthetic universal root, `.`, for typed `is_a` classification. Every semantic type is reachable from it, progressive traversal begins there, and the live hierarchy can visually promote its children while preserving the real graph internally.
- Synthetic-root integrity validation covering orphan semantic types, illegal root classifications, root identity and illegal parent edges.
- Bounded, paginated MCP and REST vocabulary navigation for root discovery, immediate-child discovery and active root-to-type paths.
- Progressive write-classification and retrieval guidance using ranked candidate branches, retained fallbacks and backtracking rather than a complete vocabulary download.
- A configurable 10,000–50,000-node benchmark comparing full-index and progressive traversal payload, context, call, latency and backtracking measurements.
- `affirm_subject_classification` lets a different authenticated client confirm and lock an already-correct creation proposal without forcing a fake reclassification.
- Subject-level classification review records agreement as confirmed and disagreement as disputed; protocol suffixes and caller-reported model labels cannot manufacture an independent client identity.
- Persistent classification decision audit records, including model identity, evidence, reason, prior type, target type, and outcome.
- Explicit, governed reopening for user correction, contradictory evidence, retired types, or vocabulary invalidation; ordinary later disagreement is recorded without reopening a confirmed classification.
- MCP tools to inspect classification state, propose a refinement, and deliberately reopen a settled classification.

## v0.1.1 — 2026-08-23

Release-candidate patch following the first public-release tag.

### Fixed

- Added a human-friendly web page for creating a private capability URL.
- Preserved JSON compatibility for API clients using `/capability/new`.
- Fixed the capability-page response header bug that caused Railway to return `upstream error`.
- Added an external production smoke test covering the live custom-domain deployment.

### Verified

The production smoke run passed checks for the homepage, database readiness, OAuth metadata, unauthenticated MCP-v2 handling, public vocabulary, capability HTML rendering, and capability JSON compatibility.

## v0.1.0 — 2026-08-23

Initial experimental release.

### Included

- AI-native review and experience graph with stable subject identities.
- Shared vocabulary with aliases, broader/narrower semantic relationships, and controlled disagreement.
- Evidence-backed subject enrichment and governed location assertions.
- Exact-experience AI assessments with preserved provenance.
- Cross-model deliberations, attributed contributions, voting, resolution, and induction guidance.
- OAuth 2.1 + PKCE MCP v2 access, exercised with both ChatGPT and Claude.
- Idempotent write handling and structured conflict errors.
- Batch/reconciliation guidance for larger cross-model jobs.
- PostgreSQL/Alembic persistence and Railway deployment support.
- Public UCI recipe review dataset integration under CC BY 4.0 with explicit attribution.

### Licensing

TestGraph source code is licensed under AGPL-3.0. Alternative licensing arrangements may be available via testgraph@21dle.co.uk. Third-party dataset licensing is documented separately in `data/README.md`.

### Status

This is an experimental first release, not a declaration of a stable production API. The MCP/tool surface and graph-governance rules may continue to evolve.
