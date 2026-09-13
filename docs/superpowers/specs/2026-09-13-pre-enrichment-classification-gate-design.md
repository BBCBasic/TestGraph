# Pre-Enrichment Classification Gate Design

## Goal

Prevent ordinary updates to an existing subject from committing while that subject still has an unsettled provisional classification.

The required order is:

1. Resolve the existing subject.
2. Inspect its classification state.
3. If the classification is provisional or candidate, require the current independent classification review workflow to settle it.
4. Only after confirmation or dispute resolution may the client retry and commit the requested enrichment.
5. Continue the existing post-write workflow processing after a successful mutation.

New-subject creation remains able to include identifiers and attributes needed to determine the initial classification.

## Scope

The gate applies to MCP/API operations that add or correct ordinary facts on an existing subject:

- `enrich_subject`;
- `correct_subject_fact`;
- `assert_location` for its existing target subject;
- `save_experience` when its subject type and canonical key resolve an existing subject.

`save_experience` is not gated when it will create a genuinely new subject. Initial attributes, identifiers, provenance and context may still be stored with that creation proposal.

Classification decision tools and dispute-resolution tools are not gated because they are the operations that settle the prerequisite. Resolving a contested location assertion is also outside this gate because it resolves an existing assertion rather than enriching the subject.

## Root Cause

The current workflow starts in the idempotent write finalizer. By that point `ensure_subject`, `correct_subject_fact`, `create_location_assertion`, or experience creation has already mutated the session. The finalizer can report `classification_review_required`, but it cannot prevent the preceding enrichment from being committed in the same transaction.

The fix must move classification eligibility checking ahead of every covered mutation while retaining the finalizer for post-write continuation.

## Preflight Contract

Introduce one focused service that receives an already-resolved existing subject and returns either no blocker or a durable classification prerequisite response.

For an existing subject:

- `confirmed`: allow the mutation;
- `provisional` or `candidate`: create or resume the enrichment workflow and return `classification_review_required` before mutating the subject;
- `disputed`: return the durable dispute/resolver workflow before mutating the subject;
- any other unsettled state: block conservatively with its durable workflow state.

The response is a machine-readable MCP error/result containing:

- `error_code: classification_review_required` for the ordinary independent-review prerequisite, or the corresponding dispute code/state;
- `subject_id` and current classification status;
- the complete existing `workflow` body, including `workflow_run_id`, `workflow_action_required`, `next_action`, `next_action_arguments`, `next_action_instruction`, and `decision_tools`.

The tool description must state that an ordinary existing-subject mutation is incomplete when this response is returned and must not be reported as successful.

## Atomicity and Idempotency

The preflight runs after enough read-only resolution to identify the target, but before validation or persistence that can mutate graph, review, correction, assertion, provenance, or enrichment state.

Starting or resuming the prerequisite workflow is intentionally durable and committed separately. The requested business mutation is not staged or stored.

A blocked request does not create an `IdempotencyRecord` for the requested operation. Therefore the client can:

1. retain the same deterministic idempotency key;
2. settle classification through the returned workflow;
3. retry the byte-equivalent logical request;
4. commit the mutation exactly once.

If an idempotency record already exists for a previously successful request, normal replay semantics remain authoritative.

## Existing-Subject Resolution

`enrich_subject`, `correct_subject_fact`, and `assert_location` already identify an existing subject before mutation and can invoke the shared gate directly.

`save_experience` must perform an exact, read-only lookup by resolved subject type and canonical key before creating or enriching anything. A match invokes the gate. No match follows the existing new-subject creation path, whose identity-collision and reclassification protections remain unchanged.

Context subjects created or referenced inside a larger request retain their current classification workflows. This change gates the primary existing subject targeted by the operation; it does not turn a single request into a multi-subject classification transaction.

## Classification Outcomes

The gate delegates all decisions to the existing creator-reviewer convergence implementation:

- an independent agreement confirms and locks the proposal;
- a different supported type creates a dispute;
- the enrichment remains blocked throughout the dispute;
- after resolver settlement, the original mutation can be retried;
- the creating client cannot self-confirm by changing only `source_model`.

No subject type, field name, car, colour, location, or domain-specific rule is introduced.

## Post-Write Workflow

The current finalization hooks remain registered for successful `subject-enrichment` and `experience` writes. Equivalent hooks are added where needed for successful correction and location-assertion writes so responses cannot claim completion while another mandatory workflow action is outstanding.

After every successful covered mutation, callers must still inspect `workflow.workflow_action_required` and follow `workflow.next_action` when true.

## Testing

Regression and integration tests must prove:

1. Enrichment against an existing provisional subject returns the durable prerequisite and does not change identifiers, attributes, provenance, context, or idempotency records.
2. An independent agreement confirms the classification; retrying the unchanged enrichment and key commits once and completes the workflow.
3. An independent different-type decision disputes before enrichment; no enrichment commits until resolver settlement.
4. Existing confirmed subjects are enriched without an unnecessary review.
5. New subjects may still be created with classification-relevant information.
6. A blocked request can be retried with the same idempotency key without duplicate writes.
7. Correcting `attributes.colour` from `silver/grey` to `red` leaves the corrected current value, one correction audit record, and a settled classification workflow.
8. Existing-subject `save_experience` and `assert_location` use the same preflight ordering.
9. Tool descriptions expose the mandatory preflight contract.
10. The full existing suite remains green.

## Database and Deployment

No schema migration is required. The database contains test data only, and no compatibility layer is needed for records created under the previous ordering.

The finished implementation will update the changelog and MCP server version, run the full relevant test suite and full project suite, and push the verified commit to `BBCBasic/TestGraph` on `master`.
