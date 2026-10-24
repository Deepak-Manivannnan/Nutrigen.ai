# Strict calorie refusal and fast backend startup

**Status:** Accepted · **Recorded:** 2026-10-24

## Context

Two problems were found in the running system:

**Problem 1 — Calorie conflict loop.** When a user requested calories outside their safe range (for example, 1,500 kcal when the safe range was 1,950–2,150 kcal), the system treated "the user typed a number" as "the conflict is resolved." The safety check was then skipped, the unsafe value was sent to the planner, the graph rejected it, and the same warning message repeated in a loop. The combined diet+calorie conflict prompt also offered "proceed at requested calories" as an option, which contradicts the safety guardrail. Worse, the refusal wording was hardcoded to "maintain" and "below," even for gain or lose goals and even when calories were above the range.

**Problem 2 — Slow backend startup.** `init_db()` in `backend/app/db/session.py` sanitised every existing meal plan in the database on every server restart. Sanitisation called the USDA FoodData Central API for every ingredient in every plan. With dozens of users and hundreds of meals, this took 2+ minutes before the server could accept any request. Plans are already validated when they are created, so re-sanitising them on every restart was redundant and made the server depend on an external API just to boot.

## Decision

### Change A — Strict calorie refusal

- `cal_resolved` is now set to `True` **only** when `calorie_guardrail_confirmed` is set by the LLM. A bare calorie number or `chosen_calorie_target` no longer clears the question.
- The calorie guardrail check **always runs** before the plan proceeds, regardless of whether `cal_resolved` is True.
- If the user confirms the recommended safe value, the plan proceeds at that safe value.
- If the user insists on an unsafe value after already being warned, the system returns a **clear refusal**: "I cannot create a plan at X kcal because it falls outside your safe range." It does not ask again.
- The refusal wording is **goal-specific**: lose (muscle loss risk), gain (weight loss instead of gain), maintain (nutritional deficiency and fatigue). The relation (`below` or `above`) and the actual numbers come from the guardrail result.
- The conflict prompt no longer offers "proceed at requested calories" as an option. Only safe choices are offered: recommended calories with the requested diet, or the profile diet with its recommended calories.

### Change B — Fast startup (remove USDA API calls from `init_db()`)

- `init_db()` now only creates database tables (`Base.metadata.create_all`) and runs migration scripts (`ALTER TABLE` statements).
- The plan-sanitisation loop that called the USDA API for every existing meal plan is removed.
- Sanitisation, when needed, happens at plan creation or edit time — not at server startup.

## Consequences

**Good:**

- Calorie refusals are clear, goal-specific, and not repetitive. The user gets one honest "no" with an explanation and a safe alternative.
- No unsafe calorie target can slip through because a question was "answered."
- Backend starts in seconds instead of minutes.
- Server startup no longer depends on the USDA API being reachable.
- Deployments are faster; health checks pass quickly.

**Costly:**

- Any existing bad data already in the database is no longer automatically sanitised on restart. Bad plans must be fixed manually or through a one-time cleanup script.
- Plans created before this change may still contain unsafe values; they are only re-checked when the user edits them.

## Code map

- `backend/app/orchestration/runner.py` — calorie guardrail logic, `cal_resolved`, refusal path
- `backend/app/orchestration/supervisor/agent.py` — conflict prompt wording, fallback template
- `backend/app/db/session.py` — `init_db()` now creates tables and runs migrations only
