# Multi-turn questions are explicit, client-persisted pending workflows

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

Planning regularly needs a second turn: the user did not say how many days, a fixed
breakfast conflicts with a vegan profile, or a plan name matches more than one saved plan.
A stateless chat endpoint cannot remember that it asked unless the question itself is
stored somewhere. The alternative — a server-side conversation session — adds state,
expiry, and failure modes the product does not otherwise need.

## Decision

Model an open question as a `PendingWorkflow` object and round-trip it through the client.

- Fields: `workflow_type`, `pending_conflicts`, `asked_questions`, `extracted_info`,
  `next_expected_decision`, `original_intent`, `original_message`,
  `user_profile_at_start`, `created_at`.
- When the Supervisor needs more information, it saves the pending object, asks one clear
  question, and returns it with the response.
- The frontend stores it per user in `localStorage` and sends it back on the next message.
  The intent call receives it as context, so answers like "yes" or "5 days" attach to the
  right question.
- `resume_request` continues the pending work; `cancel_request` drops it; a genuinely new
  request triggers an interruption message that lets the user choose.
- `extracted_info` accumulates answers, so a question is not asked twice.

## Consequences

**Good:** the backend stays stateless between turns and scales without sticky sessions.
Refreshing the page does not lose the question, because the client owns a copy. The
pending object is inspectable, which makes multi-turn bugs reproducible.

**Costly:** the browser is the only owner of pending state — clearing site data, signing
out, or switching devices loses the thread. There is no server-side expiry, so a stale
pending workflow can survive until it is resumed or cancelled. Every new multi-turn flow
must add a `workflow_type` and teach the runner how to merge answers, which is more
ceremony than a free-form chat memory. The LLM composes the wording, so identical states
can produce differently phrased questions.

## Code map

- `backend/app/schemas/state.py` — `PendingWorkflow`
- `backend/app/orchestration/runner.py` — interruption gate, resume/cancel, answer merging
- `backend/app/orchestration/supervisor/intent.py` — pending context in the intent prompt
- `frontend/src/context/AppContext.jsx` — per-user persistence
