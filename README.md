# TasteGraph 1.1

AI-native structured experience storage and personalised review interpretation.

## Local setup in PyCharm

1. Open this folder as the project.
2. Create a Python 3.11+ virtual environment.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Copy `.env.example` to `.env`.
5. Run migrations:

```bash
alembic upgrade head
```

6. Seed schemas and demo identities:

```bash
python -m scripts.seed
```

7. Run `run.py` in PyCharm, or:

```bash
python run.py
```

Open http://127.0.0.1:8000 and http://127.0.0.1:8000/docs.

Development API key: `dev-secret` in `X-API-Key`. Client identity is derived from the credential; `X-Client-ID` is not trusted.

## Connect ChatGPT with OAuth

TasteGraph includes a tool-only MCP app at `/mcp` with three actions:

- `search` — find reviews belonging to the connected TasteGraph user
- `fetch` — read one complete review
- `save_review` — resolve/create a subject and save a user-approved review, with retry-safe idempotency

The production connection uses OAuth 2.1 Authorization Code + PKCE. ChatGPT receives a short-lived scoped token; it never receives the connection code, API key or TasteGraph owner ID.

Before the first deployment, add a long random `OAUTH_CONNECTION_CODE` in Railway. After deployment, run `python -m scripts.list_users`, then set `OAUTH_OWNER_USER_ID` to Robert's existing TasteGraph user UUID and allow Railway to redeploy.

## Import the open UCI recipe reviews

After migrations and `python -m scripts.seed`, run:

```bash
python -m scripts.import_uci_recipe_reviews --representative-reviews 100 --load
```

This downloads the CC BY 4.0 UCI dataset and chooses one evidence-rich review from each of its 100 recipes. It writes the converted records to `data/uci_recipe_reviews_100.json` and loads them into the configured database. Re-running it is safe: stable source IDs prevent duplicates.

The original review text, 0-5 star score, timestamp, votes, source record ID, licence and attribution are preserved in provenance. The importer interprets explicit statements about flavour, clarity, timing, ingredient availability, difficulty, repeat-worthiness and modifications. Each interpretation retains its supporting source sentence; anything unsupported remains `null`.

This project already includes the checked 100-review bundle. To load that exact batch without downloading or regenerating it, run:

```bash
python -m scripts.import_uci_recipe_reviews --load-bundle data/uci_recipe_reviews_100.json
```

To inspect loaded records, use `GET /api/v1/subjects?subject_type=recipe` and `GET /api/v1/experiences?subject_type=recipe&publication_status=published` in `/docs`.

## Development data reset

A guarded reset page is available at `/development/reset`. It is hidden and returns 404 unless explicitly enabled:

```text
ENABLE_DEVELOPMENT_RESET=true
DEVELOPMENT_RESET_TOKEN=<a separate random value of at least 20 characters>
```

The page requires the exact confirmation phrase and reset token, then permanently removes v1/v2 review and knowledge data. It preserves users, schemas, OAuth connections and capability credentials. Disable `ENABLE_DEVELOPMENT_RESET` when the control is not needed.

## Tests

```bash
pytest -q
```

## Railway deployment

1. Push this repository to GitHub.
2. Create a Railway project from the GitHub repository.
3. Add a PostgreSQL service.
4. In the API service, use **Add Reference** and select the Postgres service's
   `DATABASE_URL`. Do not paste the displayed `${{Postgres.DATABASE_URL}}` text
   as a plain value and do not add quotes around it.
5. Add variables:

```text
ENVIRONMENT=production
APP_SECRET=<random secret>
DEVELOPMENT_API_KEY=<long random key>
CLIENT_API_KEYS={} # optional JSON map of revocable client credentials and scopes
OAUTH_OWNER_USER_ID=<Robert user UUID>
OAUTH_CONNECTION_CODE=<long random connection code>
PUBLIC_BASE_URL=https://<your-domain>
ALLOWED_HOSTS=["<your-domain>","<railway-domain>"]
CORS_ORIGINS=["https://<your-domain>"]
```

`railway.json` configures:
- pre-deploy migrations: `alembic upgrade head`
- Uvicorn start command using Railway's `$PORT`

The application converts Railway's `postgresql://` URL to SQLAlchemy's
`postgresql+psycopg://` form because the project uses Psycopg 3. On a successful
deployment, Alembic logs `Context impl PostgresqlImpl`. An unresolved reference
or a Railway deployment that falls back to SQLite now stops with a specific
configuration error before the API starts.
- readiness health check at `/health/ready`

After the first deployment, run `python -m scripts.seed` from a Railway shell or one-off command.

## Architecture implemented

- Generic Subject model for recipe, restaurant and future domains
- Versioned schema registry
- Domain-specific Pydantic validation stored as JSON/JSONB
- Draft-first publication with explicit approval/version
- Development API-key authentication plus optional revocable, scoped client credentials
- Central read policy: public lists expose only published public reviews; drafts/private data require authentication
- Canonical subject resolution and version-checked draft editing
- OAuth 2.1 Authorization Code + PKCE, dynamic client registration, refresh-token rotation and scoped access tokens
- ChatGPT-compatible MCP endpoint with authenticated `search`, `fetch` and `save_review` tools
- Idempotency keys
- Provenance, consent, ownership and visibility
- Pairwise reviewer-reader alignment
- Reader-specific relevance output
- Audit log
- Soft deletion
- Request IDs and consistent JSON errors
- Request-size limit
- Pagination-ready list endpoints
- Alembic migrations
- SQLite locally; PostgreSQL on Railway


## Safe read and editing routes

- Public lists return only experiences that are both `published` and `public`.
- Exact IDs may also retrieve a published `unlisted` experience.
- Drafts and private experiences require an `experience:read` credential.
- `aggregate_only` experiences are never returned as individual reviews.
- Resolve subjects with `GET /api/v1/subjects/resolve?subject_type=recipe&canonical_key=...`.
- Edit a draft with `PATCH /api/v1/experiences/{id}` and its current `expected_version`.

Optional client credentials are configured as a JSON object in `CLIENT_API_KEYS`. Each object key is the verified client ID; each value contains `secret`, `subject`, and `scopes`. Example:

```json
{"claude-code":{"secret":"replace-with-a-long-random-secret","subject":"robert","scopes":["subject:write","experience:draft","experience:edit","experience:publish"]}}
```

