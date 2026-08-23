# TestGraph

**Experimental AI-native experience graph for shared, verifiable memory across AI assistants.**

> **Status:** working pre-release research system. The architecture and MCP workflows have been exercised with multiple AI clients, but the project is not yet presented as a stable production service or API.

TestGraph stores human reviews and AI-derived structure as durable graph knowledge while keeping evidence, provenance and server-verifiable execution separate from model claims. It is designed so that different AI systems can contribute to and reuse the same knowledge without requiring a complete domain schema in advance.

## Project goals

1. **Schema emergence:** Give several AIs unfamiliar experiences and obtain a useful structure without designing the categories beforehand.
2. **Controlled disagreement:** Conflicting classifications converge through evidence, confidence and server rules instead of flip-flopping.
3. **Truthful execution:** Models cannot claim that discovery, enrichment or reconciliation happened unless the server can verify it.
4. **Calling-AI capability:** TestGraph deliberately uses the calling AI as its semantic and discovery engine. The AI should apply its available reasoning, retrieval and tool capabilities to unfamiliar subjects, derive useful structure and relationships, and reconcile evidence without waiting for TestGraph to prescribe a domain-specific form. TestGraph provides stable graph primitives, persistence and server-side verification; the calling AI provides the open-ended intelligence.

These goals are acceptance criteria for TestGraph's architecture and tests, not merely guidance for individual AI clients.

## Standard vocabulary model

Reviews are stored against stable `subject_type_id` values, not DNS-style concept paths. Flexible input is resolved through canonical subject types and globally unique aliases; case, punctuation, possessives and ordinary plurals are normalised mechanically. Unknown types may be created as provisional entries after dictionary lookup.

Classification is separate metadata. For example, `ferry belongs_to transportation` improves broad transportation searches but never changes where a ferry review is stored. `review` is the record type, not a vocabulary node. Reusable structured fields have their own stable IDs and aliases and may be attached to multiple subject types.

Migration `0009_flat_standard_vocabulary` deliberately discards the old v2 concept/review data while preserving users, OAuth state, capability credentials and other authentication data.

## Local setup

1. Create a Python 3.11+ virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and replace every placeholder secret with a private value.
4. Run migrations:

```bash
alembic upgrade head
```

5. Seed schemas and demo identities:

```bash
python -m scripts.seed
```

6. Start the application:

```bash
python run.py
```

Open `http://127.0.0.1:8000` and `http://127.0.0.1:8000/docs`.

Do not reuse example or development credentials outside a local development environment. Secrets, API keys, OAuth connection codes and owner identifiers must never be committed to the repository.

## MCP and OAuth

TestGraph includes a tool-only MCP app. The current multi-model integration is exercised through `/mcp-v2`. Its production connection uses OAuth 2.1 Authorization Code + PKCE. A connected AI receives a short-lived scoped token; it does not receive the connection code, API key or TestGraph owner ID.

The MCP surface includes review search/fetch/save operations and the newer graph/reconciliation capabilities used by multi-model experiments. Treat the deployed MCP schema as authoritative because this experimental surface is still evolving.

Before a deployment, configure a long random `OAUTH_CONNECTION_CODE`, set the appropriate `OAUTH_OWNER_USER_ID`, and ensure production secrets exist only in the deployment environment.

## Import the open UCI recipe reviews

After migrations and `python -m scripts.seed`, run:

```bash
python -m scripts.import_uci_recipe_reviews --representative-reviews 100 --load
```

This downloads the CC BY 4.0 UCI dataset and chooses one evidence-rich review from each of its 100 recipes. It writes the converted records to `data/uci_recipe_reviews_100.json` and loads them into the configured database. Re-running it is safe: stable source IDs prevent duplicates.

The original review text, 0-5 star score, timestamp, votes, source record ID, licence and attribution are preserved in provenance. The importer interprets explicit statements about flavour, clarity, timing, ingredient availability, difficulty, repeat-worthiness and modifications. Each interpretation retains its supporting source sentence; anything unsupported remains `null`.

To load the checked bundle without downloading or regenerating it:

