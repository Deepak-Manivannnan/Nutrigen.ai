import re
from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field, field_validator
from backend.app.llm.client import llm_client, LLMClient, get_llm_client
from backend.app.observability.logging import get_logger

logger = get_logger("supervisor.intent")

class FixedMealSlot(BaseModel):
    meal_type: str = Field(..., description="Meal slot: breakfast, lunch, or dinner")
    dish: str = Field(..., description="Dish description or name")

class EditDetails(BaseModel):
    day: Optional[int] = Field(None, description="Day number to edit")
    meal_type: Optional[str] = Field(None, description="Meal slot to edit")
    instructions: Optional[str] = Field(None, description="Instructions for replacement")

IntentType = Literal[
    "chat",         # Greetings, pleasantries, polite conversation
    "app_info",     # Questions about NutriGen, how it works, what it can do
    "profile_info", # Inquiries about the user's saved profile details ("tell about my profile", "what information does my profile hold")
    "out_of_scope", # Non-food, non-meal-planning questions (iPhones, Apple CEO, AI, paracetamol, etc.)
    "explain",      # Nutrition / calorie / diet questions
    "new_plan",     # Explicit request to create/plan meals
    "edit_meal",    # User wants to swap/edit a meal, or general request to update a plan
    "grocery_only", # User wants only grocery list / mandi prices
    "delete_plan",  # Request to delete a meal plan or confirming plan deletion
    "rename_plan",  # Request to rename a meal plan
    "add_snack"     # Request to add a snack to a meal plan
]

class UserIntentExtraction(BaseModel):
    intent: IntentType = Field(
        default="chat",
        description="The primary intent of the user message. Defaults to 'chat' for greetings/general talk."
    )
    requested_days: Optional[int] = Field(
        None,
        description="Number of days to plan for (e.g. 'tomorrow' -> 1, 'for 3 days' -> 3, 'for a week' -> 7). Return None if not specified."
    )
    requested_calories: Optional[float] = Field(
        None,
        description="Target daily calories requested by user (e.g. '1800 kcal' -> 1800.0). Return None if not specified."
    )
    requested_diet_type: Optional[str] = Field(
        None,
        description="'veg', 'non-veg', or 'vegan' if explicitly requested in this message (e.g. 'vegetarian plan' -> 'veg')."
    )
    diet_conflict_confirmed: bool = Field(
        default=False,
        description="True if user is confirming or choosing their diet preference after a conflict prompt."
    )
    chosen_diet_type: Optional[str] = Field(
        None,
        description="The chosen diet type if confirming a conflict ('veg', 'non-veg', 'vegan')."
    )
    fixed_meals: List[FixedMealSlot] = Field(
        default_factory=list,
        description="Any meals the user explicitly wants locked (e.g. [{'meal_type': 'breakfast', 'dish': 'eggs and toast'}])."
    )
    edit_details: Optional[EditDetails] = Field(
        None,
        description="If intent is edit_meal, specifies day and meal slot."
    )
    explain_query: Optional[str] = Field(
        None,
        description="If intent is explain, the specific question or topic the user wants explained."
    )
    target_plan_id: Optional[int] = Field(
        None,
        description="Plan ID mentioned by user (e.g., 'Delete Plan 9' -> 9, 'Rename Plan 1' -> 1)."
    )
    new_plan_name: Optional[str] = Field(
        None,
        description="New name if intent is rename_plan (e.g. 'Rename Plan 1 to High Protein' -> 'High Protein')."
    )
    is_delete_confirmation: bool = Field(
        default=False,
        description="True if user is confirming deletion (e.g. 'yes, delete it', 'yes delete plan 9', 'yes', 'confirm', 'go ahead')."
    )
    is_update_request: bool = Field(
        default=False,
        description="True if user generally asks 'I want to update my meal plan' without specific meal edit details."
    )
    snack_rebalance_choice: Optional[str] = Field(
        None,
        description="'low_calorie' or 'rebalance' if user answered snack choice prompt."
    )

    @field_validator("fixed_meals", mode="before")
    @classmethod
    def coerce_fixed_meals(cls, v):
        if isinstance(v, dict):
            return [FixedMealSlot(meal_type=str(k), dish=str(val)) for k, val in v.items()]
        return v

