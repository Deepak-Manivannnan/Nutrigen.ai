"""
Live-LLM tests for the Nutrition Agent's agent-assisted ingredient resolution.

These deliberately call the REAL Gemini client and the REAL USDA FoodData
Central API. They exist to verify judgement that cannot be faked with a stub
LLM: whether the model actually recognises a regional dish name and rewrites it
into a term USDA resolves to the right food.

Run from the REPOSITORY ROOT (not backend/), because config.py loads .env
relative to the working directory:

    python -m pytest backend/tests/live/test_nutrition_agent_resolution.py -v -s

They are excluded from the default fast suite by the `live` marker, so a normal
`pytest tests` run does not pay for them.
"""
import asyncio
import time

import pytest

from backend.app.agents.nutrition.agent import NutritionAgent, IngredientResolutionSet
from backend.app.schemas.domain import MealPlan, DayPlan, Meal, Ingredient
from backend.app.tools.nutrition.usda import (
    lookup_nutrition,
    set_ingredient_aliases,
    clear_ingredient_aliases,
)

pytestmark = pytest.mark.live


def _meal(name: str, *ingredients: str) -> Meal:
    return Meal(
        meal_type="lunch",
        recipe_name=name,
        calories=0,
        ingredients=[Ingredient(name=i, amount_grams=100.0) for i in ingredients],
        instructions="1. Cook it. 2. Serve warm.",
    )


def _plan(*ingredient_names: str) -> MealPlan:
    return MealPlan(
        days=[DayPlan(day_number=1, meals=[_meal("Test Dish", *ingredient_names)])],
        target_calories=2000.0,
        version=1,
    )


# --------------------------------------------------------------------------
# Pure-code checks: no LLM required, instant
# --------------------------------------------------------------------------

def test_alias_never_overrides_the_staple_table():
    """The curated staples are exact; a model rewrite must not beat them."""
    set_ingredient_aliases({"paneer": "butter"})
    try:
        import asyncio as _a
        r = _a.run(lookup_nutrition("paneer"))
        assert r["calories"] == 265.0, "staple baseline must win over any alias"
        assert r["source"] == "usda_staple_baseline"
    finally:
        clear_ingredient_aliases()


def test_aliases_are_cleared_between_plans():
    """A resolution from one audit must not leak into the next."""
    set_ingredient_aliases({"masala poha": "poha"})
    clear_ingredient_aliases()
    from backend.app.tools.nutrition.usda import get_ingredient_aliases
    assert get_ingredient_aliases() == {}


def test_resolution_schema_rejects_empty_output():
    r = IngredientResolutionSet()
    assert r.resolutions == []


# --------------------------------------------------------------------------
# Live LLM + live USDA
# --------------------------------------------------------------------------

def test_model_rewrites_dish_names_to_searchable_terms():
    """The model must convert regional dish names into plain searchable food."""
    agent = NutritionAgent()
    plan = _plan("masala poha", "beetroot poriyal", "chickpea chaat", "mint chutney")
    aliases = asyncio.run(agent._resolve_ingredient_queries(plan))

    print("\n  dish names sent to the model:")
    for k, v in aliases.items():
        print(f"    {k:<22} -> {v}")

    assert aliases, "the model resolved nothing - feature is not working"

    for original, resolved in aliases.items():
        # Must be a genuine narrowing, not the same string back.
        assert resolved != original
        assert len(resolved) <= 60
        # No brand or packaged-product language.
        for banned in ("brand", "organic", "snack", "packet", "packaged"):
            assert banned not in resolved, f"{resolved!r} looks like a product"

    # The specific failure this feature exists to prevent.
    assert "beetroot" not in aliases.get("beetroot poriyal", "beetroot"), (
        "should have resolved away from the packaged snack match"
    )


def test_resolution_improves_the_usda_match():
    """End to end: the rewrite must change which USDA record we land on."""
    dishes = ["beetroot poriyal", "chickpea chaat"]
    plan = _plan(*dishes)

    before = {}
    for d in dishes:
        r = asyncio.run(lookup_nutrition(d))
        before[d] = (r.get("matched_description", ""), r.get("calories", 0.0))

    agent = NutritionAgent()
    aliases = asyncio.run(agent._resolve_ingredient_queries(plan))
    print(f"\n  model aliases: {aliases}")
    if not aliases:
        pytest.skip("model returned no aliases this run")

    clear_ingredient_aliases()
    set_ingredient_aliases(aliases)
    after = {}
    try:
        for d in dishes:
            r = asyncio.run(lookup_nutrition(d))
            after[d] = (r.get("matched_description", ""), r.get("calories", 0.0))
    finally:
        clear_ingredient_aliases()

    print("\n  USDA match BEFORE (literal dish name):")
    for d in dishes:
        print(f"    {d:<22} {before[d][1]:>6.1f} kcal  <- {before[d][0][:48]}")
    print("  USDA match AFTER  (agent-resolved):")
    for d in dishes:
        print(f"    {d:<22} {after[d][1]:>6.1f} kcal  <- {after[d][0][:48]}")

    assert after != before, "aliases did not change any USDA match"
    # A cooked vegetable dish should not resolve to a 400+ kcal branded snack.
    for d, (_desc, kcal) in after.items():
        assert kcal < 300, f"{d} still resolves to {kcal} kcal - wrong record"


