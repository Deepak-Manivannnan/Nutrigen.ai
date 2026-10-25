# Safety rules are code; the LLM only handles language and judgement

**Status:** Accepted · **Recorded:** 2026-10-22 · **Updated:** 2026-10-25

## Context

The product makes two promises that a language model cannot be trusted to enforce
consistently: it will not recommend a dangerous calorie target, and it will not serve a
plan containing an allergen. At the same time, meal planning needs interpretation
("something light for dinner", "South Indian", "keep eggs for breakfast") that is
painful to express as rules. The design has to separate the two.

## Decision

Put every decision that could hurt someone in plain Python, and let the LLM interpret,
compose, investigate, and explain.

Deterministic rules currently include:

- Mifflin-St Jeor BMR/TDEE, goal adjustment (±300 kcal), target rounded to 50 kcal.
- A safe daily band of target ±100 kcal, clamped to 1200–4000 kcal.
- Adults only (18+), plans capped at 7 days, calorie requests outside the band refused
  with the safe range and an offer of the suggested target.
- Profile measurements are range-checked when a profile is created *and* when it is
  updated (age 18-120, height 100-250 cm, weight 30-300 kg). The limits live in
  `schemas/api.py` and are mirrored by the onboarding and Edit Profile forms
  (`frontend/src/utils/profileLimits.js`); an absurd value such as age 1000 can no longer
  reach the calorie formula. Stored values are not range-checked when read, so older rows
  keep loading.
- Diet-type conflicts and fixed-meal conflicts checked before planning.
- A separate ±5% tolerance used to audit a finished day against its target. This is not
  the same band as the input guardrail above.
- Allergy and diet re-checks on the finished plan and grocery list.
- Round limit (3), veto priority, and fail-closed behaviour.

The LLM is used for intent extraction, meal composition, ingredient investigation, and
user-facing explanations.

## Consequences

**Good:** safety behaviour is reviewable, unit-testable, and cannot be widened by a
prompt edit or a persuasive user. The same numbers appear in the API, the agents, and
the tests because they live in `config.py`.

**Costly:** new safety rules need a code change and a test, not a sentence in a prompt.
The rules can be wrong in ways tests cannot catch — the calorie band and age floor need
clinical review before this is used for real people. Deterministic checks can also be
coarse: the fixed-meal allergy check is a substring match, while the real allergen pass
happens later through the taxonomy and the Allergy agent. Duplicating a rule in the
prompt for guidance is fine; relying on the prompt for enforcement is not.

## Code map

- `backend/app/config.py` — limits and calorie policy values
- `backend/app/guardrails/calorie_rules.py` — BMR/TDEE and safe band
- `backend/app/guardrails/input_checks.py` — age, days, calories, diet, fixed meals
- `backend/app/guardrails/output_checks.py` — final allergen/diet re-check and labels
- `backend/app/orchestration/supervisor/routing.py` — veto priority, retry, escalate