# Fast single-word greeting set for sub-millisecond shortcut
FAST_GREETINGS = {
    "hi", "hello", "hey", "hola", "namaste", "good morning", "good afternoon",
    "good evening", "howdy", "sup", "yo", "hi there", "hello there"
}

async def extract_user_intent(
    user_message: str,
    client: Optional[LLMClient] = None,
    history: Optional[List[Dict[str, Any]]] = None
) -> UserIntentExtraction:
    """
    Supervisor semantic intent parser:
    Leverages LLM reasoning to classify ANY user message against NutriGen's domain boundaries.
    """
    cleaned = user_message.strip().lower()

    # Fast shortcut only for unambiguous standalone greetings
    if cleaned in FAST_GREETINGS:
        return UserIntentExtraction(intent="chat")

    # Fast check: Profile inquiries ("tell about my profile", "what information does my profile hold/contain")
    profile_inquiry_phrases = [
        "tell about my profile", "tell me about my profile", "about my profile",
        "what information does my profile hold", "what information does my profile contain",
        "what information does my profile have", "what does my profile contain",
        "what does my profile hold", "show my profile", "view my profile",
        "what details does my profile hold", "what details does my profile contain",
        "my profile details", "my profile information", "what is in my profile",
        "what kind of information does my profile", "what kind of info does my profile"
    ]
    if any(p in cleaned for p in profile_inquiry_phrases):
        return UserIntentExtraction(intent="profile_info")

    # Inspect previous assistant message in history
    last_asst_content = ""
    if history:
        for h in reversed(history):
            if h.get("role") == "assistant":
                last_asst_content = (h.get("content") or "").lower()
                break

    # 1. Check if previous assistant message asked for Diet Preference Conflict confirmation
    if "your profile preference is" in last_asst_content and "can i go ahead" in last_asst_content:
        # User is answering diet conflict prompt
        if any(w in cleaned for w in ["create vegetarian plan", "veg plan", "go with veg", "vegetarian", "yes, create with veg", "create veg"]):
            return UserIntentExtraction(
                intent="new_plan",
                diet_conflict_confirmed=True,
                chosen_diet_type="veg",
                requested_diet_type="veg"
            )
        if any(w in cleaned for w in ["use non-vegetarian", "non-veg", "non-vegetarian", "default profile", "use default"]):
            return UserIntentExtraction(
                intent="new_plan",
                diet_conflict_confirmed=True,
                chosen_diet_type="non-veg",
                requested_diet_type="non-veg"
            )
        if any(w in cleaned for w in ["vegan", "create vegan"]):
            return UserIntentExtraction(
                intent="new_plan",
                diet_conflict_confirmed=True,
                chosen_diet_type="vegan",
                requested_diet_type="vegan"
            )

    # 2. Check if previous assistant message asked for Snack rebalance options
    if "adding a snack may exceed your daily calorie target" in last_asst_content:
        if any(w in cleaned for w in ["low-calorie", "low calorie", "add low calorie", "add low-calorie"]):
            return UserIntentExtraction(intent="add_snack", snack_rebalance_choice="low_calorie")
        if any(w in cleaned for w in ["rebalance", "rebalance all 4 meals", "all 4 meals"]):
            return UserIntentExtraction(intent="add_snack", snack_rebalance_choice="rebalance")

    # 3. Check if previous assistant message asked for Plan Deletion confirmation
    is_pending_delete = (
        "are you sure you want to delete" in last_asst_content
        or "can i delete it" in last_asst_content
        or "which meal plan should be deleted" in last_asst_content
        or "reply 'yes, delete it'" in last_asst_content
        or "reply \"yes, delete it\"" in last_asst_content
    )
    if is_pending_delete:
        if any(w in cleaned for w in ["yes", "yeah", "yep", "sure", "confirm", "proceed", "go ahead", "delete it", "ok", "okay"]):
            target_id = None
            m = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
            if m:
                target_id = int(m.group(1))
            return UserIntentExtraction(
                intent="delete_plan",
                target_plan_id=target_id,
                is_delete_confirmation=True
            )
        if any(w in cleaned for w in ["no", "cancel", "don't", "keep it", "stop"]):
            return UserIntentExtraction(
                intent="delete_plan",
                is_delete_confirmation=False
            )

    # Fast rule check: Rename plan
    if "rename" in cleaned and ("plan" in cleaned or "#" in cleaned):
        target_id = None
        m_id = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
        if m_id:
            target_id = int(m_id.group(1))
        # Extract new name after "to" (e.g. "rename plan 1 to Healthy Feast")
        m_name = re.search(r'to\s+["\']?([^"\']+)["\']?$', user_message, re.IGNORECASE)
        new_name = m_name.group(1).strip() if m_name else None
        return UserIntentExtraction(
            intent="rename_plan",
            target_plan_id=target_id,
            new_plan_name=new_name
        )

    # Fast rule check: General update / modify request
    general_update_phrases = [
        "i want to update my meal plan", "i want to update a meal plan", "i want to update a meal",
        "i want to update the meal plan", "i want to update this meal plan", "i want to update meal plan",
        "update my meal plan", "update a meal plan", "update meal plan", "update a meal",
        "i want to modify my plan", "modify my meal plan", "modify a meal plan"
    ]
    if any(cleaned == p or cleaned.startswith(p) for p in general_update_phrases) and not any(w in cleaned for w in ["breakfast", "lunch", "dinner", "snack", "swap", "replace"]):
        target_id = None
        m_id = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
        if m_id:
            target_id = int(m_id.group(1))
        return UserIntentExtraction(
            intent="edit_meal",
            target_plan_id=target_id,
            is_update_request=True
        )

    # Fast rule check: Explicit plan deletion phrases
    delete_keywords = ["delete plan", "delete this meal plan", "delete meal plan", "remove plan", "remove meal plan", "clear plan", "cancel plan", "delete my plan", "delete the plan"]
    if any(dk in cleaned for dk in delete_keywords) or (cleaned.startswith("delete") and ("plan" in cleaned or "#" in cleaned)):
        target_id = None
        m = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
        if m:
            target_id = int(m.group(1))
        return UserIntentExtraction(
            intent="delete_plan",
            target_plan_id=target_id,
            is_delete_confirmation=("yes" in cleaned or "confirm" in cleaned)
        )

    # Fast rule check: Add snack
    if "snack" in cleaned and any(w in cleaned for w in ["add", "include", "put", "want a snack", "have a snack"]):
        return UserIntentExtraction(
            intent="add_snack",
            snack_details=user_message
        )

    # Fast rule check for app capabilities / general queries
    if any(ph in cleaned for ph in ["other than", "what else", "besides", "what can you do", "what do you do", "how does this work"]):
        return UserIntentExtraction(intent="app_info")

    llm = client or get_llm_client("supervisor")
    prompt = (
        "You are the Supervisor intent classifier for NutriGen, a specialized clinical meal planning assistant.\n\n"
        "Analyze the user message and classify its intent into EXACTLY ONE category.\n\n"
        "--- DOMAIN BOUNDARY DEFINITION ---\n"
        "NutriGen's domain is STRICTLY LIMITED to:\n"
        "• Meal planning, daily menus, breakfast/lunch/dinner/snack schedules.\n"
        "• Dietary nutrition, calories, macronutrients (protein, carbs, fats), vitamins.\n"
        "• Cooking recipes, food ingredients, and culinary preparation.\n"
        "• Food allergies, dietary preferences (vegetarian, vegan, non-veg, keto), ingredient exclusions.\n"
        "• Grocery shopping lists and Mandi wholesale ingredient prices.\n"
        "• User profile parameters and Inquiries about NutriGen itself.\n\n"
        "--- INTENT CATEGORIES ---\n"
        "1. 'profile_info':\n"
        "   - The user asks about what data/details are in their user profile (e.g. 'Tell about my profile', 'What information does my profile contain/hold?', 'Show my profile details').\n\n"
        "2. 'rename_plan':\n"
        "   - Requesting to rename a meal plan (e.g. 'Rename Plan 1 to High Protein', 'Rename my meal plan to Weekend Veg Feast').\n\n"
        "3. 'add_snack':\n"
        "   - Asking to add or include a snack meal in a plan (e.g. 'Add a snack to day 3', 'Can I have an evening snack?').\n\n"
        "4. 'out_of_scope':\n"
        "   - ANY question or request that falls OUTSIDE the food/nutrition/meal-planning domain.\n"
        "   - Examples: iPhones, Apple CEO, pharmaceuticals/paracetamol, AI, politics, coding, math, general trivia.\n\n"
        "5. 'chat':\n"
        "   - Casual greetings, goodbyes, social pleasantries, or polite small talk ('hi', 'hello', 'good morning', 'thanks').\n\n"
        "6. 'app_info':\n"
        "   - Questions specifically asking what NutriGen does or how its system works ('What can you do for me?', 'Explain how you work').\n\n"
        "7. 'explain':\n"
        "   - Questions about dietary nutrition, calories, macros, or food science ('How are my calories calculated?', 'Why 1800 kcal?').\n\n"
        "8. 'new_plan':\n"
        "   - EXPLICIT request to create or plan meals ('Plan a 5-day vegetarian meal plan', 'Generate meals for 1900 kcal').\n\n"
        "9. 'edit_meal':\n"
        "   - Modifying/swapping dishes in a plan, or general 'I want to update my meal plan'.\n\n"
        "10. 'grocery_only':\n"
        "   - Requesting only grocery lists or mandi commodity prices.\n\n"
        "11. 'delete_plan':\n"
        "   - Asking to delete a meal plan, or confirming deletion.\n\n"
        f"User message: \"{user_message}\"\n\n"
        "--- FIELD EXTRACTION RULES ---\n"
        "• requested_days: (integer days e.g. 1, 3, 5, 7 or null)\n"
        "• requested_calories: (float calories e.g. 1700.0 or null)\n"
        "• requested_diet_type: ('veg', 'non-veg', or 'vegan' if mentioned e.g. 'veg meal plan' -> 'veg')\n"
        "• target_plan_id: (integer plan id if mentioned e.g. 'Plan #2' -> 2 or null)\n"
        "• new_plan_name: (new name if renaming e.g. 'Rename Plan 1 to Keto Delight' -> 'Keto Delight')\n"
        "• is_update_request: (true if user generally asks 'I want to update my meal plan' without specific meal details)\n"
        "• is_delete_confirmation: (true if user says 'yes', 'confirm', 'yes delete it')\n"
    )

    try:
        extraction: UserIntentExtraction = await llm.generate_structured(
            prompt=prompt,
            response_schema=UserIntentExtraction
        )
        return extraction
    except Exception as e:
        logger.warning(f"Error in LLM intent extraction: {e}. Applying safe fallback.")
        # Fall back to 'delete_plan' if deletion keywords present
        if any(w in cleaned for w in ["delete", "remove plan", "clear plan", "cancel plan"]):
            target_id = None
            m = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
            if m:
                target_id = int(m.group(1))
            return UserIntentExtraction(
                intent="delete_plan",
                target_plan_id=target_id,
                is_delete_confirmation=("yes" in cleaned or "confirm" in cleaned)
            )
        # Fall back to 'rename_plan' if rename keywords present
        if "rename" in cleaned:
            target_id = None
            m = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
            if m:
                target_id = int(m.group(1))
            return UserIntentExtraction(intent="rename_plan", target_plan_id=target_id)
        # Fall back to 'profile_info'
        if "profile" in cleaned:
            return UserIntentExtraction(intent="profile_info")
        # Fall back to 'edit_meal' if swap/replace/update keywords present
        if any(w in cleaned for w in ["swap", "replace", "change meal", "change breakfast", "change lunch", "change dinner", "edit meal", "edit plan", "update"]):
            return UserIntentExtraction(
                intent="edit_meal",
                edit_details=EditDetails(instructions=user_message)
            )
        # Fall back to 'new_plan' ONLY if planning action keywords are present
        if any(w in cleaned for w in ["generate", "plan", "diet", "meal plan", "menu", "schedule meals"]):
            # Extract diet type if present
            d_type = "veg" if "veg" in cleaned and "non" not in cleaned else ("non-veg" if "non-veg" in cleaned or "chicken" in cleaned or "meat" in cleaned else None)
            return UserIntentExtraction(intent="new_plan", requested_diet_type=d_type)
        return UserIntentExtraction(intent="chat")
