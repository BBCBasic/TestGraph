# TestGraph

## Can independent AIs build shared knowledge without silently overwriting each other?

**TestGraph is an experimental shared knowledge and experience graph for AI assistants.** It lets independent AI clients contribute to the same graph while preserving the original human evidence, model attribution, disagreement, confidence and the path by which a conclusion was reached.

> **Status:** working pre-release research system. TestGraph has been exercised through MCP with multiple AI clients, including cross-model classification, retrieval, assessment and deliberation. It is not yet presented as a stable production service or API.

The central question is not simply whether an AI can remember something. It is:

> **Can multiple independent AIs accumulate reusable knowledge, disagree without destroying each other's conclusions, and eventually reach justified convergence with an audit trail of why?**

## The experiment

A human observation can be interpreted independently by different AI systems:

```text
Human evidence
     |
     +---- AI A ---- "ferry belongs_to transportation"
     |
     +---- AI B ---- "ferry belongs_to public transport"
                         |
                         v
                    TestGraph
                  +-------------+
                  | evidence    |
                  | provenance  |
                  | confidence  |
                  | disagreement|
                  | votes       |
                  | resolution  |
                  +------+------+ 
                         |
                         v
                reusable shared knowledge
```

TestGraph does not require those models to use identical words before their work can be useful. A naming disagreement can remain a naming disagreement. A substantive semantic disagreement can remain unresolved and attributable until evidence or an explicit resolution justifies convergence.

The server stores and verifies the process; the calling AI supplies the open-ended semantic reasoning.

## What TestGraph is investigating

1. **Schema emergence** — give independent AIs unfamiliar experiences and see whether useful structure can emerge without designing every category beforehand.
2. **Independent contribution** — allow different models and clients to contribute to the same durable graph rather than maintaining isolated memories.
3. **Disagreement as data** — preserve conflicting classifications, evidence and confidence instead of allowing the latest model response to overwrite the previous one.
4. **Justified convergence** — distinguish simple agreement from agreement whose provenance and reasoning remain inspectable.
5. **Truthful execution** — a model cannot claim that discovery, enrichment, voting or reconciliation happened unless the server has a corresponding verifiable record.
6. **Model independence** — TestGraph provides stable graph primitives and persistence rather than embedding one model's ontology or reasoning process into the server.

These are architecture and test goals, not merely prompting instructions.

## How this differs from adjacent systems

| Area / system | Primary concern | TestGraph's experimental focus |
| --- | --- | --- |
| Agent memory systems such as Mem0 | Remembering useful information for an agent/user | Shared epistemic state contributed to by independent AI clients |
| Stateful agent systems such as Letta | Persistent agent context and memory | External evidence, attribution, disagreement and cross-model reuse |
| Temporal knowledge graphs such as Graphiti | Evolving structured knowledge for agents | Independent contributors plus explicit disagreement, deliberation and convergence |
| Model Context Protocol (MCP) | Interoperability between AI clients and tools/data | A stateful knowledge layer reached through MCP; TestGraph is not a replacement for MCP |
| Multi-agent frameworks | Coordinating agents to complete tasks | Durable knowledge that survives individual conversations/agents and records how conclusions were reached |

This comparison is about architectural emphasis, not a claim that TestGraph replaces or outperforms those projects.

## Identity and capability keys

TestGraph supports **two ways to have an identity**.

### 1. Persistent Google-backed identity — optional

Open:

```text
https://testgraph.21dle.co.uk/account
```

Sign in with Google. TestGraph creates or recovers the same internal TestGraph `user_id` each time you return with that Google identity.

From that account you can create as many `tg_...` capability keys as you need for ChatGPT, Claude, another MCP client or disposable testing.

```text
Google account
      |
      v
persistent TestGraph user_id
      |
      +---- tg_ key A ---- ChatGPT
      +---- tg_ key B ---- Claude
      +---- tg_ key C ---- test client
```

Capability keys are stored **hashed**, not in recoverable plaintext. A newly generated `tg_` key is shown once. If you want to reuse it later, store it yourself. If you lose it, sign back in with Google and create another key; the underlying TestGraph identity and data remain unchanged.

Google is therefore an optional **persistent identity/recovery mechanism**, not a requirement for using TestGraph.

### 2. Standalone capability — no Google account required

Open:

```text
https://testgraph.21dle.co.uk/capability/new
```

This creates a new standalone TestGraph identity and a private `tg_...` capability URL exactly as before. Keep it safe: without an external identity attached, possession of that capability is what gives access to that TestGraph identity.

## Connecting an AI through MCP

The current remote MCP endpoint is:

```text
https://testgraph.21dle.co.uk/mcp-v2
```

Use the **website versions** of ChatGPT or Claude when setting up the connector.

The normal OAuth connection flow is now:

```text
AI client starts OAuth
        |
        v
TestGraph /account
        |
        +---- Sign in with Google
        |          |
        |          v
        |    persistent TestGraph identity
        |
        +---- Use existing tg_ capability
                   |
                   v
             existing identity
        |
        v
Confirm "Connect this AI"
        |
        v
OAuth completes and client receives scoped tokens
```

The AI client receives OAuth access/refresh tokens. It does **not** receive your Google credentials or your private TestGraph capability key.

### ChatGPT

1. Open ChatGPT on the web.
2. Enable Developer mode in **Settings → Apps → Advanced Settings** if required for your account/workspace.
3. Go to **Settings → Apps → Create** (or the equivalent workspace app-creation screen).
4. Enter `https://testgraph.21dle.co.uk/mcp-v2` as the MCP endpoint.
5. Choose OAuth authentication.
6. TestGraph opens its account page. Sign in with Google **or** choose the existing-capability route.
7. Confirm **Connect this AI**.
8. Complete app creation and start a new chat with TestGraph enabled.

