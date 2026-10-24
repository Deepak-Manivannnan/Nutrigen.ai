# Allergen safety is a curated taxonomy, with investigation for what it cannot see

**Status:** Accepted · **Recorded:** 2026-10-22

## Context

Users write "nuts", "dairy", or a free-text custom allergen. Ingredients are worse:
"paneer", "semolina", "dashi", "tahini", "curry paste". Literal substring matching misses
hidden sources; naive matching also produces false positives ("eggplant" for an egg
allergy, "coconut milk" for dairy) that make the product unusable.

## Decision

Use a curated, code-owned allergen taxonomy as the deterministic layer.

- Canonical entries expand an allergen into targets: peanut → groundnut/arachis; dairy →
  milk, cheese, butter, ghee, paneer, whey, casein; gluten → wheat, barley, rye, semolina,
  maida, atta; shellfish → shrimp, prawn, crab, lobster, squid, and so on.
- Matching normalises case/whitespace, supports whole-word matches and collapsed compound
  matches ("peanut butter" → "peanutbutter"), and handles plurals.
- A `FALSE_POSITIVE_EXCLUSIONS` table removes known collisions: eggplant for egg, nutmeg
  and butternut for nut, coconut/almond/oat milk for dairy, peach/pear for pea.
- Custom allergens get generic singularisation and literal matching, so an unlisted
  allergy still works conservatively.
- For compound or unfamiliar ingredients, the Allergy agent calls `lookup_ingredient`,
  which returns a normalised name and guidance, then reasons about culinary derivatives.
- The final veto is not the LLM's opinion: `check_allergens` is re-run over the plan and
  grocery list in the output guardrail, and any hit blocks the result.

## Consequences

**Good:** common allergens behave predictably and can be unit-tested without a model. The
exclusion table prevents the most embarrassing false positives. Investigation handles
ingredients the table has never seen.

**Costly:** the taxonomy is the safety boundary, so a missing synonym is a missing
protection — "curry paste" is only caught if the agent investigates it, and the final
deterministic pass sees the literal name. The hidden-source lists are finite and
region-specific. Custom allergens are weaker than curated ones. Maintaining exclusions is
ongoing work: every new false positive is a code change, and over-broad exclusions can
create false negatives.

## Code map

- `backend/app/tools/allergens.py` — taxonomy, exclusions, normalisation, matching
- `backend/app/agents/allergy/agent.py` — investigation and report
- `backend/app/guardrails/output_checks.py` — final deterministic screen
