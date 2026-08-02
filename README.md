# TasteGraph 1.0

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

Development API key: `dev-secret` in `X-API-Key` or `Authorization: Bearer dev-secret`.

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

## Tests

```bash
pytest -q
```

## Railway deployment

1. Push this repository to GitHub.
2. Create a Railway project from the GitHub repository.
3. Add a PostgreSQL service.
4. Set `DATABASE_URL` to Railway's PostgreSQL connection variable/reference.
5. Add variables:

```text
ENVIRONMENT=production
APP_SECRET=<random secret>
DEVELOPMENT_API_KEY=<long random key>
PUBLIC_BASE_URL=https://<your-domain>
ALLOWED_HOSTS=["<your-domain>","<railway-domain>"]
CORS_ORIGINS=["https://<your-domain>"]
```

`railway.json` configures:
- pre-deploy migrations: `alembic upgrade head`
- Uvicorn start command using Railway's `$PORT`
- readiness health check at `/health/ready`

After the first deployment, run `python -m scripts.seed` from a Railway shell or one-off command.

## Architecture implemented

- Generic Subject model for recipe, restaurant and future domains
- Versioned schema registry
- Domain-specific Pydantic validation stored as JSON/JSONB
- Draft-first publication with explicit approval/version
- Development API-key authentication behind a replaceable scope dependency
- OAuth discovery placeholder
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
