"""
Plan approval: the Supervisor's last two steps, run only when the user approves.

1. The Shopper builds the grocery list for the approved version of the plan.
2. The output guardrail re-checks the whole plan AND that grocery list against
   the user's current allergies and the plan's diet.

Drafting never builds a grocery list (the graph ends at a reviewed draft), so
this is the one place both happen. Both approval paths use it: the Approve
button (api/routes/plans.py) and "approve it" in chat (orchestration/runner.py).
"""
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.schemas.domain import MealPlan, GroceryList
from backend.app.agents.shopper.agent import ShopperAgent
from backend.app.guardrails.output_checks import verify_output_safety

_GUARDRAIL_PREFIX = "Critical safety failure in output guardrail: "


class ApprovalBlocked(Exception):
    """The final safety check failed. `reason` is safe to show the user."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


async def build_and_verify_grocery(
    meal_plan: MealPlan,
    user_allergies: List[str],
    session: AsyncSession,
    task_id: str,
) -> GroceryList:
    """Run the Shopper, then the output guardrail. Returns the grocery list.

    Raises ApprovalBlocked when the final check fails (nothing must be saved),
    and lets any Shopper error propagate unchanged for the caller's existing
    "grocery list could not be generated" handling.
    """
    grocery = await ShopperAgent().generate_grocery_list(meal_plan=meal_plan, task_id=task_id)

    is_valid, warnings, _meta = await verify_output_safety(
        plan=meal_plan,
        grocery_list=grocery,
        user_allergies=user_allergies,
        session=session,
    )
    if not is_valid:
        detail = (warnings[0] if warnings else "the final safety check failed").replace(_GUARDRAIL_PREFIX, "")
        raise ApprovalBlocked(
            f"The final safety check found something that doesn't fit your profile ({detail}). "
            "The plan is still a draft and no grocery list was saved. Ask me in chat to change that meal, then approve again."
        )
    return grocery
