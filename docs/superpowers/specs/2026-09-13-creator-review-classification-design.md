# Creator–Reviewer Classification Design

## Goal

Make V2 classification converge after one independent review of the classification established when a subject is created.

The required lifecycle is:

1. AI 1 creates a subject with an initial type. This is the creation proposal and the subject is provisional.
2. AI 2 later touches the subject, inspects its classification and submits one explicit classification decision.
3. Agreement confirms and locks the initial type. Disagreement makes the classification disputed and starts the existing debate/resolution path.

AI 1 is not asked to return for a third decision.

## Semantics

Creation is an attributed classification proposal, not a row in `subject_classification_decisions` and not a formal review vote. The first qualifying post-creation decision is compared with that proposal:

- same type: confirm and lock;
- different supported type: preserve both positions and mark disputed;
- same creator identity: do not settle; keep the workflow waiting for an independent reviewer.

The audit response must display the creation proposal separately from formal decision history so callers can see exactly why the classification settled.

## Identity boundary

The server can trust the authenticated OAuth client identity because it resolves that identity itself. The exact model name remains caller-reported metadata.

For convergence, independence is therefore enforced primarily by authenticated client identity: the reviewing decision must come from a different OAuth client from the creation proposal. Changing only `source_model` cannot manufacture independence.

The subject stores immutable proposal attribution for the current classification round:

- proposed type ID;
- proposing OAuth client ID;
- caller-reported model label when supplied;
- proposal timestamp.

When a classification is reopened, the restored baseline becomes the new proposal and receives new proposal attribution from the reopening action.

## Creation paths

Every path that can create a `V2Subject`, including a reviewed subject and an unreviewed context subject, records the authenticated creating client. `save_experience` and `enrich_subject` gain an optional `source_model` argument so the audit can retain the model label without treating it as trusted identity.

Existing subjects in the test deployment may be backfilled from the earliest durable workflow/client evidence where available. If no creator can be resolved, the proposal is marked legacy/unknown; one explicit agreeing review may settle it, but the audit must disclose that the creator identity was unavailable.

## Workflow orchestration

After `save_experience` or `enrich_subject` creates or touches an unconfirmed subject, the response contains durable workflow state.

The workflow has only these relevant outcomes:

- no formal review yet: `classification_review_required`;
- creator attempted self-review: remain `classification_review_required`, requiring a different client;
- independent reviewer agreed: `completed`;
- independent reviewer disagreed: `disputed`.

The obsolete `awaiting_second_model` step is not used after the first qualifying independent review.

Both write-tool descriptions explicitly require callers to inspect and follow `workflow.next_action`. Responses also expose a machine-readable `workflow_action_required` boolean so a normal client does not have to infer pending work from prose.

The server cannot force an external MCP host to make another tool call, so durable state remains authoritative if a client stops. The next model can resume through `list_my_workflows`.

## Rob verification

Production audit evidence already shows:

- Rob was first created through the authenticated Claude client;
- `gpt-5.6-sol` later inspected and affirmed `Rob -> person` through the authenticated ChatGPT client;
- the two client identities are distinct.

After deployment, replaying/synchronising that existing decision must produce `confirmed`, set `classification_locked_at`, mark the formal decision confirmed, complete the durable workflow and expose the creation proposal plus review decision in the classification audit.

## Tests

Regression coverage must prove:

1. Creation produces a proposal but no formal decision row.
2. A later decision from a different authenticated client confirms on agreement.
3. A later decision from a different authenticated client disputes on disagreement.
4. The creator cannot self-confirm by changing only `source_model`.
5. Proposal and decision attribution are visible in classification state.
6. The workflow persists across calls and completes or disputes after the first independent review.
7. `save_experience` and `enrich_subject` both advertise and return mandatory workflow follow-up metadata.
8. Existing convergence, specificity, locking, reopening and idempotency behaviour remains covered.

## Non-goals

- Cryptographically proving an exact commercial model version supplied by an MCP host.
- Requiring AI 1 to return after AI 2 has reviewed the proposal.
- Treating subject creation as a formal classification decision.
