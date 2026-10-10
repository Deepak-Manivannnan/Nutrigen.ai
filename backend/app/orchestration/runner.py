import re
import uuid
from typing import Dict, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.schemas.state import PlanState
from backend.app.orchestration.graph import create_meal_planner_graph
from backend.app.orchestration.supervisor.intent import extract_user_intent, EditDetails
from backend.app.orchestration.supervisor.agent import SupervisorAgent
from backend.app.db.repositories.plans import PlanRepository
from backend.app.db.session import AsyncSessionLocal
from backend.app.observability.logging import get_logger

logger = get_logger("orchestration.runner")

def find_referenced_plan(
    user_message: str,
    all_plans: List[Any],
    target_id: Optional[int] = None,
    target_name: Optional[str] = None
) -> Optional[Any]:
    """Helper to cleanly resolve referenced meal plan without exposing internal IDs."""
    if not all_plans:
        return None
    if target_id:
        p = next((p for p in all_plans if p.id == target_id), None)
        if p:
            return p
    if target_name:
        t_clean = target_name.strip().lower()
        p = next((p for p in all_plans if p.name.strip().lower() == t_clean or t_clean in p.name.strip().lower()), None)
        if p:
            return p
    clean_msg = user_message.strip().lower()
    for p in sorted(all_plans, key=lambda x: len(x.name), reverse=True):
        p_name = p.name.strip().lower()
        if p_name and p_name in clean_msg:
            return p
    return all_plans[0]