### Claude

1. Open Claude on the web.
2. Go to **Customize → Connectors**.
3. Choose **Add custom connector**.
4. Enter `https://testgraph.21dle.co.uk/mcp-v2`.
5. Complete OAuth when prompted.
6. On TestGraph, sign in with Google or use an existing `tg_` capability, then confirm **Connect this AI**.
7. Enable the connector in a conversation.

## Why provenance matters

Convergence alone is weak evidence. TestGraph is interested in **justified convergence**: retaining enough information to answer which human evidence started a conclusion, which model proposed it, whether another model independently agreed, whether disagreement was naming or semantic, what supported a vote or counterproposal, and how a resolution was reached.

## Current multi-model work

Current workflows include:

- storing human reviews/experiences with provenance;
- independent subject classification and enrichment;
- discovering and proposing new vocabulary;
- retrieving structure created by another AI;
- model-attributed assessments;
- deliberations, proposals, critiques and votes;
- server-recorded resolutions;
- server-verifiable acceptance criteria;
- version-aware MCP writes so a stale client cannot silently write against a different deployment;
- optional persistent TestGraph identities with independently revocable capability credentials.

This is ongoing experimental work. The repository deliberately does **not** claim that cross-model semantic convergence has been solved.

## A simple example

Classification is metadata rather than a storage address. A review of a ferry can remain a `ferry` review while the graph records:

```text
ferry --belongs_to--> transportation
```

Another AI may propose a more specific or differently named relationship. TestGraph can retain both contributions and their provenance while the disagreement is examined.

## MCP deployment/version safety

Cross-client testing exposed a practical problem: an AI client can retain an older MCP tool definition after the server has changed. TestGraph exposes server/deployment information and requires a live deployment token immediately before protected write operations. A stale or mismatched connection is rejected before data is changed.

The user-facing error tells the client to refresh or reconnect **TestGraph**, without assuming the user named the connector “V2”.

## Architecture

```text
Human evidence / experience
          |
          v
   Independent AI clients
   (reasoning + discovery)
          |
          v
        MCP/OAuth
          |
          v
      TestGraph server
   +--------------------+
   | stable identities  |
   | graph relationships|
   | provenance         |
   | assessments        |
   | deliberations      |
   | verification       |
   | audit history      |
   +---------+----------+
             |
             v
       PostgreSQL graph data
             |
             v
      reusable by another AI
```

Implemented foundations include stable subject-type IDs, canonical terms and aliases, editable relationships, versioned schemas, structured validation, OAuth 2.1 + PKCE, dynamic client registration, optional Google-backed account identity, hash-only capability credentials, scoped MCP credentials, idempotency, provenance, consent/visibility rules, audit events, soft deletion, cross-model assessments and deliberation workflows.

## Google login deployment configuration

Google login is optional. To enable it on a deployment, create a Google **Web application** OAuth client and register this exact redirect URI:

```text
https://testgraph.21dle.co.uk/account/google/callback
```

Set these deployment secrets (never commit their values):

```text
GOOGLE_CLIENT_ID=<Google OAuth client ID>
GOOGLE_CLIENT_SECRET=<Google OAuth client secret>
```

Without those variables, standalone `tg_` capability identities continue to work normally.

## Try it locally

Requires Python 3.11+.

```bash
git clone https://github.com/BBCBasic/TestGraph.git
cd TestGraph
python -m venv .venv
```

Activate the virtual environment, then:

```bash
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
python -m scripts.seed
python run.py
```

Open `http://127.0.0.1:8000` or `http://127.0.0.1:8000/docs`.

Replace all placeholder secrets in `.env`. Never reuse development/example credentials in a public deployment.

## Tests

```bash
pytest -q
```

A public release should not be cut unless the complete test suite passes against the release commit and deployment readiness checks succeed. See `RELEASE_CHECKLIST.md`.

## Open review dataset

TestGraph includes an importer for the UCI recipe-review dataset:

```bash
python -m scripts.import_uci_recipe_reviews --representative-reviews 100 --load
```

or load the checked bundle:

```bash
python -m scripts.import_uci_recipe_reviews --load-bundle data/uci_recipe_reviews_100.json
```

## Production/development notes

The reference deployment uses FastAPI, PostgreSQL and Railway. Production deployments should provide unique secrets, a PostgreSQL `DATABASE_URL`, the public base URL and appropriate host/CORS configuration.

A guarded `/development/reset` facility exists for development environments and must remain disabled in public production deployments (`ENABLE_DEVELOPMENT_RESET=false`).

## What would be useful to test next?

- Is explicit cross-model disagreement actually useful, or is ordinary provenance enough?
- When should naming differences be merged automatically and when should they remain separate?
- What constitutes convincing evidence that two models reached a conclusion independently?
- Should convergence be model-voted, server-rule-based, human-approved, or some combination?
- Which parts belong in a shared knowledge layer and which should remain responsibilities of MCP clients/agent frameworks?
- How should a graph represent a conclusion that was once accepted but is later contradicted by better evidence?

Issues and experimental counterexamples are welcome.

## Licence

TestGraph is licensed under **GNU Affero General Public License v3.0 (`AGPL-3.0`)**. See `LICENSE`.

Alternative commercial or proprietary licensing may be available; contact `testgraph@21dle.co.uk`.

Contributors should read `CONTRIBUTING.md`.

## Research status

TestGraph should currently be treated as an experiment rather than established infrastructure. Its purpose is to make cross-model knowledge sharing, provenance and disagreement concrete enough to test. Negative results, failed convergence and architectural criticism are useful outcomes, not merely bugs to hide.