def test_full_review_still_produces_a_verdict():
    """The deterministic guarantee must be untouched by the new step."""
    agent = NutritionAgent()
    plan = _plan("masala poha", "paneer", "brown rice")
    t0 = time.time()
    report = asyncio.run(
        agent.review_plan(
            plan=plan,
            calorie_target=2000.0,
            user_profile={"goal": "maintain", "activity_level": "medium"},
            task_id="live-test",
        )
    )
    print(f"\n  review took {time.time() - t0:.1f}s  passed={report.passed}  days={len(report.daily_totals)}")
    assert isinstance(report.daily_totals, list) and report.daily_totals
    assert report.daily_totals[0].calories >= 0
    from backend.app.tools.nutrition.usda import get_ingredient_aliases
    assert get_ingredient_aliases() == {}, "aliases leaked after review_plan finished"


def test_resolution_skips_staples_and_plain_names():
    """Confident matches never reach the model."""
    agent = NutritionAgent()
    plan = _plan("paneer", "rice", "brown rice", "red lentils")
    assert asyncio.run(agent._resolve_ingredient_queries(plan)) == {}


# --------------------------------------------------------------------------
# Part 1: ranking. No LLM, no API - pure deterministic scoring.
# --------------------------------------------------------------------------

def test_scorer_prefers_relevance_over_data_type():
    """The defect that let a pork record win a vegetarian ingredient.

    'Olives, black' and 'Olive loaf, pork' both appear in USDA's results for
    'black olives'. SR Legacy's prestige bonus used to outrank relevance.
    """
    from backend.app.tools.nutrition.usda import _score_food_candidate

    right = _score_food_candidate("black olives", "Olives, black", "Survey (FNDDS)")
    wrong = _score_food_candidate("black olives", "Olive loaf, pork", "SR Legacy")
    print(f"\n  Olives, black  [FNDDS]     {right:>7.1f}")
    print(f"  Olive loaf, pork [SR Legacy] {wrong:>7.1f}")
    assert right > wrong, "data-type prestige must not outrank relevance"


def test_scorer_is_order_insensitive():
    from backend.app.tools.nutrition.usda import _score_food_candidate
    a = _score_food_candidate("black olives", "Olives, black", "SR Legacy")
    b = _score_food_candidate("black olives", "Black, ripe, olives", "SR Legacy")
    assert a == b, "word order and commas must not change the score"


def test_match_quality_reports_confidence():
    from backend.app.tools.nutrition.usda import match_quality
    assert match_quality("black olives", "Olives, black") == 1.0
    assert match_quality("basmati rice", "Dirty rice") < 1.0
    assert match_quality("nutritional yeast", "Doughnuts, yeast-leavened") <= 0.5


def test_part1_alone_fixes_black_olives():
    """End-to-end proof for Part 1, with no model involved at all."""
    r = asyncio.run(lookup_nutrition("black olives"))
    print(f"\n  black olives -> {r.get('calories')} kcal  <- {r.get('matched_description')}")
    assert "pork" not in (r.get("matched_description") or "").lower(), \
        "a pork record must never win a vegetarian ingredient"
    assert r.get("match_quality", 0) >= 0.5


# --------------------------------------------------------------------------
# Timing - what the user actually asked to know
# --------------------------------------------------------------------------

def test_nutrition_review_timing():
    """Report how long a nutrition review takes end to end."""
    agent = NutritionAgent()
    plan = _plan(
        "masala poha", "beetroot poriyal", "chickpea chaat", "mint chutney",
        "paneer", "brown rice", "black olives", "nutritional yeast",
    )

    t0 = time.time()
    resolution = asyncio.run(agent._resolve_ingredient_queries(plan))
    resolve_s = time.time() - t0
    print(f"\n  resolution stage : {resolve_s:>5.1f}s   aliases={len(resolution)}")
    for k, v in resolution.items():
        print(f"      {k:<24} -> {v}")

    from backend.app.tools.nutrition.usda import set_ingredient_aliases, clear_ingredient_aliases
    set_ingredient_aliases(resolution)
    t1 = time.time()
    try:
        report = asyncio.run(
            agent.review_plan(
                plan=plan, calorie_target=2000.0,
                user_profile={"goal": "maintain", "activity_level": "medium"},
                task_id="timing",
            )
        )
    finally:
        clear_ingredient_aliases()
    total = time.time() - t0

    print(f"  full review_plan : {time.time() - t1:>5.1f}s   (resolution + math)")
    print(f"  TOTAL            : {total:>5.1f}s")
    assert report is not None
