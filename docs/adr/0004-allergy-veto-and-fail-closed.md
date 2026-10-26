# The allergy veto is absolute and verification fails closed

**Status:** Accepted · **Recorded:** 2026-10-22 · **Updated:** 2026-10-26

## Context

An allergy miss is the one failure the product cannot absorb. The reviewers are LLM plus
tools, so they can crash, time out, or return a malformed report. Treating a crashed
reviewer as "no objection" would put an unscreened plan in front of the user with an
"approved" label — the worst possible failure mode.

## Decision

Screen allergies in three independent layers and never guess when a layer is unavailable.

1. **Deterministic matching** (`check_allergens`) against the curated allergen taxonomy.
   This runs inside the Allergy agent and again in the output guardrail.
2. **Agent investigation.** The Allergy agent can inspect compound ingredients with
   `lookup_ingredient` and explain hidden sources (dashi → fish, ghee → dairy).
3. **Output guardrail.** Runs at approval, after the Shopper: the plan **and its grocery
   list** are re-screened against the user's current allergies, plus the declared diet,
   before the plan is marked approved. If it fails, the plan stays a draft and no grocery
   list is saved. Drafts are not re-screened here: the Allergy agent's own code check
   (layer 1, plus `check_diet_compliance`) already ran on the same meals.

The Supervisor then applies fixed rules:

- Any allergy failure is a veto. It outranks nutrition feedback and cannot be waived by
  another agent. A veto sends the Planner back to redo only the vetoed meal (ADR 0007).
- If either reviewer raises an exception, `parallel_reviews_node` returns `status:
  "failed"`. Nothing is approved, and the user is told that verification could not run.
- At the round limit, the run escalates with the outstanding reasons instead of shipping
  a partial result.

## Consequences

**Good:** a model outage produces an honest error, not a false all-clear. A compromised or
confused reviewer is still backstopped by deterministic checks. The audit story is simple:
every allergy decision has a code path and a trace entry.

**Costly:** availability is deliberately sacrificed. If Gemini is down, users cannot get a
plan, because "unverified" is not an acceptable output. Matching is conservative and
literal — compound or regional names can be vetoed on suspicion, and new allergens only
work if the taxonomy or the investigation catches them. Custom allergens fall back to
singularised literal matching, which is weaker than the curated entries. Grocery items
are screened too, so a safe meal plan can still fail on a packaged good.

**Known gaps (found in the 2026-10-25 pre-launch review; not yet fixed):**

- "Fails closed" holds when a reviewer *raises*. If only the Allergy agent's AI call fails,
  `AllergyAgent.review_plan` catches it and continues with the deterministic matching alone,
  and the report still reads "Allergies verified". Hidden sources the deterministic list
  does not know (for example naan or pesto) then depend on the AI.
- The deterministic list is short. Measured misses included gluten via bread, chapati,
  naan, pasta and noodles; egg via brioche; peanut via satay; tree nuts via pesto and
  marzipan; fish via Worcestershire sauce. Substring matching also over-blocks (pork flags
  "chamomile" and "graham"; nuts flags "nutritional yeast"). `lookup_ingredient` is a
  static note, not a real lookup.
- At the round limit the plan is saved with status `escalated` and presented as ready, and
  `POST /plans/{id}/approve` (and approval by chat) neither checks that status nor re-runs
  the allergy and diet checks. Editing a profile's allergies does not re-check saved plans.

## Code map

- `backend/app/tools/allergens.py` — canonical targets, exclusions, matching
- `backend/app/agents/allergy/agent.py` — investigation and report
- `backend/app/orchestration/graph.py` — parallel reviews, fail-closed branch
- `backend/app/orchestration/supervisor/routing.py` — veto priority and escalation
- `backend/app/guardrails/output_checks.py` — final plan + grocery screen
