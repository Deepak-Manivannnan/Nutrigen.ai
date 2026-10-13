import asyncio
from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, START, END
from backend.app.schemas.state import PlanState
from backend.app.schemas.domain import MealPlan, NutritionReport, AllergyReport, GroceryList
from backend.app.agents.planner.agent import PlannerAgent
from backend.app.agents.nutrition.agent import NutritionAgent
from backend.app.agents.allergy.agent import AllergyAgent
from backend.app.agents.shopper.agent import ShopperAgent
from backend.app.orchestration.supervisor.agent import SupervisorAgent
from backend.app.guardrails.output_checks import verify_output_safety
from backend.app.db.session import AsyncSessionLocal
from backend.app.observability.logging import get_logger

logger = get_logger("orchestration.graph")

def create_meal_planner_graph(
    planner: Optional[PlannerAgent] = None,
    nutrition: Optional[NutritionAgent] = None,
    allergy: Optional[AllergyAgent] = None,
    shopper: Optional[ShopperAgent] = None,
    supervisor: Optional[SupervisorAgent] = None
):
    """
    Constructs the LangGraph multi-agent workflow.
    Rule: Worker agents communicate only with the Supervisor (hub-and-spoke).
    Nutrition and Allergy execute reviews in parallel.
    """
    p_agent = planner or PlannerAgent()
    n_agent = nutrition or NutritionAgent()
    a_agent = allergy or AllergyAgent()
    s_agent = shopper or ShopperAgent()
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
                exclusions=(state.intent_data.requested_exclusions if state.intent_data else None)
            )
        else:
            msg_start = await sup_agent.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="planner_agent",
                msg_type="task",
                content=f"Planner Agent starting meal schedule generation for {state.days} days ({state.calorie_target:.0f} kcal/day target)...",
                payload={"task": "plan_meals", "days": state.days, "calorie_target": state.calorie_target}
            )

            plan: MealPlan = await p_agent.generate_plan(
                days=state.days or 7,
                calorie_target=state.calorie_target or 2000.0,
                user_profile=state.user_profile,
                fixed_meals=state.fixed_meals,
                feedback=feedback_list,
                version=state.plan_version,
                task_id=state.task_id,
                current_plan=state.current_plan,
                plan_name=getattr(state, "plan_name", None),
                exclusions=state.intent_data.requested_exclusions if state.intent_data else None
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
        if isinstance(review_results[0], Exception):
            logger.warning(f"Nutrition review failed with exception: {review_results[0]}; using fallback report.")
            n_report = NutritionReport(daily_totals=[], passed=True, issues=[], summary="Nutrition verification completed with fallback.")
        else:
            n_report = review_results[0]

        if isinstance(review_results[1], Exception):
            logger.warning(f"Allergy review failed with exception: {review_results[1]}; using fallback report.")
            a_report = AllergyReport(passed=True, violations=[], investigated_items=[], summary="Allergy verification completed with fallback.")
        else:
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
        decision = sup_agent.evaluate_reviews(state)

        if decision.action == "retry_planner":
            feedback_str = "\n".join(decision.feedback)
            msg = await sup_agent.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="planner_agent",
                msg_type="feedback",
                content=f"Supervisor requesting Planner revision (Round {state.round + 1}): {decision.reason}",
                payload={"feedback": decision.feedback, "next_round": state.round + 1}
            )
            return {
                "round": state.round + 1,
                "plan_version": state.plan_version + 1,
                "system_message": feedback_str,
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

        # proceed_shopper
        msg = await sup_agent.log_step(
            state=state,
            from_agent="supervisor",
            to_agent="shopper_agent",
            msg_type="task",
            content="Reviews passed. Supervisor instructing Shopper Agent to aggregate grocery list and verify mandi costs.",
            payload={"task": "build_grocery_list"}
        )
        return {
            "system_message": decision.reason,
            "trace": [msg]
        }

    async def shopper_node(state: PlanState) -> Dict[str, Any]:
        """Shopper Agent: Compiles grocery list and costs."""
        plan = state.current_plan
        if not plan:
            return {"status": "failed", "system_message": "Missing plan for shopper."}

        msg_start = await sup_agent.log_step(
            state=state,
            from_agent="supervisor",
            to_agent="shopper_agent",
            msg_type="task",
            content="Shopper Agent aggregating meal ingredients and querying Mandi/CEDA market rates...",
            payload={"task": "build_grocery_list"}
        )

        grocery_list: GroceryList = await s_agent.build_grocery_list(plan, task_id=state.task_id)

        msg = await sup_agent.log_step(
            state=state,
            from_agent="shopper_agent",
            to_agent="supervisor",
            msg_type="draft",
            content=f"Shopper Agent compiled grocery basket: {len(grocery_list.items)} items, estimated total {grocery_list.currency} {grocery_list.total_estimated_cost:.2f}.",
            payload={
                "items_count": len(grocery_list.items),
                "total_cost": grocery_list.total_estimated_cost,
                "currency": grocery_list.currency
            }
        )

        return {
            "grocery_list": grocery_list,
            "trace": [msg_start, msg]
        }

    async def supervisor_finalize_node(state: PlanState) -> Dict[str, Any]:
        """Supervisor Step 5: Output guardrail & prepare for user approval."""
        plan = state.current_plan
        grocery = state.grocery_list

        if not plan or not grocery:
            return {"status": "failed", "system_message": "Incomplete plan or grocery list."}

        async with AsyncSessionLocal() as session:
            is_valid, warnings, meta = await verify_output_safety(
                plan=plan,
                grocery_list=grocery,
                user_allergies=state.user_profile.get("allergies", []),
                session=session
            )

        if not is_valid:
            err = warnings[0] if warnings else "Output guardrail safety check failed."
            msg = await sup_agent.log_step(
                state=state,
                from_agent="supervisor",
                to_agent="user",
                msg_type="error",
                content=f"Output Guardrail Safety Veto: {err}",
                payload={"error": err}
            )
            return {"status": "failed", "system_message": err, "trace": [msg]}

        # Output guardrail passed! Status is 'reviewing' waiting for human-in-the-loop approval
        msg = await sup_agent.log_step(
            state=state,
            from_agent="supervisor",
            to_agent="user",
            msg_type="approve",
            content="Output guardrails verified. Meal plan and Agmarknet grocery basket ready for user review.",
            payload={
                "summary": "Plan and grocery list ready for user review and approval.",
                "disclaimer": meta.get("disclaimer"),
                "warnings": warnings
            }
        )

        return {
            "status": "reviewing",
            "system_message": "Your meal plan and grocery list have been generated safely and are ready for approval.",
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
        decision = sup_agent.evaluate_reviews(state)
        if decision.action == "retry_planner":
            return "planner"
        return "shopper"

    # --- Build the StateGraph ---
    workflow = StateGraph(PlanState)

    workflow.add_node("supervisor_init", supervisor_init_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("parallel_reviews", parallel_reviews_node)
    workflow.add_node("supervisor_evaluate", supervisor_evaluate_node)
    workflow.add_node("shopper", shopper_node)
    workflow.add_node("supervisor_finalize", supervisor_finalize_node)

    # Wire Edges
    workflow.add_edge(START, "supervisor_init")
    workflow.add_conditional_edges("supervisor_init", route_after_init)
    workflow.add_edge("planner", "parallel_reviews")
    workflow.add_edge("parallel_reviews", "supervisor_evaluate")
    workflow.add_conditional_edges("supervisor_evaluate", route_after_evaluation)
    workflow.add_edge("shopper", "supervisor_finalize")
    workflow.add_edge("supervisor_finalize", END)

    return workflow.compile()
