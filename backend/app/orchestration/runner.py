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
        # Match target_plan_id if specified, else latest
        if intent_data.target_plan_id:
            matched = next((p for p in all_plans if p.id == intent_data.target_plan_id), None)
            if matched:
                existing_plan = PlanRepository.to_meal_plan_domain(matched)
                db_plan_id = matched.id
        if not existing_plan:
            existing_plan = PlanRepository.to_meal_plan_domain(all_plans[0])
            db_plan_id = all_plans[0].id

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
        current_plan=existing_plan,
        plan_version=(existing_plan.version + 1) if existing_plan else 1,
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
            msg = (
                "You haven't created any meal plans yet to rename! 🥗\n\n"
                "Would you like me to create one for you? Just let me know: *\"Plan a 3-day 1800 kcal meal plan\"*!"
            )
            state.system_message = msg
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        target_plan = None
        if intent_data.target_plan_id:
            target_plan = next((p for p in all_plans if p.id == intent_data.target_plan_id), None)
        elif len(all_plans) == 1:
            target_plan = all_plans[0]

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
                logger.error(f"[{tid}] Failed to rename plan #{target_plan.id}: {e}")

            msg = f"Meal Plan #{target_plan.id} has been renamed to **\"{new_title}\"**! ✏️"
            state.system_message = msg
            state.status = "approved"
            state.plan_id = target_plan.id
            return state
        elif target_plan:
            msg = f"What would you like to rename **\"{target_plan.name}\"** (Plan #{target_plan.id}) to? Reply with the new name!"
            state.system_message = msg
            state.status = "conversational"
            return state
        else:
            plan_bullets = [f"• **Plan #{p.id}**: {p.name}" for p in all_plans]
            msg = (
                f"You have multiple meal plans in your Plans tab:\n\n"
                + "\n".join(plan_bullets)
                + "\n\nPlease specify which plan you want to rename (e.g. *\"Rename Plan #{all_plans[0].id} to High Protein\"*):"
            )
            state.system_message = msg
            state.status = "conversational"
            state.quick_options = [
                {"label": f"Rename {p.name}", "value": f"Rename Plan #{p.id} to "}
                for p in all_plans
            ]
            return state

    # =========================================================================
    # Step 4: Handle Add Snack Intent
    # =========================================================================
    if intent_data.intent == "add_snack":
        if len(all_plans) == 0:
            msg = (
                "You don't have an active meal plan yet to add a snack to! 🥗\n\n"
                "Would you like me to create a meal plan first? Just ask: *\"Plan a 3-day 1800 kcal meal plan\"*!"
            )
            state.system_message = msg
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        target_plan = all_plans[0]
        if not intent_data.snack_rebalance_choice:
            target_cal = int(target_plan.calorie_target or 2000)
            msg = (
                f"Adding a snack may exceed your daily calorie target of {target_cal:,} kcal. Would you like me to:\n\n"
                f"1) **Add a low-calorie snack** (100–150 kcal fresh fruit/nuts) keeping main meals as planned, or\n"
                f"2) **Rebalance all 4 meals** (Breakfast ~25%, Lunch ~35%, Dinner ~25%, Snack ~15%) across the day to stay within your exact target calorie range?"
            )
            state.system_message = msg
            state.status = "conversational"
            state.quick_options = [
                {"label": "Add low-calorie snack", "value": "Add a low-calorie snack to my meal plan"},
                {"label": "Rebalance all 4 meals", "value": "Rebalance all 4 meals including snack within target calories"}
            ]
            return state
        else:
            # User confirmed snack mode: route to edit_meal with explicit instructions
            intent_data.intent = "edit_meal"
            choice = intent_data.snack_rebalance_choice
            inst = "Add a healthy low-calorie snack (100-150 kcal)" if choice == "low_calorie" else "Rebalance all 4 meals (breakfast, lunch, dinner, snack) to meet target calories"
            intent_data.edit_details = EditDetails(meal_type="snack", instructions=inst)

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
            or "reply 'yes, delete it'" in last_asst_content
            or "reply \"yes, delete it\"" in last_asst_content
            or "which meal plan should be deleted" in last_asst_content
        )

        cleaned_user_msg = user_message.strip().lower()
        is_cancelling = any(w in cleaned_user_msg for w in ["no", "cancel", "don't", "keep it", "stop"])

        if is_pending_confirmation and is_cancelling:
            msg = "Cancelled deletion. Your meal plan is kept safely! 🛡️ Let me know if you would like to make any adjustments instead."
            state.system_message = msg
            state.status = "conversational"
            return state

        # Determine target plan ID if explicitly stated
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
            plan_to_delete = None
            if target_id:
                plan_to_delete = next((p for p in all_plans if p.id == target_id), None)

            if not plan_to_delete and is_pending_confirmation:
                m_prev = re.search(r'Plan\s*#?(\d+)', last_asst_content, re.IGNORECASE)
                if m_prev:
                    prev_id = int(m_prev.group(1))
                    plan_to_delete = next((p for p in all_plans if p.id == prev_id), None)

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
                    logger.info(f"[{tid}] Deleted plan #{del_id} for user {user_id}")
                except Exception as e:
                    logger.error(f"[{tid}] Error executing plan deletion #{del_id}: {e}")

                remaining_count = len(all_plans) - 1
                msg = (
                    f"Meal Plan #{del_id} (**\"{del_name}\"**) has been permanently deleted! 🗑️\n\n"
                    f"{'You currently have no saved meal plans. Would you like to create a new one?' if remaining_count == 0 else f'You have {remaining_count} meal plan remaining in your Plans tab.'}"
                )
                state.system_message = msg
                state.status = "deleted"
                state.deleted_plan_id = del_id
                state.quick_options = [
                    {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                    {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
                ]
                return state
            else:
                msg = "I couldn't identify which plan to delete. Please specify the plan number (e.g. *\"Delete Plan #<id>\"*) or select one from the Plans tab."
                state.system_message = msg
                state.status = "conversational"
                return state

        # Case 0: 0 plans exist
        if len(all_plans) == 0:
            msg = (
                "You don't have any active meal plans to delete! 🥗\n\n"
                "Would you like me to create one first? Just ask me in the chat: *\"Plan a 3-day 1800 kcal meal plan\"*!"
            )
            state.system_message = msg
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        # Case 1: 1 plan exists or user specified target_id
        if target_id or len(all_plans) == 1:
            target_plan = next((p for p in all_plans if p.id == target_id), None) if target_id else all_plans[0]
            if target_plan:
                msg = (
                    f"You have created **\"{target_plan.name}\"** recently (**Plan #{target_plan.id}**: {target_plan.days}-day plan, "
                    f"{int(target_plan.calorie_target)} kcal/day, status: {target_plan.status.capitalize()}).\n\n"
                    f"Can I delete it? Are you sure you want to delete this meal plan?\n\n"
                    f"Reply **'Yes, delete it'** to confirm, or **'Cancel'** to keep it."
                )
                state.system_message = msg
                state.status = "conversational"
                state.quick_options = [
                    {"label": f"Yes, delete {target_plan.name}", "value": f"Yes, delete Plan #{target_plan.id}"},
                    {"label": "Cancel", "value": "Cancel"}
                ]
                return state

        # Case 2: Multiple plans exist
        plan_bullets = []
        options = []
        for p in all_plans:
            plan_bullets.append(f"• **Plan #{p.id}**: {p.name} ({p.days}-day, {int(p.calorie_target)} kcal/day)")
            options.append({"label": f"Delete {p.name} (#{p.id})", "value": f"Delete Plan #{p.id}"})
        options.append({"label": "Cancel", "value": "Cancel"})

        bullets_str = "\n".join(plan_bullets)
        msg = (
            f"You have multiple meal plans ({len(all_plans)} saved meal plans).\n\n"
            f"Please select the one you want to delete:\n\n"
            f"{bullets_str}\n\n"
            f"You can choose from the options below or reply with the plan name or number."
        )
        state.system_message = msg
        state.status = "conversational"
        state.quick_options = options
        return state

    # =========================================================================
    # Step 6: Handle Edit / Update Plan Request
    # =========================================================================
    if intent_data.intent == "edit_meal":
        if len(all_plans) == 0:
            msg = (
                "You haven't created any meal plans yet to update! 🥗\n\n"
                "Would you like me to create one for you first? Just ask: *\"Plan a 3-day 1800 kcal meal plan\"*!"
            )
            state.system_message = msg
            state.status = "conversational"
            state.quick_options = [
                {"label": "Plan 3 days (1800 kcal)", "value": "Plan 3 days for 1800 kcal"},
                {"label": "Plan 7 days (veg)", "value": "Plan 7 days vegetarian meal plan"}
            ]
            return state

        # If user generally said "I want to update my meal plan" without specific meal dish instructions
        if intent_data.is_update_request:
            if len(all_plans) == 1:
                target_p = all_plans[0]
                msg = (
                    f"You have created **\"{target_p.name}\"** recently. "
                    f"Tell me what you want to update in this meal plan?"
                )
                state.system_message = msg
                state.status = "conversational"
                state.quick_options = [
                    {"label": "Swap breakfast tomorrow", "value": f"Update Plan #{target_p.id} swap day 1 breakfast with oatmeal"},
                    {"label": "Add a healthy snack", "value": f"Add a snack to Plan #{target_p.id}"},
                    {"label": "Change diet to vegetarian", "value": f"Update Plan #{target_p.id} to vegetarian"}
                ]
                return state
            else:
                # Multiple plans: check if target_plan_id was specified
                target_p = next((p for p in all_plans if p.id == intent_data.target_plan_id), None)
                if not target_p:
                    plan_bullets = [f"• **Plan #{p.id}**: {p.name} ({p.days}-day, {p.diet_type}, {int(p.calorie_target)} kcal)" for p in all_plans]
                    msg = (
                        f"You have multiple meal plans in your Plans tab:\n\n"
                        + "\n".join(plan_bullets)
                        + "\n\nPlease select the one you want to update:"
                    )
                    state.system_message = msg
                    state.status = "conversational"
                    state.quick_options = [
                        {"label": f"Update {p.name} (#{p.id})", "value": f"I want to update Plan #{p.id}: "}
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
        profile_diet = str(user_profile.get("diet_type", "veg")).lower()
        req_diet = str(intent_data.requested_diet_type).lower() if intent_data.requested_diet_type else None

        # Check Diet Preference Conflict:
        # If user explicitly requested a diet type different from profile and not yet confirmed:
        if req_diet and req_diet != profile_diet and not intent_data.diet_conflict_confirmed:
            req_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(req_diet, req_diet.capitalize())
            profile_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(profile_diet, profile_diet.capitalize())
            days_val = intent_data.requested_days or 3
            cal_val = int(intent_data.requested_calories or 2000)

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

        # Calorie Guardrail Check
        req_cal = intent_data.requested_calories or user_profile.get("target_calories")
        req_days = intent_data.requested_days or 3
        is_safe, err_msg, meta = supervisor.check_input_guardrail(
            user_profile=user_profile,
            requested_calories=req_cal,
            requested_days=req_days,
            fixed_meals=fixed
        )
        if not is_safe:
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
                        grocery_list=final_state.grocery_list,
                        status="draft",  # Version X Draft upon modification!
                        increment_version=True
                    )
                    final_state.plan_version = new_version
                    final_state.status = "draft"
                    inst_text = getattr(intent_data.edit_details, "instructions", "") or user_message
                    final_state.system_message = (
                        f"I've updated your meal plan! 🥗 Your request ('{inst_text}') has been applied in place.\n\n"
                        f"The plan is now updated to **Version {new_version} Draft** in your Plans tab. "
                        f"You can review it and click **'Approve & Generate Grocery List'** whenever you're ready!"
                    )
                    logger.info(f"[{tid}] Updated existing plan #{db_plan_id} to Version {new_version} Draft.")
                else:
                    plan_diet_str = user_profile.get("diet_type", "veg")
                    p_name = f"{len(final_state.current_plan.days)}-Day {plan_diet_str.capitalize()} Plan"
                    created_p = await repo.create_plan(
                        user_id=user_id,
                        days=len(final_state.current_plan.days),
                        calorie_target=final_state.current_plan.target_calories,
                        meal_plan=final_state.current_plan,
                        grocery_list=final_state.grocery_list,
                        status="draft",  # Version 1 Draft upon initial creation!
                        name=p_name,
                        diet_type=plan_diet_str,
                        version=1
                    )
                    final_state.plan_version = 1
                    final_state.status = "draft"
                    final_state.system_message = (
                        f"Your **{p_name}** (**Version 1 Draft**) has been generated! 🥗\n\n"
                        f"Daily calories: {int(final_state.current_plan.target_calories):,} kcal/day distributed across "
                        f"Breakfast (30%), Lunch (40%), and Dinner (30%).\n\n"
                        f"You can inspect the daily menu in your Plans tab and click **'Approve & Generate Grocery List'** whenever you're ready!"
                    )
                    logger.info(f"[{tid}] Persisted new plan to database as Version 1 Draft.")
            else:
                async with AsyncSessionLocal() as temp_session:
                    repo = PlanRepository(temp_session)
                    if intent_data.intent == "edit_meal" and existing_plan and db_plan_id:
                        new_version = (existing_plan.version or 1) + 1
                        await repo.update_plan(
                            plan_id=db_plan_id,
                            meal_plan=final_state.current_plan,
                            grocery_list=final_state.grocery_list,
                            status="draft",
                            increment_version=True
                        )
                        final_state.plan_version = new_version
                        final_state.status = "draft"
                        inst_text = getattr(intent_data.edit_details, "instructions", "") or user_message
                        final_state.system_message = (
                            f"I've updated your meal plan! 🥗 Your request ('{inst_text}') has been applied in place.\n\n"
                            f"The plan is now updated to **Version {new_version} Draft** in your Plans tab. "
                            f"You can review it and click **'Approve & Generate Grocery List'** whenever you're ready!"
                        )
                    else:
                        plan_diet_str = user_profile.get("diet_type", "veg")
                        p_name = f"{len(final_state.current_plan.days)}-Day {plan_diet_str.capitalize()} Plan"
                        await repo.create_plan(
                            user_id=user_id,
                            days=len(final_state.current_plan.days),
                            calorie_target=final_state.current_plan.target_calories,
                            meal_plan=final_state.current_plan,
                            grocery_list=final_state.grocery_list,
                            status="draft",
                            name=p_name,
                            diet_type=plan_diet_str,
                            version=1
                        )
                        final_state.plan_version = 1
                        final_state.status = "draft"
                        final_state.system_message = (
                            f"Your **{p_name}** (**Version 1 Draft**) has been generated! 🥗\n\n"
                            f"Daily calories: {int(final_state.current_plan.target_calories):,} kcal/day distributed across "
                            f"Breakfast (30%), Lunch (40%), and Dinner (30%).\n\n"
                            f"You can inspect the daily menu in your Plans tab and click **'Approve & Generate Grocery List'** whenever you're ready!"
                        )
        except Exception as e:
            logger.error(f"[{tid}] Failed to persist plan to database: {e}")

    return final_state
