# The allergy veto is absolute and verification fails closed

**Status:** Accepted · **Recorded:** 2026-10-22

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
3. **Output guardrail.** The finished plan **and grocery list** are re-screened, plus the
   declared diet, before the result is returned.

The Supervisor then applies fixed rules:

- Any allergy failure is a veto. It outranks nutrition feedback and cannot be waived by
  another agent.
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

## Code map

- `backend/app/tools/allergens.py` — canonical targets, exclusions, matching
- `backend/app/agents/allergy/agent.py` — investigation and report
- `backend/app/orchestration/graph.py` — parallel reviews, fail-closed branch
- `backend/app/orchestration/supervisor/routing.py` — veto priority and escalation
- `backend/app/guardrails/output_checks.py` — final plan + grocery screen
