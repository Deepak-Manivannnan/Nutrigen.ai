# One structured LLM call decides intent and extracts every parameter

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

The first version routed on keyword lists. That broke on ordinary phrasing ("something
light", "the second one", "actually make it vegan") and made every new capability a new
regex. The Supervisor needs a reliable answer to two questions before it can act: what
does the user want, and with which parameters?

## Decision

Classify with one `generate_structured` call against `UserIntentExtraction`, at
temperature 0, and treat the returned object as the contract for the turn.

- The schema carries the intent plus all parameters: days, calories, diet, cuisine,
  exclusions, fixed meals, snacks, edit target, plan name, rename target, and so on.
- Supported intents: `chat`, `app_info`, `profile_info`, `out_of_scope`, `explain`,
  `new_plan`, `edit_meal`, `approve_plan`, `grocery_only`, `delete_plan`, `rename_plan`,
  `list_plans`, `resume_request`, `cancel_request`.
- Up to six prior turns and the current `PendingWorkflow` are injected as context, so
  "yes", "the second one", and "do that" resolve against the conversation.
- Classifying out-of-scope requests is part of the same call; there is no separate topic
  filter.
- The result is an input, not an authority: guardrails and ownership checks still run
  after it.

## Consequences

**Good:** adding a capability means extending one schema and one prompt, then handling the
new branch. Ambiguous language is handled by the model rather than by an ever-growing
keyword list. The extraction object is logged and easy to test.

**Costly:** the prompt and schema are now a public internal API; a field rename ripples
into `runner.py`, the pending workflow, and tests. Every turn costs one LLM call before
any work starts. Classification is probabilistic — the flow relies on confirmation
prompts and deterministic gates (for example, before abandoning a pending question)
rather than on perfect accuracy. A model outage fails the turn at step one.

## Code map

- `backend/app/orchestration/supervisor/intent.py` — schema, prompt assembly, extraction
- `backend/app/orchestration/supervisor/prompt.md` — classification instructions
- `backend/app/schemas/state.py` — intent vocabulary and pending workflow
- `backend/app/orchestration/runner.py` — dispatch per intent
