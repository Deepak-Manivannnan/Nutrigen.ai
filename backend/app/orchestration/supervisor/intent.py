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
    day: Optional[int] = Field(None, description="Day number to edit (e.g. 1, 2, 3)")
    meal_type: Optional[str] = Field(None, description="Meal slot to edit: breakfast, lunch, dinner, or snack")
    target_meal_calories: Optional[float] = Field(None, description="Target calories or calorie range for this specific single meal slot (e.g. 450.0). NOT daily calories!")
    instructions: Optional[str] = Field(None, description="Instructions for replacement or modification")

IntentType = Literal[
    "chat",         # Greetings, pleasantries, polite conversation
    "app_info",     # Questions about NutriGen, how it works, what it can do
    "profile_info", # Inquiries about the user's saved profile details ("tell about my profile", "what information does my profile hold")
    "out_of_scope", # Non-food, non-meal-planning questions (iPhones, Apple CEO, AI, paracetamol, etc.)
    "explain",      # Nutrition / calorie / diet questions
    "new_plan",     # Explicit request to create/plan meals
    "edit_meal",    # User wants to swap/edit a meal, or general request to update a plan
    "approve_plan", # User wants to approve a plan and generate its grocery list
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
        description="Target daily calories requested by user for the ENTIRE DAY (e.g. '1800 kcal' -> 1800.0). MUST be None if calories are for a single meal/slot like breakfast/lunch/dinner/snack."
    )
    requested_diet_type: Optional[str] = Field(
        None,
        description="'veg', 'non-veg', or 'vegan' if explicitly requested in this message (e.g. 'vegetarian plan' -> 'veg')."
    )
    requested_exclusions: List[str] = Field(
        default_factory=list,
        description="Ingredients or food categories to exclude (e.g. ['dairy'] if 'dont have dairy/diary foods')."
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
        description="If intent is edit_meal, specifies day, meal slot, and target slot calories."
    )
    explain_query: Optional[str] = Field(
        None,
        description="If intent is explain, the specific question or topic the user wants explained."
    )
    target_plan_id: Optional[int] = Field(
        None,
        description="Plan ID mentioned by user (e.g., 'Delete Plan 9' -> 9, 'Rename Plan 1' -> 1)."
    )
    target_plan_name: Optional[str] = Field(
        None,
        description="Plan name mentioned by user (e.g., 'Good Meal' in 'delete Good Meal' or 'update Good Meal')."
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

    @field_validator("requested_exclusions", mode="before")
    @classmethod
    def normalize_exclusions(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            v = [v]
        res = []
        for item in v:
            item_clean = str(item).strip().lower()
            if item_clean in ("diary", "dairy foods", "dairy products", "dairy food", "diary foods"):
                res.append("dairy")
            else:
                res.append(item_clean)
        return list(set(res))

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

def _extract_plan_parameters_from_text(text: str) -> Dict[str, Any]:
    """Helper to extract days, calories, diet, and exclusions from any text message."""
    res = {
        "days": None,
        "calories": None,
        "diet_type": None,
        "exclusions": []
    }
    if not text:
        return res
    cl = text.lower()

    # Days extraction
    m_days = re.search(r'\b(?:for\s+)?(\d+)\s*(?:-| )?days?\b', cl)
    if m_days:
        res["days"] = int(m_days.group(1))
    elif "week" in cl:
        res["days"] = 7
    elif "tomorrow" in cl:
        res["days"] = 1

    # Calories extraction (daily calories only, not meal slot)
    if not any(slot in cl for slot in ["breakfast", "lunch", "dinner", "snack"]):
        m_cal = re.search(r'(\d{3,4})\s*(?:kcal|cal|calories|calorle)', cl)
        if m_cal:
            res["calories"] = float(m_cal.group(1))
    else:
        m_day_cal = re.search(r'(\d{3,4})\s*(?:kcal|cal|calories|calorle)(?:\s*/\s*day|\s*per\s*day|\s*daily)?', cl)
        if m_day_cal and ("day" in cl or "daily" in cl):
            res["calories"] = float(m_day_cal.group(1))

    # Diet type
    if any(w in cl for w in ["vegetarian", "veg foods", "veg food", "veg meal", "veg plan", "veg"]) and not any(w in cl for w in ["non-veg", "non-vegetarian"]):
        res["diet_type"] = "veg"
    elif any(w in cl for w in ["non-veg", "non-vegetarian"]):
        res["diet_type"] = "non-veg"
    elif "vegan" in cl:
        res["diet_type"] = "vegan"

    # Exclusions (e.g. dairy / diary)
    if re.search(r'\b(dairy|diary)\b', cl):
        if any(w in cl for w in ["dont", "don't", "no ", "without", "exclude", "avoid", "free"]):
            res["exclusions"].append("dairy")

    return res

def _extract_plan_name_from_text(text: str) -> Optional[str]:
    m_q = re.search(r'["\']([^"\']+)["\']', text)
    if m_q:
        return m_q.group(1).strip()
    clean = text.lower()
    if "good meal" in clean:
        return "Good Meal"
    return None

async def extract_user_intent(
    user_message: str,
    client: Optional[LLMClient] = None,
    history: Optional[List[Dict[str, Any]]] = None
) -> UserIntentExtraction:
    """
    Supervisor semantic intent parser:
    Leverages LLM reasoning and domain rule matching to classify ANY user message against NutriGen's domain boundaries.
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

    # Inspect previous messages in history for multi-turn context
    last_asst_content = ""
    prev_user_content = ""
    if history:
        for h in reversed(history):
            if h.get("role") == "assistant" and not last_asst_content:
                last_asst_content = (h.get("content") or "").lower()
            elif h.get("role") == "user" and not prev_user_content:
                prev_user_content = (h.get("content") or "").strip()

    # 1. Check if previous assistant message asked for Diet Preference Conflict confirmation
    if ("your profile preference is" in last_asst_content and "can i go ahead" in last_asst_content) or \
       ("your profile preference is" in last_asst_content and "create a" in last_asst_content):
        # Extract prior request parameters to preserve days, calories, exclusions
        prev_p = _extract_plan_parameters_from_text(prev_user_content)
        curr_p = _extract_plan_parameters_from_text(user_message)

        inherited_days = curr_p["days"] or prev_p["days"]
        inherited_cal = curr_p["calories"] or prev_p["calories"]
        inherited_excl = list(set(curr_p["exclusions"] + prev_p["exclusions"]))

        if any(w in cleaned for w in ["create vegetarian plan", "veg plan", "go with veg", "vegetarian", "yes, create with veg", "create veg", "no create a vegetarian meal", "create a vegetarian meal", "vegetarian meal"]):
            return UserIntentExtraction(
                intent="new_plan",
                diet_conflict_confirmed=True,
                chosen_diet_type="veg",
                requested_diet_type="veg",
                requested_days=inherited_days,
                requested_calories=inherited_cal,
                requested_exclusions=inherited_excl
            )
        if any(w in cleaned for w in ["use non-vegetarian", "non-veg", "non-vegetarian", "default profile", "use default"]):
            return UserIntentExtraction(
                intent="new_plan",
                diet_conflict_confirmed=True,
                chosen_diet_type="non-veg",
                requested_diet_type="non-veg",
                requested_days=inherited_days,
                requested_calories=inherited_cal,
                requested_exclusions=inherited_excl
            )
        if any(w in cleaned for w in ["vegan", "create vegan"]):
            return UserIntentExtraction(
                intent="new_plan",
                diet_conflict_confirmed=True,
                chosen_diet_type="vegan",
                requested_diet_type="vegan",
                requested_days=inherited_days,
                requested_calories=inherited_cal,
                requested_exclusions=inherited_excl
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
            plan_name = _extract_plan_name_from_text(user_message)
            return UserIntentExtraction(
                intent="delete_plan",
                target_plan_id=target_id,
                target_plan_name=plan_name,
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
        plan_name = _extract_plan_name_from_text(user_message)
        m_name = re.search(r'to\s+["\']?([^"\']+)["\']?$', user_message, re.IGNORECASE)
        new_name = m_name.group(1).strip() if m_name else None
        return UserIntentExtraction(
            intent="rename_plan",
            target_plan_id=target_id,
            target_plan_name=plan_name,
            new_plan_name=new_name
        )

    # Fast rule check: Explicit plan deletion (e.g. "i want to delete good meal plan", "delete meal plan", "delete good meal")
    if ("delete" in cleaned or "remove plan" in cleaned or "clear plan" in cleaned) and any(w in cleaned for w in ["plan", "good meal", "meal plan"]) and not any(w in cleaned for w in ["why", "explain"]):
        target_id = None
        m = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
        if m:
            target_id = int(m.group(1))
        plan_name = _extract_plan_name_from_text(user_message)
        return UserIntentExtraction(
            intent="delete_plan",
            target_plan_id=target_id,
            target_plan_name=plan_name,
            is_delete_confirmation=("yes" in cleaned or "confirm" in cleaned)
        )

    # Fast rule check: Specific meal slot edit / swap / replace / modification
    # e.g. 'I want to update "Good Meal": replace day 2 breakfast with something else and reduce the calorie like 400 to 500 kcal'
    meal_slot_keywords = ["breakfast", "lunch", "dinner", "snack"]
    has_meal_slot = any(k in cleaned for k in meal_slot_keywords) or bool(re.search(r'day\s*\d+', cleaned))
    has_edit_action = any(k in cleaned for k in ["replace", "swap", "change", "modify", "update", "switch", "substitute"])

    if has_meal_slot and has_edit_action and not any(k in cleaned for k in ["create a 3-day", "plan a 3-day", "plan a 7-day", "generate a"]):
        m_day = re.search(r'day\s*(\d+)', cleaned)
        day_val = int(m_day.group(1)) if m_day else None

        m_slot = re.search(r'\b(breakfast|lunch|dinner|snack)\b', cleaned)
        m_type = m_slot.group(1) if m_slot else None

        # Check for slot calorie limits (e.g. "400 to 500 kcal", "400-500 kcal", "500 kcal")
        slot_cal = None
        m_range = re.search(r'(\d+)\s*(?:to|-)\s*(\d+)\s*(?:kcal|cal|calories|calorle)', cleaned)
        if m_range:
            slot_cal = (float(m_range.group(1)) + float(m_range.group(2))) / 2.0
        else:
            m_single = re.search(r'(\d+)\s*(?:kcal|cal|calories|calorle)', cleaned)
            if m_single:
                slot_cal = float(m_single.group(1))

        plan_name = _extract_plan_name_from_text(user_message)
        target_id = None
        m_id = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
        if m_id:
            target_id = int(m_id.group(1))

        # Clean instructions to remove redundant "(Plan #11):" noise
        cleaned_instructions = re.sub(r'\(Plan\s*#\d+\):?', '', user_message, flags=re.IGNORECASE).strip()

        return UserIntentExtraction(
            intent="edit_meal",
            target_plan_id=target_id,
            target_plan_name=plan_name,
            requested_calories=None,  # Slot calorie is NOT a daily target!
            is_update_request=False,  # Specific meal details provided
            edit_details=EditDetails(
                day=day_val,
                meal_type=m_type,
                target_meal_calories=slot_cal,
                instructions=cleaned_instructions
            )
        )

    # Fast rule check: General update / modify request (e.g. "I want to update Good Meal", "update my meal plan")
    general_update_phrases = [
        "i want to update", "i wanted to update", "update my meal plan", "update a meal plan",
        "update the meal plan", "update meal plan", "modify my meal plan", "modify meal plan",
        "update good meal", "modify good meal"
    ]
    if any(p in cleaned for p in general_update_phrases) and not has_meal_slot:
        target_id = None
        m_id = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
        if m_id:
            target_id = int(m_id.group(1))
        plan_name = _extract_plan_name_from_text(user_message)
        return UserIntentExtraction(
            intent="edit_meal",
            target_plan_id=target_id,
            target_plan_name=plan_name,
            is_update_request=True,
            requested_calories=None
        )

    # Fast rule check: Add snack
    if "snack" in cleaned and any(w in cleaned for w in ["add", "include", "put", "want a snack", "have a snack"]):
        return UserIntentExtraction(
            intent="add_snack"
        )

    # Fast rule check: Approve meal plan
    approve_phrases = ["approve this plan", "approve the plan", "approve plan", "approve draft", "approve meal plan", "approve"]
    if any(p == cleaned or cleaned.startswith(p) for p in approve_phrases) or (cleaned.startswith("approve ") and "grocery" not in cleaned):
        plan_name = _extract_plan_name_from_text(user_message)
        m_id = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
        target_id = int(m_id.group(1)) if m_id else None
        return UserIntentExtraction(
            intent="approve_plan",
            target_plan_id=target_id,
            target_plan_name=plan_name
        )

    # Fast rule check for app capabilities / general queries / scope inquiries
    app_info_triggers = [
        "other than", "what else", "besides", "what can you do", "what do you do",
        "how does this work", "other results", "other result", "other info", "other information",
        "can you provide other", "can you show other", "can you do other", "provide other results",
        "provide other", "give other results", "show other results"
    ]
    if any(ph in cleaned for ph in app_info_triggers):
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
        "   - Questions specifically asking what NutriGen does or how its system works, or asking if it can provide other results/capabilities ('What can you do for me?', 'Explain how you work', 'Can you provide other results than this?').\n\n"
        "7. 'explain':\n"
        "   - Questions about dietary nutrition, calories, macros, or food science ('How are my calories calculated?', 'Why 1800 kcal?').\n\n"
        "8. 'new_plan':\n"
        "   - EXPLICIT request to create a NEW multi-day meal plan from scratch ('Plan a 5-day vegetarian meal plan', 'Generate meals for 1900 kcal').\n\n"
        "9. 'edit_meal':\n"
        "   - MUST ONLY be selected when user explicitly asks to modify, swap, replace, or update a meal dish in an existing plan (e.g. 'replace day 2 breakfast with something else', 'swap lunch with paneer', 'I want to update Good Meal'). NEVER select for general questions!\n\n"
        "10. 'approve_plan':\n"
        "   - User wants to approve the drafted meal plan and generate the grocery list (e.g. 'approve this plan', 'looks good approve it', 'approve').\n\n"
        "11. 'grocery_only':\n"
        "   - Requesting only grocery lists or mandi commodity prices.\n\n"
        "12. 'delete_plan':\n"
        "   - Asking to delete a meal plan (e.g. 'delete good meal plan', 'i want to delete this meal plan'), or confirming deletion.\n\n"
        f"User message: \"{user_message}\"\n\n"
        "--- CRITICAL FIELD EXTRACTION RULES ---\n"
        "• MEAL EDITS vs NEW PLANS:\n"
        "  - If user wants to replace/swap/modify a meal slot (e.g. 'replace day 2 breakfast and reduce calories like 400 to 500 kcal'):\n"
        "    * intent is ALWAYS 'edit_meal' (NEVER 'new_plan')!\n"
        "    * requested_calories MUST BE NULL! (requested_calories represents entire daily target, NOT single meal slot).\n"
        "    * edit_details: set day (e.g. 2), meal_type ('breakfast'), target_meal_calories (450.0), and instructions.\n"
        "• target_plan_name: (string name if user mentions a plan by name like 'Good Meal' or in quotes)\n"
        "• target_plan_id: (integer plan id if user explicitly writes '#2' or 'Plan 2')\n"
        "• is_update_request: (true ONLY if user generally asks 'I want to update my meal plan' without specific meal slot details)\n"
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
            plan_name = _extract_plan_name_from_text(user_message)
            return UserIntentExtraction(
                intent="delete_plan",
                target_plan_id=target_id,
                target_plan_name=plan_name,
                is_delete_confirmation=("yes" in cleaned or "confirm" in cleaned)
            )
        # Fall back to 'rename_plan' if rename keywords present
        if "rename" in cleaned:
            target_id = None
            m = re.search(r'(?:plan\s*#?|#)(\d+)', cleaned)
            if m:
                target_id = int(m.group(1))
            plan_name = _extract_plan_name_from_text(user_message)
            return UserIntentExtraction(intent="rename_plan", target_plan_id=target_id, target_plan_name=plan_name)
        # Fall back to 'profile_info'
        if "profile" in cleaned:
            return UserIntentExtraction(intent="profile_info")
        # Fall back to 'edit_meal' if swap/replace/update keywords present
        if any(w in cleaned for w in ["swap", "replace", "change meal", "change breakfast", "change lunch", "change dinner", "edit meal", "edit plan", "update"]):
            plan_name = _extract_plan_name_from_text(user_message)
            return UserIntentExtraction(
                intent="edit_meal",
                target_plan_name=plan_name,
                edit_details=EditDetails(instructions=user_message)
            )
        # Fall back to 'new_plan' ONLY if planning action keywords are present
        if any(w in cleaned for w in ["generate", "plan", "diet", "meal plan", "menu", "schedule meals"]):
            d_type = "veg" if "veg" in cleaned and "non" not in cleaned else ("non-veg" if "non-veg" in cleaned or "chicken" in cleaned or "meat" in cleaned else None)
            return UserIntentExtraction(intent="new_plan", requested_diet_type=d_type)
        return UserIntentExtraction(intent="chat")

