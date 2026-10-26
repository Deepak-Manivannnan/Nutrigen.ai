import asyncio
from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, START, END
from backend.app.schemas.state import PlanState
from backend.app.schemas.domain import MealPlan, NutritionReport, AllergyReport
from backend.app.agents.planner.agent import PlannerAgent
from backend.app.agents.nutrition.agent import NutritionAgent
from backend.app.agents.allergy.agent import AllergyAgent
from backend.app.orchestration.supervisor.agent import SupervisorAgent
from backend.app.observability.logging import get_logger

logger = get_logger("orchestration.graph")

def create_meal_planner_graph(
    planner: Optional[PlannerAgent] = None,
    nutrition: Optional[NutritionAgent] = None,
    allergy: Optional[AllergyAgent] = None,
    supervisor: Optional[SupervisorAgent] = None
):
    """
    Constructs the LangGraph multi-agent workflow.
    Rule: Worker agents communicate only with the Supervisor (hub-and-spoke).
    Nutrition and Allergy execute reviews in parallel.

    The graph ends at a reviewed DRAFT. The Shopper and the final output
    guardrail are not part of drafting: they run only when the user approves
    the plan (see orchestration/approval.py), so the grocery list is built once,
    for the version the user actually approved.
    """
    p_agent = planner or PlannerAgent()
    n_agent = nutrition or NutritionAgent()
    a_agent = allergy or AllergyAgent()
    sup_agent = supervisor or SupervisorAgent()

    # --- Node Definitions ---

    async def supervisor_init_node(state: PlanState) -> Dict[str, Any]:
        """Supervisor Step 1: Input guardrail validation."""
        is_safe, err_msg, meta = sup_agent.check_input_guardrail(
            user_profile=state.user_profile,
            requested_calories=state.calorie_target,
            requested_days=state.days,
            fixed_meals=state.fixed_meals
        )

        if not is_safe:
            msg = await sup_agent.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="user",
                msg_type="error",
                content=f"Input Guardrail Blocked: {err_msg}",
                payload={"error": err_msg}
            )
            return {
                "status": "failed",
                "system_message": err_msg,
                "trace": [msg]
            }

        days = len(state.current_plan.days) if state.current_plan else meta["days"]
        if not days:
            # The number of days is never assumed. The chat layer asks for it
            # before the graph starts; reaching here without it is a bug, so
            # stop rather than quietly plan some other length.
            msg = await sup_agent.log_step(
                state=state, from_agent="supervisor", to_agent="user", msg_type="error",
                content="Input Guardrail Blocked: the number of days is missing.",
                payload={"error": "days_missing"}
            )
            return {
                "status": "failed",
                "system_message": "I need to know how many days the plan should cover before I can build it.",
                "trace": [msg]
            }
        target_cal = state.current_plan.target_calories if state.current_plan else meta["calorie_target"]

        action_desc = "Modifying meal in existing plan" if state.intent == "edit_meal" else f"Assigning {days}-day plan ({target_cal:.0f} kcal/day)"
        msg = await sup_agent.log_step(
            state=state,
            from_agent="supervisor",
            to_agent="planner_agent",
            msg_type="task",
            content=f"Supervisor validated constraints. {action_desc} to Planner Agent.",
            payload={
                "task": "modify_meal" if state.intent == "edit_meal" else "plan_meals",
                "days": days,
                "calorie_target": target_cal,
                "fixed_meals": state.fixed_meals,
                "edit_details": state.edit_details.model_dump() if hasattr(state.edit_details, "model_dump") else state.edit_details
            }
        )

        return {
            "days": days,
            "calorie_target": target_cal,
            "status": "drafting",
            "system_message": None,
            "trace": [msg]
        }

    async def planner_node(state: PlanState) -> Dict[str, Any]:
        """Planner Agent: Generates or revises the meal plan."""
        feedback_list = []
        if state.system_message and state.round > 1:
            feedback_list = state.system_message.splitlines()

        # On a retry round the Supervisor has already worked out, from the two
        # reviews, exactly which meals/days to redo and put that instruction on
        # the state. The Planner redoes only those (see repair_plan); if there
        # is no specific instruction, the whole plan is regenerated as before.
        repair_targets = None
        if (
            state.intent != "edit_meal"
            and state.round > 1
            and state.current_plan
            and state.repair_targets is not None
            and not state.repair_targets.is_empty
            and len(state.current_plan.days) == (state.days or len(state.current_plan.days))
        ):
            repair_targets = state.repair_targets

        if state.intent == "edit_meal" and state.current_plan and state.edit_details:
            day_num = getattr(state.edit_details, "day", None) or 1
            m_type = getattr(state.edit_details, "meal_type", None) or "meal"
            msg_start = await sup_agent.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="planner_agent",
                msg_type="task",
                content=f"Planner Agent modifying meal slot (Day {day_num} {m_type}): '{getattr(state.edit_details, 'instructions', '')}'...",
                payload={"task": "modify_meal", "day": day_num, "meal_type": m_type}
            )
            plan: MealPlan = await p_agent.modify_meal(
                current_plan=state.current_plan,
                edit_details=state.edit_details,
                user_profile=state.user_profile,
                task_id=state.task_id,
                exclusions=(state.intent_data.requested_exclusions if state.intent_data else None),
                cuisine=getattr(state, "cuisine", None),
            )
        else:
            plan: Optional[MealPlan] = None

            if repair_targets is not None:
                # Retry round: only the meals/days the reviewers flagged are
                # redone; everything that passed is carried over unchanged.
                msg_start = await sup_agent.log_step(
                    state=state,
                    from_agent="supervisor",
                    to_agent="planner_agent",
                    msg_type="task",
                    content=f"Planner Agent repairing only the flagged parts ({repair_targets.describe()}); all other meals stay unchanged...",
                    payload={
                        "task": "plan_meals",  # same task name as a full pass, so the progress bar treats it the same
                        "repair": True,
                        "days": sorted(i.day_number for i in repair_targets.whole_days()),
                        "meals": [f"day {i.day_number} {i.meal_type}" for i in repair_targets.single_meals()],
                    }
                )
                try:
                    plan = await p_agent.repair_plan(
                        current_plan=state.current_plan,
                        targets=repair_targets,
                        calorie_target=state.calorie_target or state.current_plan.target_calories,
                        user_profile=state.user_profile,
                        version=state.plan_version,
                        task_id=state.task_id,
                        exclusions=state.intent_data.requested_exclusions if state.intent_data else None,
                        cuisine=getattr(state, "cuisine", None),
                        style_notes=state.style_notes,
                        main_ingredients=state.main_ingredients,
                    )
                except Exception as repair_err:
                    # Never leave the user without a plan because a repair
                    # failed: fall back to writing the whole plan again.
                    logger.warning(f"Targeted repair failed ({repair_err}); regenerating the whole plan instead.")
                    plan = None

            if plan is None:
                msg_start = await sup_agent.log_step(
                    state=state,
                    from_agent="supervisor",
                    to_agent="planner_agent",
                    msg_type="task",
                    content=f"Planner Agent starting meal schedule generation for {state.days} days ({state.calorie_target:.0f} kcal/day target)...",
                    payload={"task": "plan_meals", "days": state.days, "calorie_target": state.calorie_target}
                )

                plan = await p_agent.generate_plan(
                    days=state.days,
                    calorie_target=state.calorie_target or 2000.0,
                    user_profile=state.user_profile,
                    fixed_meals=state.fixed_meals,
                    feedback=feedback_list,
                    version=state.plan_version,
                    task_id=state.task_id,
                    current_plan=state.current_plan,
                    plan_name=getattr(state, "plan_name", None),
                    cuisine=getattr(state, "cuisine", None),
                    exclusions=state.intent_data.requested_exclusions if state.intent_data else None,
                    include_snack=getattr(state, "include_snack", False) or (getattr(state.intent_data, "include_snack", False) if state.intent_data else False),
                    style_notes=state.style_notes,
                    main_ingredients=state.main_ingredients,
                )


        msg = await sup_agent.log_step(
            state=state,
            from_agent="planner_agent",
            to_agent="supervisor",
            msg_type="draft",
            content=f"Planner Agent completed draft v{plan.version} ({len(plan.days)} days, avg {plan.average_daily_calories:.0f} kcal/day).",
            payload={
                "plan_version": plan.version,
                "days_count": len(plan.days),
                "avg_calories": plan.average_daily_calories
            }
        )

        return {
            "current_plan": plan,
            "status": "reviewing",
            # The instruction has been carried out; the next retry (if any) gets a fresh one.
            "repair_targets": None,
            "trace": [msg_start, msg]
        }

    async def parallel_reviews_node(state: PlanState) -> Dict[str, Any]:
        """Supervisor Step 3: Run Nutrition & Allergy checks in parallel."""
        plan = state.current_plan
        if not plan:
            return {"status": "failed", "system_message": "Missing plan for review."}

        msg_start = await sup_agent.log_step(
            state=state,
            from_agent="supervisor",
            to_agent="all",
            msg_type="task",
            content="Supervisor initiating parallel reviews: Nutrition Agent (±5% macros) & Allergy Agent (80+ allergens).",
            payload={"task": "parallel_reviews"}
        )

        # Dispatch parallel review tasks
        nutrition_task = n_agent.review_plan(
            plan=plan,
            calorie_target=state.calorie_target or 2000.0,
            user_profile=state.user_profile,
            task_id=state.task_id
        )
        allergy_task = a_agent.review_plan(
            plan=plan,
            user_allergies=state.user_profile.get("allergies", []),
            task_id=state.task_id
        )

        review_results = await asyncio.gather(nutrition_task, allergy_task, return_exceptions=True)

        # Fail CLOSED. A verifier that crashed did not clear the plan - it
        # never got to judge it. Recording a crash as "passed" would put an
        # unverified plan in front of the user as approved, and for allergy
        # that means an unscreened ingredient list. An honest error is safer
        # than a false confirmation, so the turn stops here.
        nutrition_failed = isinstance(review_results[0], Exception)
        allergy_failed = isinstance(review_results[1], Exception)

        if nutrition_failed or allergy_failed:
            failed_checks: list[str] = []
            if nutrition_failed:
                logger.error("Nutrition review crashed; plan NOT verified.", exc_info=review_results[0])
                failed_checks.append("nutrition")
            if allergy_failed:
                logger.error("Allergy review crashed; plan NOT verified.", exc_info=review_results[1])
                failed_checks.append("allergen")

            if nutrition_failed and allergy_failed:
                user_message = (
                    "Both our nutrition and allergen checks ran into a backend problem, so we could "
                    "not verify your plan. Nothing has been approved - please try again in a few minutes."
                )
            elif nutrition_failed:
                user_message = (
                    "Our nutrition checker ran into a backend problem, so we could not verify the "
                    "calories and macros in your plan. Nothing has been approved - please try again "
                    "in a few minutes."
                )
            else:
                user_message = (
                    "Our allergen checker ran into a backend problem, so we could not screen your "
                    "plan for allergens. Nothing has been approved - please try again in a few minutes."
                )

            msg_err = await sup_agent.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="user",
                msg_type="error",
                content=f"Verification unavailable - failed: {', '.join(failed_checks)}. {user_message}",
                payload={"failed_checks": failed_checks}
            )
            return {
                "status": "failed",
                "system_message": user_message,
                "trace": [msg_start, msg_err],
            }

        n_report = review_results[0]
        a_report = review_results[1]

        # Log individual worker reports back to Supervisor
        msg_n = await sup_agent.log_step(
            state=state,
            from_agent="nutrition_agent",
            to_agent="supervisor",
            msg_type="approve" if n_report.passed else "feedback",
            content=f"Nutrition Agent: {'Approved' if n_report.passed else 'Feedback'} — {n_report.summary}",
            payload={
                "passed": n_report.passed,
                "issues_count": len(n_report.issues),
                "summary": n_report.summary
            }
        )

        msg_a = await sup_agent.log_step(
            state=state,
            from_agent="allergy_agent",
            to_agent="supervisor",
            msg_type="approve" if a_report.passed else "veto",
            content=f"Allergy Agent: {'Approved' if a_report.passed else 'HARD VETO'} — {a_report.summary}",
            payload={
                "passed": a_report.passed,
                "violations_count": len(a_report.violations),
                "summary": a_report.summary
            }
        )

        return {
            "nutrition_report": n_report,
            "allergy_report": a_report,
            "trace": [msg_start, msg_n, msg_a]
        }

    async def supervisor_evaluate_node(state: PlanState) -> Dict[str, Any]:
        """Supervisor Step 4: Evaluate review reports with deterministic routing."""
        # A verification crash already failed the run in parallel_reviews.
        # Returning early stops decide_next_step from reading two None reports
        # as "both passed" and overwriting the honest error message.
        if state.status == "failed":
            return {}

        decision = sup_agent.evaluate_reviews(state)

        if decision.action == "retry_planner":
            feedback_str = "\n".join(decision.feedback)
            msg = await sup_agent.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="planner_agent",
                msg_type="feedback",
                content=f"Supervisor requesting Planner revision (Round {state.round + 1}): {decision.reason}",
                payload={
                    "feedback": decision.feedback,
                    "next_round": state.round + 1,
                    "repair": decision.repair.describe() if decision.repair else None,
                }
            )
            return {
                "round": state.round + 1,
                "plan_version": state.plan_version + 1,
                "system_message": feedback_str,
                # The Supervisor's instruction to the Planner: redo exactly these.
                "repair_targets": decision.repair,
                "trace": [msg]
            }

        elif decision.action == "escalate":
            msg = await sup_agent.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="user",
                msg_type="escalate",
                content=f"Supervisor escalating to user: {decision.reason}",
                payload={"reason": decision.reason}
            )
            return {
                "status": "escalated",
                "system_message": decision.reason,
                "trace": [msg]
            }

        # Both reviews passed: the reviewed draft goes back to the user for
        # approval. No grocery list yet - that is built only on approval.
        msg = await sup_agent.log_step(
            state=state,
            from_agent="supervisor",
            to_agent="user",
            msg_type="approve",
            content="Nutrition and Allergy reviews passed. Draft plan ready for the user to review and approve.",
            payload={"summary": "Draft plan ready for user review and approval."}
        )
        return {
            "status": "reviewing",
            "system_message": "Your meal plan has been checked and is ready for your review.",
            "trace": [msg]
        }

    # --- Conditional Edge Functions ---

    def route_after_init(state: PlanState) -> str:
        if state.status == "failed":
            return END
        return "planner"

    def route_after_evaluation(state: PlanState) -> str:
        if state.status == "escalated":
            return END
        # Verification crashed - the plan was never judged. Stop here rather
        # than falling through to the Shopper on the strength of a crash.
        if state.status == "failed":
            return END
        decision = sup_agent.evaluate_reviews(state)
        if decision.action == "retry_planner":
            return "planner"
        # Reviews passed: stop at the draft. The Shopper runs only on approval.
        return END

    # --- Build the StateGraph ---
    workflow = StateGraph(PlanState)

    workflow.add_node("supervisor_init", supervisor_init_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("parallel_reviews", parallel_reviews_node)
    workflow.add_node("supervisor_evaluate", supervisor_evaluate_node)

    # Wire Edges
    workflow.add_edge(START, "supervisor_init")
    workflow.add_conditional_edges("supervisor_init", route_after_init)
    workflow.add_edge("planner", "parallel_reviews")
    workflow.add_edge("parallel_reviews", "supervisor_evaluate")
    workflow.add_conditional_edges("supervisor_evaluate", route_after_evaluation)

    return workflow.compile()
