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

TestGraph overlaps with several important areas of current AI infrastructure, but is exploring a different layer.

| Area / system | Primary concern | TestGraph's experimental focus |
| --- | --- | --- |
| Agent memory systems such as Mem0 | Remembering useful information for an agent/user | Shared epistemic state contributed to by independent AI clients |
| Stateful agent systems such as Letta | Persistent agent context and memory | External evidence, attribution, disagreement and cross-model reuse |
| Temporal knowledge graphs such as Graphiti | Evolving structured knowledge for agents | Independent contributors plus explicit disagreement, deliberation and convergence |
| Model Context Protocol (MCP) | Interoperability between AI clients and tools/data | A stateful knowledge layer reached through MCP; TestGraph is not a replacement for MCP |
| Multi-agent frameworks | Coordinating agents to complete tasks | Durable knowledge that survives individual conversations/agents and records how conclusions were reached |

This comparison is about architectural emphasis, not a claim that TestGraph replaces or outperforms those projects.

## Why provenance matters

Convergence alone is weak evidence. Many systems can make several values collapse into one canonical value.

TestGraph is interested in **justified convergence**: retaining enough information to answer questions such as:

- Which human evidence started this conclusion?
- Which model proposed the classification?
- Did another model independently agree?
- Was the disagreement merely terminology or genuinely semantic?
- What evidence supported a vote or counterproposal?
- Who or what resolved it?
- Can a later AI inspect that history rather than trusting an unexplained canonical value?

## Current multi-model work

The project has been exercised with independent AI clients against the same TestGraph instance. Current workflows include:

- storing human reviews/experiences with provenance;
- independent subject classification and enrichment;
- discovering and proposing new vocabulary;
- retrieving structure created by another AI;
- model-attributed assessments;
- deliberations, proposals, critiques and votes;
- server-recorded resolutions;
- server-verifiable acceptance criteria;
- version-aware MCP writes so a stale client cannot silently write against a different deployment.

This is ongoing experimental work. The repository deliberately does **not** claim that cross-model semantic convergence has been solved.

## A simple example

Classification is metadata rather than a storage address. A review of a ferry can remain a `ferry` review while the graph records relationships such as:

```text
ferry --belongs_to--> transportation
```

Another AI may propose a more specific or differently named relationship. TestGraph can retain both contributions and their provenance while the disagreement is examined. A later search can still use the useful structure without pretending that unresolved semantic questions have disappeared.

Reviews are stored against stable `subject_type_id` values. Flexible input is resolved through canonical subject types and globally unique aliases; ordinary case, punctuation, possessive and plural differences are normalised mechanically. Reusable structured fields have stable IDs and can be attached to multiple subject types.

## MCP

TestGraph exposes a tool-only MCP application. The current multi-model integration is exercised through `/mcp-v2` using OAuth 2.1 Authorization Code + PKCE.

A connected AI receives a short-lived scoped token; it does not receive the user's private capability key, connection secret or owner identifier.

The MCP surface includes search/fetch/save operations plus graph, assessment, deliberation and reconciliation tools. Because this is a research system, the deployed MCP schema should currently be treated as authoritative.

### Deployment/version safety

Cross-client testing exposed a practical problem: an AI client can retain an older MCP tool definition after the server has changed. TestGraph therefore exposes server/deployment information and requires a live deployment token immediately before protected write operations. A stale or mismatched connection is rejected before data is changed.

This mechanism is itself experimental and is intended to make cross-client testing failures observable rather than ambiguous.

## Architecture

At a high level:

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

Implemented foundations include stable subject-type IDs, canonical terms and aliases, editable relationships, versioned schemas, structured validation, OAuth 2.1 + PKCE, dynamic client registration, scoped credentials, idempotency, provenance, consent/visibility rules, audit events, soft deletion, cross-model assessments and deliberation workflows.

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

A public release should not be cut unless the complete test suite passes against the release commit and the deployment readiness checks succeed. See `RELEASE_CHECKLIST.md`.

## Open review dataset

TestGraph includes an importer for the UCI recipe-review dataset. It can select representative evidence-rich reviews, retain source attribution/licensing and load them into TestGraph for repeatable experiments.

```bash
python -m scripts.import_uci_recipe_reviews --representative-reviews 100 --load
```

A checked bundle can also be loaded without downloading/regenerating the source dataset:

```bash
python -m scripts.import_uci_recipe_reviews --load-bundle data/uci_recipe_reviews_100.json
```

## Production/development notes

The reference deployment uses FastAPI, PostgreSQL and Railway. `railway.json` runs migrations before deployment and starts Uvicorn using Railway's assigned port. Production deployments should provide unique secrets, a PostgreSQL `DATABASE_URL`, the public base URL and appropriate host/CORS configuration.

A guarded `/development/reset` facility exists for development environments and must remain disabled in public production deployments (`ENABLE_DEVELOPMENT_RESET=false`). See the source, `SECURITY.md` and `RELEASE_CHECKLIST.md` for operational details.

## What would be useful to test next?

External criticism is particularly useful around these questions:

- Is explicit cross-model disagreement actually useful, or is ordinary provenance enough?
- When should naming differences be merged automatically and when should they remain separate?
- What constitutes convincing evidence that two models reached a conclusion independently?
- Should convergence be model-voted, server-rule-based, human-approved, or some combination?
- Which parts belong in a shared knowledge layer and which should remain responsibilities of MCP clients/agent frameworks?
- How should a graph represent a conclusion that was once accepted but is later contradicted by better evidence?

Issues and experimental counterexamples are welcome.

## Licence

TestGraph is licensed under the **GNU Affero General Public License v3.0 (`AGPL-3.0`)**. See `LICENSE`.

The AGPL permits use, modification and redistribution subject to its terms, including its network-source obligations for modified versions used to provide a network service.

Alternative commercial or proprietary licensing may be available; contact `testgraph@21dle.co.uk`.

Contributors should read `CONTRIBUTING.md`.

## Research status

TestGraph should currently be treated as an experiment rather than established infrastructure. Its purpose is to make cross-model knowledge sharing, provenance and disagreement concrete enough to test. Negative results, failed convergence and architectural criticism are therefore useful outcomes, not merely bugs to hide.
