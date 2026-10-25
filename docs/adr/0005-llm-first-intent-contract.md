# One structured LLM call decides intent and extracts every parameter

**Status:** Accepted · **Recorded:** 2026-10-22 · **Updated:** 2026-10-25

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
  `list_plans`, `resume_request`, `cancel_request`, `unclear`.
- Up to six prior turns and the current `PendingWorkflow` are injected as context, so
  "yes", "the second one", and "do that" resolve against the conversation.
- **The model also sees the user's saved plans** (id, name, length, calories, diet,
  status, and which plan the previous reply created) and returns `target_plan_id` /
  `target_plan_name`. The code only checks that the id/name exists among that user's plans
  (`find_referenced_plan`); it does not search the message or earlier chat for plan names,
  day counts or diet words, and it never falls back to "the first plan". With several
  plans and no answer from the model, the user is asked which plan. A lone plan is the only
  candidate and is used.
- **Yes/no and abandon decisions are the model's.** `is_delete_confirmation`, the
  `cancel_request` intent, `resume_request` and `abandon_pending_work` carry them; word
  lists such as "yes/ok/no/stop" or "instead/forget" no longer decide anything. A plan is
  deleted only when a confirmation about a specific plan is pending AND the model reads the
  reply as a yes (a delete confirmation, or `resume_request` for a bare "yes, go ahead")
  AND the reply does not point at a different plan.
- **Edit scope and operation are the model's too** (`edit_scope`, `operation`, `meal_type`,
  `day`, `target_meal_calories`, `requested_exclusions`); `modify_meal` no longer searches the
  instruction text for "remove", "all days", "entire day" or "light". A blank operation means
  replace.
- **`unclear` handles what cannot be understood**: a lone dot, random characters or digits,
  an accidental send, a typo that changes the meaning (ordinary typos are read through). A
  message with no letters or digits skips the model call. If the model's answer cannot be
  read, the result is also `unclear`, not small talk. For `unclear` the app does nothing,
  keeps any pending question open, and the reply is written from the open thread (the
  pending work and the question that was asked, or the assistant's last message) so the
  conversation continues instead of resetting.
- **The number of days is never assumed.** If a new-plan request does not say it, the
  Supervisor asks (an LLM-written question that acknowledges what the user already said and
  mentions the 7-day maximum) and keeps the request open as a `days_missing` pending
  question. A reply that gives a length ("3", "a week") completes the request and is not
  treated as an interruption.
- **Nothing the user asked for is lost across a follow-up question.** Whenever a new-plan
  question is left open (days, diet or calorie conflict, profile defaults vs own specs), the
  request's details (cuisine, exclusions, snacks, plan name, locked meals, days, calories,
  diet) are stored with it (`extracted_info["carry"]`) and put back into the next extraction
  unless that message is a complete fresh specification.
- **Cuisine is kept as the user said it** ("South Indian", not "Indian"). The recipe search
  maps it to a TheMealDB area itself; the Planner is told that every meal must be a genuine
  dish of that cuisine, in the first draft and in targeted repairs. The cuisine is saved on the
  plan (`plans.cuisine`, added 2026-10-25; NULL for older plans), so a later "change Tuesday's
  lunch" stays inside it; an edit takes its cuisine, diet and calorie target from the plan and
  asks only what to change, unless the user names another cuisine for that one meal.
- **New exclusions can be remembered.** After a plan is created, foods the user left out that
  are not already in their saved dislikes or allergies are offered for saving
  (`save_dislikes` pending). They are applied to the plan either way. The model reads the
  yes/no (`save_preference_answer`); on yes, code adds them to the existing `dislikes`
  field and the frontend reloads the profile; on no, nothing is stored; any other message
  drops the question quietly.
- Classifying out-of-scope requests is part of the same call; there is no separate topic
  filter.
- The result is an input, not an authority: guardrails and ownership checks still run
  after it.

## Consequences

**Good:** adding a capability means extending one schema and one prompt, then handling the
new branch. Ambiguous language is handled by the model rather than by an ever-growing
keyword list, and a word inside a longer sentence ("ok" in "cook", "no" in "know") can no
longer trigger a delete or a cancel. The extraction object is logged and easy to test.

**Costly:** the prompt and schema are now a public internal API; a field rename ripples
into `runner.py`, the pending workflow, and tests. Every turn costs one LLM call before
any work starts (the saved-plan list adds a few lines to it). Classification is
probabilistic: the flow relies on confirmation prompts and deterministic gates (a pending
question, an id that must belong to the user) rather than on perfect accuracy, and a
misread yes/no now means asking again instead of a keyword rescue. A model outage fails the
turn at step one.

**Deliberately still code:** food-safety and data rules (allergen and diet lists, diet
compliance, the fixed-meal conflict check, unit and ingredient-name matching), technical
checks (email format, retry detection), and the check on the model's own replies for false
"I've done it" claims.

## Code map

- `backend/app/orchestration/supervisor/intent.py` — schema, prompt assembly, extraction
- `backend/app/orchestration/supervisor/prompt.md` — classification instructions
- `backend/app/schemas/state.py` — intent vocabulary and pending workflow
- `backend/app/orchestration/runner.py` — dispatch per intent
