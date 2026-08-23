# Changelog

All notable changes to TestGraph will be documented in this file.

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
