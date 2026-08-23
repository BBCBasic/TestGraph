# Security Policy

TestGraph is currently an experimental pre-release system. Security-sensitive deployments should be treated as development/research deployments until a release is explicitly declared stable.

## Secrets

Never commit API keys, OAuth connection codes, access or refresh tokens, database credentials, owner identifiers, Railway variables, `.env` files, or production session material.

Use unique secrets for each environment and client. Rotate any credential that may have appeared in source control, logs, screenshots, issue text, test fixtures, chat transcripts, deployment output, or other shared material.

Before making this repository public, review the **entire Git history**, not just the current tree, for secrets and personal data. Removing a secret from the latest commit does not remove it from repository history.

## Production safeguards

Production deployments should, at minimum:

- use PostgreSQL rather than a local SQLite fallback;
- set `ENVIRONMENT=production`;
- keep `ENABLE_DEVELOPMENT_RESET=false`;
- use a long random `APP_SECRET`;
- use unique, scoped and revocable client credentials;
- restrict `ALLOWED_HOSTS` and `CORS_ORIGINS` to required origins;
- keep OAuth connection codes and owner identifiers in deployment secrets only;
- use HTTPS for public endpoints;
- run migrations before application startup;
- confirm `/health/ready` succeeds after deployment;
- verify that public endpoints cannot expose draft, private or `aggregate_only` experiences.

## Reporting a vulnerability

Until a dedicated private vulnerability-reporting channel is configured, do **not** publish exploit details or live credentials in a GitHub issue.

Repository maintainers should enable GitHub private vulnerability reporting before public release. Once enabled, security reports should use that private channel.

## Supported versions

There is no stable supported release yet. Security fixes currently apply to the latest `master` branch only.