async def run_meal_planner(
    user_id: int,
    user_message: str,
    user_profile: Dict[str, Any],
    session: Optional[AsyncSession] = None,
    task_id: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None
) -> PlanState:
    """
    Main entry point for running the multi-agent meal planning workflow:
    1. Extracts intent & parameters (days, calories, fixed meals, edit details, deletion, rename, diet conflict, snack).
    2. Instantiates PlanState.
    3. Handles conversational intents, profile inquiry, rename, snack questions, deletion, and updates.
    4. Handles diet preference conflict confirmation and safe calorie guardrails.
    5. Executes LangGraph hub-and-spoke workflow for plan creation / modification.
    6. Persists draft/updated plans with proper versioning and status.
    """
    tid = task_id or f"t-{uuid.uuid4().hex[:8]}"
    logger.info(f"Starting workflow run for task {tid} (user {user_id})")

    # Step 1: Supervisor intent extraction with history context
    intent_data = await extract_user_intent(user_message, history=history)
    logger.info(f"[{tid}] Extracted intent: {intent_data.intent}, days: {intent_data.requested_days}, calories: {intent_data.requested_calories}, diet: {intent_data.requested_diet_type}")

    # Merge standing fixed meals from profile if any
    fixed = {}
    if isinstance(intent_data.fixed_meals, list):
        for slot in intent_data.fixed_meals:
            fixed[slot.meal_type] = slot.dish
    elif isinstance(intent_data.fixed_meals, dict):
        fixed = dict(intent_data.fixed_meals)

    if "standing_fixed_meals" in user_profile and user_profile["standing_fixed_meals"]:
        for k, v in user_profile["standing_fixed_meals"].items():
            if k not in fixed:
                fixed[k] = v

    # Retrieve all user plans for multi-plan checks
    all_plans = []
    try:
        if session:
            repo = PlanRepository(session)
            all_plans = await repo.get_all_by_user(user_id)
        else:
            async with AsyncSessionLocal() as temp_session:
                repo = PlanRepository(temp_session)
                all_plans = await repo.get_all_by_user(user_id)
    except Exception as e:
        logger.warning(f"[{tid}] Failed to query user plans: {e}")

    # Determine existing active plan if editing or referencing
    existing_plan = None
    db_plan_id = None
    if all_plans:
        matched_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name
        )
        if matched_plan:
            existing_plan = PlanRepository.to_meal_plan_domain(matched_plan)
            db_plan_id = matched_plan.id

    # If existing plan has a specific diet type, preserve it
    effective_plan_diet = getattr(existing_plan, "diet_type", None) or user_profile.get("diet_type") or "veg"

    state = PlanState(
        task_id=tid,
        user_id=user_id,
        user_profile=user_profile,
        intent=intent_data.intent,
        days=intent_data.requested_days or (len(existing_plan.days) if existing_plan else None),
        calorie_target=intent_data.requested_calories or (existing_plan.target_calories if existing_plan else None),
        fixed_meals=fixed,
        edit_details=intent_data.edit_details,
        current_plan=existing_plan if intent_data.intent == "edit_meal" else None,
        plan_version=(existing_plan.version + 1) if (existing_plan and intent_data.intent == "edit_meal") else 1,
        round=1,
        max_rounds=3,
        status="drafting"
    )

    supervisor = SupervisorAgent()

    # =========================================================================
    # Step 2: Handle Profile Info Inquiry ("Tell about my profile")
    # =========================================================================
    if intent_data.intent == "profile_info":
        response_text = await supervisor.generate_profile_info_response(state, user_message, history=history)
        state.system_message = response_text
        state.status = "conversational"
        from backend.app.guardrails.calorie_rules import calculate_mifflin_st_jeor
        c_info = calculate_mifflin_st_jeor(
            age=int(user_profile.get("age", 26)),
            sex=str(user_profile.get("sex", "male")),
            height_cm=float(user_profile.get("height_cm", 175)),
            weight_kg=float(user_profile.get("weight_kg", 70)),
            activity_level=str(user_profile.get("activity_level", "medium")),
            goal=str(user_profile.get("goal", "maintain"))
        )
        sug = int(c_info["suggested_target"])
        p_diet = user_profile.get("diet_type", "veg")
        state.quick_options = [
            {"label": f"Plan 3 days ({sug:,} kcal)", "value": f"Plan a 3-day {p_diet} meal plan for {sug} kcal"},
            {"label": f"Plan 7 days ({sug:,} kcal)", "value": f"Plan a 7-day {p_diet} meal plan for {sug} kcal"}
        ]
        await supervisor.log_step(
            state=state,
            from_agent="supervisor",
            to_agent="user",
            msg_type="approve",
            content=response_text[:120] + "...",
            payload={"intent": "profile_info"}
        )
        return state

    # =========================================================================
    # Step 3: Handle Rename Plan Intent
    # =========================================================================
    if intent_data.intent == "rename_plan":
        if len(all_plans) == 0:
            context_prompt = (
                "The user asked to rename a meal plan, but they currently have 0 saved meal plans in their account. "
                "In friendly, polite English, let them know they don't have any saved meal plans yet to rename, and invite them to create one first."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        target_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name
        )

        if target_plan and intent_data.new_plan_name:
            new_title = intent_data.new_plan_name.strip()
            try:
                if session:
                    repo = PlanRepository(session)
                    await repo.rename_plan(target_plan.id, new_title)
                else:
                    async with AsyncSessionLocal() as temp_session:
                        repo = PlanRepository(temp_session)
                        await repo.rename_plan(target_plan.id, new_title)
            except Exception as e:
                logger.error(f"[{tid}] Failed to rename plan: {e}")

            context_prompt = (
                f"Successfully renamed the user's meal plan to '{new_title}'. "
                f"In polite, natural English, confirm to the user that their meal plan has been renamed to '{new_title}'."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "approved"
            state.plan_id = target_plan.id
            return state
        elif target_plan and (len(all_plans) == 1 or intent_data.target_plan_name or intent_data.target_plan_id):
            context_prompt = (
                f"The user wants to rename their meal plan '{target_plan.name}'. "
                f"In friendly, simple English, ask what new name they would like to give to '{target_plan.name}'."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            return state
        else:
            plan_bullets = [f"• **\"{p.name}\"**" for p in all_plans]
            context_prompt = (
                f"The user wants to rename a meal plan, and they have {len(all_plans)} saved plans: {', '.join(p.name for p in all_plans)}. "
                f"In polite English, list their plans and ask which one they would like to rename."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": f"Rename {p.name}", "value": f"Rename \"{p.name}\" to "}
                for p in all_plans
            ]
            return state

    # =========================================================================
    # Step 4: Handle Add Snack Intent
    # =========================================================================
    if intent_data.intent == "add_snack":
        if len(all_plans) == 0:
            context_prompt = (
                "The user asked to add a snack to their meal plan, but they currently have 0 active meal plans saved in their account. "
                "Conversationally and warmly let them know they don't have an active meal plan yet to add a snack to, and invite them to create one first."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        target_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name
        ) or all_plans[0]

        # Determine target day (from intent_data, user_message, or history)
        target_day = intent_data.target_day
        if not target_day:
            m_d = re.search(r'\b(?:for\s+)?day\s*(\d+)\b', user_message, re.IGNORECASE)
            if m_d:
                target_day = int(m_d.group(1))
        if not target_day and history:
            for h in reversed(history):
                m_hd = re.search(r'\b(?:for\s+)?day\s*(\d+)\b', (h.get("content") or ""), re.IGNORECASE)
                if m_hd:
                    target_day = int(m_hd.group(1))
                    break
        if not target_day:
            target_day = 1

        choice = intent_data.snack_rebalance_choice
        if not choice:
            cl = user_message.lower()
            if any(w in cl for w in ["low-calorie", "low calorie", "light snack", "light"]):
                choice = "low_calorie"
            elif any(w in cl for w in ["rebalance", "all 4 meals"]):
                choice = "rebalance"

        if not choice:
            target_cal = int(target_plan.calorie_target or 2250)
            context_prompt = (
                f"The user wants to add a snack meal to Day {target_day} of their meal plan '{target_plan.name}' (current target: {target_cal:,} kcal/day). "
                f"In natural, polite, friendly conversational English, explain that adding a snack will increase their daily intake. "
                f"Ask them whether they would like to: "
                f"1) Add a light, low-calorie snack (100–150 kcal fresh fruit/nuts) keeping main meals intact, or "
                f"2) Rebalance all 4 meals across Day {target_day} so the total stays at their {target_cal:,} kcal target. "
                f"Keep it natural, polite, and simple."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": f"Add low-calorie snack (Day {target_day})", "value": f"Add a low-calorie snack for Day {target_day}"},
                {"label": f"Rebalance all 4 meals (Day {target_day})", "value": f"Rebalance all 4 meals including snack for Day {target_day}"}
            ]
            return state
        else:
            domain_plan = PlanRepository.to_meal_plan_domain(target_plan)
            target_day_plan = next((d for d in domain_plan.days if d.day_number == target_day), None)
            if not target_day_plan:
                target_day_plan = domain_plan.days[0]
                target_day = target_day_plan.day_number

            plan_diet = getattr(domain_plan, "diet_type", None) or target_plan.diet_type or user_profile.get("diet_type") or "veg"
            is_vegan = plan_diet == "vegan"
            is_veg = plan_diet in ("veg", "vegetarian")

            from backend.app.schemas.domain import Ingredient, Meal
            if is_vegan:
                snack_recipe = "Roasted Almonds and Fresh Apple Slices"
                snack_ingredients = [
                    Ingredient(name="Almonds", amount_grams=20.0),
                    Ingredient(name="Apple", amount_grams=120.0)
                ]
                snack_cal = 140.0
                snack_p, snack_c, snack_f = 4.0, 20.0, 7.0
                snack_instructions = "1. Slice fresh crisp apple into wedges.\n2. Lightly dry-roast almonds in a skillet for 2 minutes.\n3. Portion into a snack bowl and enjoy."
            elif is_veg:
                snack_recipe = "Roasted Makhana (Foxnuts) with Almonds"
                snack_ingredients = [
                    Ingredient(name="Makhana (Foxnuts)", amount_grams=25.0),
                    Ingredient(name="Almonds", amount_grams=15.0),
                    Ingredient(name="Olive Oil", amount_grams=5.0, unit="ml"),
                    Ingredient(name="Chaat Masala", amount_grams=2.0)
                ]
                snack_cal = 145.0
                snack_p, snack_c, snack_f = 4.0, 18.0, 7.0
                snack_instructions = "1. Heat olive oil in a pan on medium-low heat.\n2. Add makhana and almonds, roasting for 4 minutes until crisp.\n3. Sprinkle chaat masala, toss well, and serve."
            else:
                snack_recipe = "Greek Yogurt with Berries and Walnuts"
                snack_ingredients = [
                    Ingredient(name="Greek Yogurt", amount_grams=120.0),
                    Ingredient(name="Mixed Berries", amount_grams=50.0),
                    Ingredient(name="Walnuts", amount_grams=15.0)
                ]
                snack_cal = 145.0
                snack_p, snack_c, snack_f = 12.0, 14.0, 5.0
                snack_instructions = "1. Spoon Greek yogurt into a bowl.\n2. Top with washed berries and crushed walnuts.\n3. Serve chilled."

            existing_snack = next((m for m in target_day_plan.meals if m.meal_type.lower() == "snack"), None)
            if existing_snack:
                existing_snack.recipe_name = snack_recipe
                existing_snack.ingredients = snack_ingredients
                existing_snack.calories = snack_cal
                existing_snack.protein_g = snack_p
                existing_snack.carbs_g = snack_c
                existing_snack.fat_g = snack_f
                existing_snack.instructions = snack_instructions
            else:
                new_snack = Meal(
                    meal_type="snack",
                    recipe_name=snack_recipe,
                    source="ai_generated",
                    ingredients=snack_ingredients,
                    calories=snack_cal,
                    protein_g=snack_p,
                    carbs_g=snack_c,
                    fat_g=snack_f,
                    instructions=snack_instructions
                )
                target_day_plan.meals.append(new_snack)

            if choice == "rebalance":
                target_daily = domain_plan.target_calories or 2250.0
                splits = {"breakfast": 0.25, "lunch": 0.35, "dinner": 0.25, "snack": 0.15}
                for m in target_day_plan.meals:
                    t_ratio = splits.get(m.meal_type.lower(), 0.25)
                    desired = t_ratio * target_daily
                    if m.calories > 0:
                        factor = desired / m.calories
                        for ing in m.ingredients:
                            ing.amount_grams = round(ing.amount_grams * factor, 1)
                        m.calories = round(desired, 1)
                        m.protein_g = round(m.protein_g * factor, 1)
                        m.carbs_g = round(m.carbs_g * factor, 1)
                        m.fat_g = round(m.fat_g * factor, 1)

            target_day_plan.total_calories = round(sum(m.calories for m in target_day_plan.meals), 1)
            target_day_plan.total_protein_g = round(sum(m.protein_g for m in target_day_plan.meals), 1)
            target_day_plan.total_carbs_g = round(sum(m.carbs_g for m in target_day_plan.meals), 1)
            target_day_plan.total_fat_g = round(sum(m.fat_g for m in target_day_plan.meals), 1)

            new_version = (target_plan.version or 1) + 1
            domain_plan.version = new_version
            domain_plan.average_daily_calories = round(sum(d.total_calories for d in domain_plan.days) / len(domain_plan.days), 1)

            if session:
                repo = PlanRepository(session)
                await repo.update_plan(
                    plan_id=target_plan.id,
                    meal_plan=domain_plan,
                    grocery_list=None,
                    status="draft",
                    increment_version=True
                )
            else:
                async with AsyncSessionLocal() as temp_session:
                    repo = PlanRepository(temp_session)
                    await repo.update_plan(
                        plan_id=target_plan.id,
                        meal_plan=domain_plan,
                        grocery_list=None,
                        status="draft",
                        increment_version=True
                    )

            context_prompt = (
                f"Successfully added a healthy snack '{snack_recipe}' ({int(snack_cal)} kcal) to Day {target_day} of meal plan '{target_plan.name}'. "
                f"The plan has been updated to Version {new_version} Draft in the Plans tab. "
                f"Day {target_day} now features {len(target_day_plan.meals)} balanced meals totaling {int(target_day_plan.total_calories):,} kcal. "
                f"All other days and existing meals remain completely unchanged. "
                f"In polite, natural, simple English, let the user know their snack has been added to Day {target_day} and invite them to view it in the Plans tab."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "draft"
            state.plan_id = target_plan.id
            state.current_plan = domain_plan
            state.plan_version = new_version
            state.quick_options = [
                {"label": f"Approve {target_plan.name}", "value": f"Approve \"{target_plan.name}\""},
                {"label": "Modify in chat", "value": f"I want to update \"{target_plan.name}\": "}
            ]
            return state

    # =========================================================================
    # Step 5: Handle Plan Deletion Flow
    # =========================================================================
    if intent_data.intent == "delete_plan":
        # Check if previous assistant message in history asked for delete confirmation
        last_asst_content = ""
        if history:
            for h in reversed(history):
                if h.get("role") == "assistant":
                    last_asst_content = (h.get("content") or "").strip().lower()
                    break

        is_pending_confirmation = (
            "are you sure you want to delete" in last_asst_content
            or "can i delete it" in last_asst_content
            or "which meal plan should be deleted" in last_asst_content
            or "reply 'yes, delete it'" in last_asst_content
            or "reply \"yes, delete it\"" in last_asst_content
        )

        cleaned_user_msg = user_message.strip().lower()
        is_cancelling = any(w in cleaned_user_msg for w in ["no", "cancel", "don't", "keep it", "stop"])

        if is_pending_confirmation and is_cancelling:
            context_prompt = (
                "The user cancelled the deletion of their meal plan. "
                "Warmly and politely reassure them that their meal plan is kept safely, and ask if they would like to make any other adjustments instead."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            return state

        # Determine target plan if explicitly stated
        target_id = intent_data.target_plan_id
        if not target_id:
            m = re.search(r'(?:plan\s*#?|#)(\d+)', user_message, re.IGNORECASE)
            if m:
                target_id = int(m.group(1))

        # Check if user is confirming deletion
        is_confirming = intent_data.is_delete_confirmation or (
            is_pending_confirmation and any(w in cleaned_user_msg for w in ["yes", "yeah", "yep", "sure", "confirm", "proceed", "go ahead", "delete it", "ok", "okay"])
        )

        if is_confirming:
            plan_to_delete = find_referenced_plan(
                user_message,
                all_plans,
                target_id=target_id,
                target_name=intent_data.target_plan_name
            )

            if not plan_to_delete and is_pending_confirmation:
                m_prev = re.search(r'\*\*\"([^\"]+)\"\*\*', last_asst_content)
                if m_prev:
                    prev_name = m_prev.group(1).lower()
                    plan_to_delete = next((p for p in all_plans if p.name.lower() == prev_name or prev_name in p.name.lower()), None)

            if not plan_to_delete and len(all_plans) == 1:
                plan_to_delete = all_plans[0]

            if plan_to_delete:
                del_id = plan_to_delete.id
                del_name = plan_to_delete.name
                try:
                    if session:
                        repo = PlanRepository(session)
                        await repo.delete_plan(del_id)
                    else:
                        async with AsyncSessionLocal() as temp_session:
                            repo = PlanRepository(temp_session)
                            await repo.delete_plan(del_id)
                    logger.info(f"[{tid}] Deleted plan '{del_name}' (id #{del_id}) for user {user_id}")
                except Exception as e:
                    logger.error(f"[{tid}] Error executing plan deletion: {e}")

                remaining_count = len(all_plans) - 1
                context_prompt = (
                    f"Successfully deleted the meal plan '{del_name}' from the database. "
                    f"Remaining saved meal plans: {remaining_count}. "
                    f"In polite, natural English, confirm that '{del_name}' has been permanently deleted. "
                    f"{'Let them know they currently have no saved meal plans, and invite them to create a new one whenever ready.' if remaining_count == 0 else f'Let them know they have {remaining_count} meal plan remaining in their Plans tab.'}"
                )
                response_text = await supervisor.generate_conversational_response(
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name")
                )
                state.system_message = response_text
                state.status = "deleted"
                state.deleted_plan_id = del_id
                state.quick_options = [
                    {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                    {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
                ]
                return state
            else:
                context_prompt = (
                    "Could not determine which meal plan the user wants to delete. "
                    "In friendly, polite English, ask them to clarify which meal plan name they would like to delete."
                )
                response_text = await supervisor.generate_conversational_response(
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name")
                )
                state.system_message = response_text
                state.status = "conversational"
                return state

        # Case 0: 0 plans exist
        if len(all_plans) == 0:
            context_prompt = (
                "The user asked to delete a meal plan, but they currently have 0 active meal plans saved in their account. "
                "In friendly, polite English, let them know they don't have any active meal plans to delete, and invite them to create one whenever they are ready."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        # Case 1: 1 plan exists or user specified target plan by name or id
        target_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=target_id,
            target_name=intent_data.target_plan_name
        )
        if target_plan and (len(all_plans) == 1 or intent_data.target_plan_name or target_id):
            context_prompt = (
                f"The user wants to delete meal plan '{target_plan.name}'. "
                f"In clear, polite English, ask them to confirm if they are sure they want to delete '{target_plan.name}', and let them know they can reply 'Yes, delete it' or 'Cancel'."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": f"Yes, delete {target_plan.name}", "value": f"Yes, delete \"{target_plan.name}\""},
                {"label": "Cancel", "value": "Cancel"}
            ]
            return state

        # Case 2: Multiple plans exist and user did not name one
        plan_bullets = []
        options = []
        for p in all_plans:
            plan_bullets.append(f"• **\"{p.name}\"** ({p.days}-day, {int(p.calorie_target)} kcal/day)")
            options.append({"label": f"Delete {p.name}", "value": f"Delete \"{p.name}\""})
        options.append({"label": "Cancel", "value": "Cancel"})

        context_prompt = (
            f"The user wants to delete a meal plan, and they have multiple plans: {', '.join(p.name for p in all_plans)}. "
            f"In polite, natural English, ask which meal plan they would like to delete."
        )
        response_text = await supervisor.generate_conversational_response(
            user_message=user_message,
            context=context_prompt,
            history=history,
            user_name=user_profile.get("name")
        )
        state.system_message = response_text
        state.status = "conversational"
        state.quick_options = options
        return state

    # =========================================================================
    # Step 5b: Handle Approve Plan Request (via Chat)
    # =========================================================================
    if intent_data.intent == "approve_plan":
        if len(all_plans) == 0:
            context_prompt = (
                "The user asked to approve their meal plan, but they currently have 0 meal plans saved in their account. "
                "In friendly, polite English, let them know they don't have any meal plans saved yet to approve, and invite them to create one first."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            return state

        target_plan = find_referenced_plan(
            user_message,
            all_plans,
            target_id=intent_data.target_plan_id,
            target_name=intent_data.target_plan_name
        )
        if not target_plan:
            drafts = [p for p in all_plans if p.status == "draft"]
            target_plan = drafts[0] if drafts else all_plans[0]

        if target_plan.status == "approved" and target_plan.grocery_list:
            context_prompt = (
                f"The meal plan '{target_plan.name}' is already approved! "
                f"In polite, natural English, inform the user that '{target_plan.name}' is already approved, and that they can view its full itemized grocery checklist and Mandi market rates in their Grocery tab. "
                f"CRITICAL: You must explicitly include the exact phrase 'already approved' in your response."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "approved"
            state.plan_id = target_plan.id
            state.quick_options = [
                {"label": "View Grocery List →", "value": "Show my grocery list"}
            ]
            return state

        # Generate grocery list via Shopper Agent
        from backend.app.agents.shopper.agent import ShopperAgent
        try:
            domain_plan = PlanRepository.to_meal_plan_domain(target_plan)
            shopper = ShopperAgent()
            grocery_basket = await shopper.generate_grocery_list(
                meal_plan=domain_plan,
                task_id=f"approve-{target_plan.id}"
            )
            target_plan.grocery_list = grocery_basket.model_dump()
            target_plan.status = "approved"

            if session:
                repo = PlanRepository(session)
                await repo.session.commit()
                await repo.session.refresh(target_plan)
            else:
                async with AsyncSessionLocal() as temp_session:
                    repo = PlanRepository(temp_session)
                    p_db = await repo.get_by_id(target_plan.id)
                    if p_db:
                        p_db.grocery_list = grocery_basket.model_dump()
                        p_db.status = "approved"
                        await repo.session.commit()

            cost = int(grocery_basket.total_estimated_cost or 0)
            context_prompt = (
                f"Successfully approved meal plan '{target_plan.name}' and compiled an itemized grocery checklist with {len(grocery_basket.items)} ingredients based on wholesale Mandi benchmarks totaling approximately ₹{cost:,}. "
                f"In polite, warm, conversational English, let the user know their meal plan is approved and their grocery list is ready to review in the Grocery tab. "
                f"Ensure you mention that the plan is approved."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "approved"
            state.plan_id = target_plan.id
            state.quick_options = [
                {"label": "View Grocery List →", "value": "Show my grocery list"}
            ]
            return state
        except Exception as e:
            logger.error(f"Error approving plan in chat: {e}")
            target_plan.status = "approved"
            if session:
                await session.commit()
            context_prompt = (
                f"Successfully approved meal plan '{target_plan.name}'. "
                f"In polite, natural English, let the user know their meal plan is approved. Ensure you mention that the plan is approved."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "approved"
            return state

    # =========================================================================
    # Step 6: Handle Edit / Update Plan Request
    # =========================================================================
    if intent_data.intent == "edit_meal":
        # Safeguard: Verify user query actually contains modification language
        has_edit_token = any(k in user_message.lower() for k in [
            "replace", "swap", "modify", "update", "change", "switch", "substitute", "edit",
            "breakfast", "lunch", "dinner", "snack"
        ])
        if len(all_plans) == 0:
            if not has_edit_token and not intent_data.is_update_request and not intent_data.edit_details:
                # Mistakenly routed to edit_meal; re-route to conversational app_info
                intent_data.intent = "app_info"
                response_text = await supervisor.handle_conversational_intent(
                    state=state,
                    user_message=user_message,
                    intent="app_info",
                    history=history
                )
                state.system_message = response_text
                state.status = "conversational"
                return state

            context_prompt = (
                "The user wants to update or edit their meal plan, but they don't have an active meal plan yet in their account. "
                "In friendly, polite English, let them know that you don't have an active meal plan yet to update, and warmly invite them to create one first. "
                "Include the phrase 'don\\'t have an active meal plan yet' naturally in your sentence."
            )
            response_text = await supervisor.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=user_profile.get("name")
            )
            state.system_message = response_text
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        # If user generally said "I want to update my meal plan" without specific meal dish instructions
        if intent_data.is_update_request:
            target_p = find_referenced_plan(
                user_message,
                all_plans,
                target_id=intent_data.target_plan_id,
                target_name=intent_data.target_plan_name
            )
            if target_p and (len(all_plans) == 1 or intent_data.target_plan_name or intent_data.target_plan_id):
                context_prompt = (
                    f"The user wants to update their meal plan '{target_p.name}'. "
                    f"In friendly, conversational English, ask what they would like to update in '{target_p.name}' (e.g. swapping a meal on a specific day, adjusting ingredients, or adding a healthy snack)."
                )
                response_text = await supervisor.generate_conversational_response(
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name")
                )
                state.system_message = response_text
                state.status = "conversational"
                state.quick_options = [
                    {"label": "Swap breakfast", "value": f"I want to update \"{target_p.name}\": swap day 1 breakfast with oatmeal"},
                    {"label": "Add a healthy snack", "value": f"Add a snack to \"{target_p.name}\""},
                    {"label": "Change diet to vegetarian", "value": f"Update \"{target_p.name}\" to vegetarian"}
                ]
                return state
            else:
                plan_bullets = [f"• **\"{p.name}\"** ({p.days}-day, {p.diet_type}, {int(p.calorie_target)} kcal)" for p in all_plans]
                context_prompt = (
                    f"The user wants to update a meal plan, and they have multiple plans: {', '.join(p.name for p in all_plans)}. "
                    f"In friendly, conversational English, ask which meal plan they would like to update."
                )
                response_text = await supervisor.generate_conversational_response(
                    user_message=user_message,
                    context=context_prompt,
                    history=history,
                    user_name=user_profile.get("name")
                )
                state.system_message = response_text
                state.status = "conversational"
                state.quick_options = [
                    {"label": f"Update {p.name}", "value": f"I want to update \"{p.name}\": "}
                    for p in all_plans
                ]
                return state

    # =========================================================================
    # Step 7: Handle General Conversational Intents (chat, app_info, out_of_scope, explain)
    # =========================================================================
    if intent_data.intent in ("chat", "app_info", "out_of_scope", "explain"):
        response_text = await supervisor.handle_conversational_intent(
            state=state,
            user_message=user_message,
            intent=intent_data.intent,
            history=history
        )
        state.system_message = response_text
        state.status = "conversational" if intent_data.intent != "out_of_scope" else "out_of_scope"
        return state

    # =========================================================================
    # Step 8: Handle New Plan (Diet Conflict Check & Calorie Guardrails)
    # =========================================================================
    if intent_data.intent == "new_plan":
        # Check and apply requested ingredient exclusions (e.g. dairy / diary)
        if "dairy" in intent_data.requested_exclusions or re.search(r'\b(diary|dairy)\b', user_message, re.IGNORECASE):
            if "allergies" not in user_profile:
                user_profile["allergies"] = []
            if "dairy" not in [str(a).lower() for a in user_profile["allergies"]]:
                user_profile["allergies"].append("dairy")

        profile_diet = str(user_profile.get("diet_type", "veg")).lower()
        req_diet = str(intent_data.requested_diet_type).lower() if intent_data.requested_diet_type else None
        diet_conflict = bool(req_diet and req_diet != profile_diet and not intent_data.diet_conflict_confirmed)

        req_cal = intent_data.requested_calories or user_profile.get("target_calories")
        req_days = intent_data.requested_days or 3
        is_safe, err_msg, meta = supervisor.check_input_guardrail(
            user_profile=user_profile,
            requested_calories=req_cal,
            requested_days=req_days,
            fixed_meals=fixed
        )

        # Check Diet Preference Conflict:
        if diet_conflict:
            req_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(req_diet, req_diet.capitalize())
            profile_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(profile_diet, profile_diet.capitalize())
            days_val = req_days
            cal_val = int(req_cal or 2000)

            if not is_safe:
                # Both diet conflict AND calorie safety trigger!
                suggested = int(meta.get("suggested_target", 2250.0))
                min_safe = int(meta.get("min_safe", 2150.0))
                max_safe = int(meta.get("max_safe", 2350.0))
                msg = (
                    f"Your profile preference is **{profile_display}**, but you requested a **{req_display}** meal plan. "
                    f"In addition, your requested target of **{cal_val:,} kcal/day** is below your maintenance range of {min_safe:,} – {max_safe:,} kcal.\n\n"
                    f"Can I create a **{days_val}-day {req_display}** plan for you? Would you like me to plan it at your requested **{cal_val:,} kcal/day**, or at your recommended target of **{suggested:,} kcal/day**?"
                )
                state.system_message = msg
                state.status = "conversational"
                state.quick_options = [
                    {"label": f"Plan {days_val} days at {cal_val:,} kcal", "value": f"Create {days_val}-day {req_display.lower()} meal plan for {cal_val} kcal (confirmed)"},
                    {"label": f"Plan {days_val} days at {suggested:,} kcal (Recommended)", "value": f"Create {days_val}-day {req_display.lower()} meal plan for {suggested} kcal (confirmed)"}
                ]
                await supervisor.log_step(
                    state=state,
                    from_agent="supervisor",
                    to_agent="user",
                    msg_type="feedback",
                    content=msg,
                    payload={"action": "diet_and_calorie_conflict_prompt", "profile_diet": profile_diet, "requested_diet": req_diet, "calories": cal_val}
                )
                return state
            else:
                msg = (
                    f"Your profile preference is **{profile_display}**, but now you are asking for a **{req_display}** meal plan. "
                    f"Can I go ahead and create with the {req_display} meal plan, or should I take your default profile {profile_display} diet type for planning this meal?"
                )
                state.system_message = msg
                state.status = "conversational"
                state.quick_options = [
                    {"label": f"Create {req_display} Plan", "value": f"Create {days_val}-day {req_display.lower()} meal plan for {cal_val} kcal (confirmed)"},
                    {"label": f"Use {profile_display} (Default)", "value": f"Create {days_val}-day {profile_display.lower()} meal plan for {cal_val} kcal (confirmed)"}
                ]
                await supervisor.log_step(
                    state=state,
                    from_agent="supervisor",
                    to_agent="user",
                    msg_type="feedback",
                    content=msg,
                    payload={"action": "diet_conflict_prompt", "profile_diet": profile_diet, "requested_diet": req_diet}
                )
                return state

        # Resolved effective diet type for this plan
        effective_diet = intent_data.chosen_diet_type or req_diet or profile_diet
        user_profile["diet_type"] = effective_diet
        state.user_profile["diet_type"] = effective_diet
        state.current_plan = None

        # Ensure state reflects requested days and calories explicitly
        state.days = req_days
        state.calorie_target = req_cal

        # Calorie Guardrail Check (if user did not confirm conflict)
        if not is_safe and not intent_data.diet_conflict_confirmed:
            msg = err_msg or "The requested calorie target is outside safe clinical guidelines for your profile."
            state.system_message = msg
            state.status = "conversational"
            suggested = meta.get("suggested_target", 2350.0)
            min_safe = meta.get("min_safe", 2250.0)
            max_safe = meta.get("max_safe", 2450.0)
            diet_lbl = {"veg": "veg", "non-veg": "non-veg", "vegan": "vegan"}.get(effective_diet, "veg")
            state.quick_options = [
                {"label": f"Plan at {int(suggested):,} kcal (Recommended)", "value": f"Plan {req_days} days {diet_lbl} for {int(suggested)} kcal"},
                {"label": f"Plan at {int(min_safe):,} kcal (Min Safe)", "value": f"Plan {req_days} days {diet_lbl} for {int(min_safe)} kcal"},
                {"label": f"Plan at {int(max_safe):,} kcal (Max Safe)", "value": f"Plan {req_days} days {diet_lbl} for {int(max_safe)} kcal"}
            ]
            await supervisor.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="user",
                msg_type="feedback",
                content=msg,
                payload={"action": "guardrail_block", "calories": req_cal, "suggested": suggested}
            )
            return state

    # If editing existing plan, make sure user_profile["diet_type"] reflects the existing plan's diet
    if intent_data.intent == "edit_meal" and existing_plan:
        user_profile["diet_type"] = effective_plan_diet

    # =========================================================================
    # Step 9: Execute LangGraph Workflow
    # =========================================================================
    graph = create_meal_planner_graph()
    result = await graph.ainvoke(state)

    if isinstance(result, PlanState):
        final_state = result
    else:
        final_state = PlanState.model_validate(result)

    # =========================================================================
    # Step 10: Persist Plan to Database & Format Friendly Response
    # =========================================================================
    if final_state.current_plan and final_state.status in ("reviewing", "approved"):
        try:
            if session:
                repo = PlanRepository(session)
                if intent_data.intent == "edit_meal" and existing_plan and db_plan_id:
                    new_version = (existing_plan.version or 1) + 1
                    updated_p = await repo.update_plan(
                        plan_id=db_plan_id,
                        meal_plan=final_state.current_plan,
                        grocery_list=None,  # Reset grocery list until user approves!
                        status="draft",  # Version X Draft upon modification!
                        increment_version=True
                    )
                    final_state.plan_version = new_version
                    final_state.status = "draft"
                    clean_inst = getattr(intent_data.edit_details, "instructions", "") or user_message
                    clean_inst = re.sub(r'\(Plan\s*#\d+\):?', '', clean_inst, flags=re.IGNORECASE).strip()
                    context_prompt = (
                        f"Successfully applied the user's edit request '{clean_inst}' to meal plan '{plan_title}'. "
                        f"The plan has been updated to Version {new_version} Draft in the Plans tab. "
                        f"In natural, polite, friendly conversational English, let the user know their modification has been applied to '{plan_title}' and that they can review it in the Plans tab and approve it whenever ready."
                    )
                    final_state.system_message = await supervisor.generate_conversational_response(
                        user_message=user_message,
                        context=context_prompt,
                        history=history,
                        user_name=user_profile.get("name")
                    )
                    final_state.quick_options = [
                        {"label": f"Approve {plan_title}", "value": f"Approve \"{plan_title}\""},
                        {"label": "Modify in chat", "value": f"I want to update \"{plan_title}\": "}
                    ]
                    logger.info(f"[{tid}] Updated existing plan '{plan_title}' to Version {new_version} Draft.")
                else:
                    plan_diet_str = user_profile.get("diet_type", "veg")
                    final_state.current_plan.diet_type = plan_diet_str
                    p_name = f"{len(final_state.current_plan.days)}-Day {plan_diet_str.capitalize()} Plan"
                    final_state.current_plan.name = p_name
                    created_p = await repo.create_plan(
                        user_id=user_id,
                        days=len(final_state.current_plan.days),
                        calorie_target=final_state.current_plan.target_calories,
                        meal_plan=final_state.current_plan,
                        grocery_list=None,  # Do not generate grocery list until user approves!
                        status="draft",  # Version 1 Draft upon initial creation!
                        name=p_name,
                        diet_type=plan_diet_str,
                        version=1
                    )
                    final_state.plan_version = 1
                    final_state.status = "draft"
                    context_prompt = (
                        f"Successfully generated new meal plan '{p_name}' (Version 1 Draft) with a target of {int(final_state.current_plan.target_calories):,} kcal/day across {len(final_state.current_plan.days)} days. "
                        f"In natural, polite, friendly conversational English, let the user know their meal plan has been created and is ready to review in the Plans tab, and invite them to approve it or make adjustments whenever ready."
                    )
                    final_state.system_message = await supervisor.generate_conversational_response(
                        user_message=user_message,
                        context=context_prompt,
                        history=history,
                        user_name=user_profile.get("name")
                    )
                    final_state.quick_options = [
                        {"label": f"Approve {p_name}", "value": f"Approve \"{p_name}\""},
                        {"label": "Modify in chat", "value": f"I want to update \"{p_name}\": "}
                    ]
                    logger.info(f"[{tid}] Persisted new plan to database as Version 1 Draft.")
            else:
                async with AsyncSessionLocal() as temp_session:
                    repo = PlanRepository(temp_session)
                    if intent_data.intent == "edit_meal" and existing_plan and db_plan_id:
                        new_version = (existing_plan.version or 1) + 1
                        await repo.update_plan(
                            plan_id=db_plan_id,
                            meal_plan=final_state.current_plan,
                            grocery_list=None,
                            status="draft",
                            increment_version=True
                        )
                        final_state.plan_version = new_version
                        final_state.status = "draft"
                        clean_inst = getattr(intent_data.edit_details, "instructions", "") or user_message
                        clean_inst = re.sub(r'\(Plan\s*#\d+\):?', '', clean_inst, flags=re.IGNORECASE).strip()
                        plan_title = getattr(final_state.current_plan, "name", "your meal plan")
                        context_prompt = (
                            f"Successfully applied the user's edit request '{clean_inst}' to meal plan '{plan_title}'. "
                            f"The plan has been updated to Version {new_version} Draft in the Plans tab. "
                            f"In natural, polite, friendly conversational English, let the user know their modification has been applied to '{plan_title}' and that they can review it in the Plans tab and approve it whenever ready."
                        )
                        final_state.system_message = await supervisor.generate_conversational_response(
                            user_message=user_message,
                            context=context_prompt,
                            history=history,
                            user_name=user_profile.get("name")
                        )
                        final_state.quick_options = [
                            {"label": f"Approve {plan_title}", "value": f"Approve \"{plan_title}\""},
                            {"label": "Modify in chat", "value": f"I want to update \"{plan_title}\": "}
                        ]
                    else:
                        plan_diet_str = user_profile.get("diet_type", "veg")
                        p_name = f"{len(final_state.current_plan.days)}-Day {plan_diet_str.capitalize()} Plan"
                        await repo.create_plan(
                            user_id=user_id,
                            days=len(final_state.current_plan.days),
                            calorie_target=final_state.current_plan.target_calories,
                            meal_plan=final_state.current_plan,
                            grocery_list=None,
                            status="draft",
                            name=p_name,
                            diet_type=plan_diet_str,
                            version=1
                        )
                        final_state.plan_version = 1
                        final_state.status = "draft"
                        context_prompt = (
                            f"Successfully generated new meal plan '{p_name}' (Version 1 Draft) with a target of {int(final_state.current_plan.target_calories):,} kcal/day across {len(final_state.current_plan.days)} days. "
                            f"In natural, polite, friendly conversational English, let the user know their meal plan has been created and is ready to review in the Plans tab, and invite them to approve it or make adjustments whenever ready."
                        )
                        final_state.system_message = await supervisor.generate_conversational_response(
                            user_message=user_message,
                            context=context_prompt,
                            history=history,
                            user_name=user_profile.get("name")
                        )
                        final_state.quick_options = [
                            {"label": f"Approve {p_name}", "value": f"Approve \"{p_name}\""},
                            {"label": "Modify in chat", "value": f"I want to update \"{p_name}\": "}
                        ]
        except Exception as e:
            logger.error(f"[{tid}] Failed to persist plan to database: {e}")

    return final_state