```bash
python -m scripts.import_uci_recipe_reviews --load-bundle data/uci_recipe_reviews_100.json
```

## Development data reset

A guarded reset page is available at `/development/reset`. It is hidden and returns 404 unless explicitly enabled:

```text
ENABLE_DEVELOPMENT_RESET=true
```

The page permanently removes v1/v2 review and knowledge data while preserving users, schemas, OAuth connections and capability credentials. **It must remain disabled in a public production deployment.**

## Tests

```bash
pytest -q
```

A public release should not be cut unless the full test suite passes against the release commit and the deployment readiness check succeeds. See `RELEASE_CHECKLIST.md`.

## Railway deployment

1. Push the repository to GitHub.
2. Create a Railway project from the GitHub repository.
3. Add PostgreSQL.
4. Reference the Postgres service's `DATABASE_URL`; do not paste an unresolved Railway reference as a plain string.
5. Configure production variables, using unique random secrets:

```text
ENVIRONMENT=production
APP_SECRET=<random secret>
DEVELOPMENT_API_KEY=<long random key; development/admin use only>
CLIENT_API_KEYS={}
OAUTH_OWNER_USER_ID=<owner UUID>
OAUTH_CONNECTION_CODE=<long random connection code>
PUBLIC_BASE_URL=https://<your-domain>
ALLOWED_HOSTS=["<your-domain>","<railway-domain>"]
CORS_ORIGINS=["https://<your-domain>"]
ENABLE_DEVELOPMENT_RESET=false
```

`railway.json` configures pre-deploy migrations, Uvicorn startup using Railway's `$PORT`, and readiness checking at `/health/ready`.

The application converts Railway's `postgresql://` URL to SQLAlchemy's `postgresql+psycopg://` form because the project uses Psycopg 3. A production deployment that cannot resolve PostgreSQL should stop rather than silently fall back to SQLite.

## Architecture implemented

- Calling-AI capability as the open-ended semantic and discovery engine, with the server as verification/persistence layer
- Stable flat subject-type IDs
- Canonical terms and aliases
- Editable type relationships used for search rather than storage addresses
- Versioned schema registry
- Domain-specific Pydantic validation stored as JSON/JSONB
- Draft-first publication with explicit approval/version
- Scoped client credentials
- Central read policy
- Canonical subject resolution and version-checked draft editing
- OAuth 2.1 Authorization Code + PKCE, dynamic client registration, refresh-token rotation and scoped access tokens
- MCP endpoint for authenticated AI access
- Idempotency keys
- Provenance, consent, ownership and visibility
- Pairwise reviewer-reader alignment and reader-specific relevance
- Audit log and soft deletion
- Request IDs, consistent JSON errors, request-size limits and pagination-ready endpoints
- Alembic migrations
- SQLite locally; PostgreSQL in production
- Cross-model reconciliation and server-recorded deliberation/assessment workflows

## Safe read and editing rules

- Public lists return only experiences that are both `published` and `public`.
- Exact IDs may also retrieve a published `unlisted` experience.
- Drafts and private experiences require an appropriate read credential.
- `aggregate_only` experiences are never returned as individual reviews.
- Resolve subjects before editing or attaching structured knowledge.
- Version checks protect concurrent draft editing.
- Optional client credentials must use unique, revocable secrets and the minimum scopes required.

## Licence

TestGraph is licensed under the **GNU Affero General Public License v3.0 (`AGPL-3.0`)**. See `LICENSE`.

The AGPL permits use, modification and redistribution subject to its terms, including its network-source obligations for modified versions used to provide a network service.

If AGPL-3.0 does not meet your requirements, **alternative commercial or proprietary licensing may be available**. Contact `testgraph@21dle.co.uk` to discuss a separate licence agreement.

Contributors should read `CONTRIBUTING.md`. Contributions are accepted only on terms that preserve the project's ability to offer alternative licences.

## Before making the repository public

Read `RELEASE_CHECKLIST.md` and `SECURITY.md`. In particular, complete the secret-history review, confirm the public-data boundary, run the complete test suite, and verify a clean deployment from the exact release commit.
