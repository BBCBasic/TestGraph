# TestGraph Public Release Checklist

This checklist separates **preparation** from the irreversible act of making the repository public.

## 1. Release identity

- [ ] Decide the first public version (`0.1.0` is appropriate for an experimental API unless a different scheme is preferred).
- [ ] Confirm the public name is consistently **TestGraph** in code, docs and deployment metadata.
- [ ] Add a short project description and repository topics on GitHub.
- [ ] Decide whether the current hosted service is part of the public release or whether only the source code is public.

## 2. Legal and data provenance

- [x] Choose and add an explicit software licence: **AGPL-3.0**, with alternative commercial/proprietary licensing available by separate agreement via `testgraph@21dle.co.uk`.
- [x] Add contributor terms that preserve the project's ability to offer alternative licences.
- [x] Confirm licences/attribution for bundled third-party datasets. UCI dataset 911 is CC BY 4.0; source, DOI, authors and separate data licence are documented in `data/README.md` and in the 100-review bundle metadata.
- [x] Confirm bundled review/test data is synthetic or openly licensed. The UCI material is openly licensed; current seed data is fully synthetic.
- [x] Remove personal names, private reviews and private identifiers from the current source tree where they are not required as public fixtures.

## 3. Secret and privacy audit

- [x] Scan the current tree for obvious API keys, tokens, passwords, OAuth codes, database URLs, cookies and private IDs found during release preparation; no active production credential is intentionally tracked.
- [x] Rewrite repository branch history: `master` was replaced with a parentless clean release baseline and all historical development branches were deleted.
- [x] Revoke credentials found in history: migration `0017_revoke_exposed_test_capabilities` permanently revokes the two exposed live-test capability hashes when deployed.
- [x] Inspect known examples, generated live-test results, seed data and migration data for sensitive values; current-tree personal demo/migration content has been replaced with synthetic/no-op equivalents.
- [x] Confirm `.env` and local databases are ignored by Git.
- [x] Confirm no production database dump is tracked in the current tree.
- [ ] Resolve GitHub-hosted unreachable-history residue before public visibility. GitHub still serves at least one pre-rewrite commit by direct SHA and closed pull requests retain old commit metadata; this requires GitHub-side purge/archive handling or publication from a fresh repository identity.

## 4. Security boundary

- [x] Verify the production development-reset route is unavailable: production code refuses `/development/reset` even if the feature flag is accidentally enabled, and the homepage uses the same production-safe gate.
- [ ] Verify production cannot silently fall back to SQLite.
- [ ] Verify OAuth tokens are scoped and short-lived and refresh-token rotation works.
- [ ] Verify client credentials are scoped, revocable and identity is derived from the credential rather than caller-supplied headers.
- [ ] Verify public endpoints expose only intended published/public records.
- [ ] Verify drafts, private records and `aggregate_only` experiences cannot leak through search, fetch, related-object or error responses.
- [ ] Enable GitHub private vulnerability reporting.

## 5. Tests and reproducibility

- [ ] Run `pytest -q` from a clean checkout of the proposed release commit.
- [ ] Run tests against the supported Python version(s).
- [ ] Run migrations from an empty database to head.
- [ ] Test migration/startup against PostgreSQL, not only SQLite.
- [ ] Seed a fresh database and exercise the principal HTTP and MCP workflows.
- [ ] Re-run the cross-model TestGraph/Claude/ChatGPT interoperability tests that establish the project's core claim.
- [ ] Confirm idempotent retries do not create duplicate reviews, assessments, deliberation contributions or batch results.
- [ ] Confirm structured conflict/errors are intelligible to an AI caller.

## 6. MCP release surface

- [ ] Capture the exact MCP tool list and schemas intended for the release.
- [ ] Remove obsolete/deprecated tools or mark them clearly.
- [ ] Confirm each mutating tool states its idempotency/retry behaviour.
- [ ] Confirm tools distinguish server-verified facts from AI assertions/proposals.
- [ ] Confirm discovery/search degrades usefully when models disagree on naming.
- [ ] Confirm naming disagreement alone does not block semantic reuse; semantic disagreement remains representable.
- [ ] Confirm batch-job guidance is visible to AI clients so large jobs do not incur unnecessary per-record overhead.
- [ ] Confirm AI guidance/induction data can evolve without silently changing canonical truth.

## 7. Documentation

- [x] Replace the old mixed TasteGraph/TestGraph README heading.
- [x] Remove the published `dev-secret` example from the README.
- [x] Add explicit pre-release status and public-release warning.
- [x] Add `SECURITY.md`.
- [x] Add `LICENSE` and document alternative licensing.
- [x] Add contributor relicensing safeguards.
- [x] Add third-party data licensing/provenance documentation.
- [ ] Document the current MCP tools from the deployed schema.
- [ ] Add a concise architecture diagram or data-flow description.
- [ ] Add one end-to-end example: review → subject resolution → structured assertions → retrieval/reuse.
- [ ] Add one cross-model reconciliation example showing disagreement and resolution.
- [ ] Explain what TestGraph deliberately does **not** do (for example, trusting a model to self-certify execution).

## 8. Deployment gate

- [ ] Deploy the exact proposed release commit to a clean/staging environment.
- [ ] Confirm Alembic reports PostgreSQL and reaches head.
- [ ] Confirm `/health/ready` is healthy.
- [x] Test OAuth connection from at least two independent AI clients (ChatGPT and Claude) against the custom-domain `/mcp-v2` endpoint.
- [ ] Test read, write, retry, reconciliation and search flows end to end.
- [x] Confirm reset/development routes are unavailable publicly.
- [ ] Check logs for secrets, PII and overly detailed exception output.

## 9. Public launch

- [ ] Freeze the release commit.
- [x] Add the chosen licence.
- [ ] Create/update `CHANGELOG.md` with the initial public release notes.
- [ ] Tag the release version.
- [ ] Make the GitHub repository public only after the GitHub unreachable-history residue and release-candidate gates pass.
- [ ] Publish a short statement describing this as an experimental implementation and the specific claims already demonstrated.

## Current blockers to public visibility

As of 23 August 2026, the known hard gates are:

1. **GitHub-side unreachable-history residue:** branch history is clean, but GitHub still serves old unreachable commits by direct SHA and closed PRs retain old commit references. Do not make this repository public until those are purged/hidden by GitHub or a fresh clean repository is used for the public release.
2. **Release-candidate tests and a clean deployment** have not yet been recorded against a frozen commit.

The software-licensing, current-tree data-provenance and branch-history rewrite gates are complete. TestGraph is AGPL-3.0 by default, with separate alternative licensing available by agreement; UCI dataset material remains under CC BY 4.0 with explicit attribution.
