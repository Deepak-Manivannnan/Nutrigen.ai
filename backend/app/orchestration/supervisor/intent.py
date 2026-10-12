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
    dish_to_replace: Optional[str] = Field(None, description="Specific dish name or description to replace")
    instructions: Optional[str] = Field(None, description="Instructions for replacement or modification")
    operation: Optional[str] = Field(None, description="Operation type: 'replace' (default), 'add', or 'remove'")

IntentType = Literal[
    "chat",         # Greetings, pleasantries, polite conversation
    "app_info",     # Questions about NutriGen, how it works, what it can do
    "profile_info", # Inquiries about the user's saved profile details ("tell about my profile", "what information does my profile hold")
    "out_of_scope", # Non-food, non-meal-planning questions (iPhones, Apple CEO, AI, paracetamol, etc.)
    "explain",      # Nutrition / calorie / diet questions
    "new_plan",     # Explicit request to create/plan meals
    "edit_meal",    # User wants to swap/edit/add/remove a meal, or general request to update a plan
    "approve_plan", # User wants to approve a plan and generate its grocery list
    "grocery_only", # User wants only grocery list / mandi prices
    "delete_plan",  # Request to delete a meal plan or confirming plan deletion
    "rename_plan",  # Request to rename a meal plan
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
        description="Ingredients or food categories to exclude (e.g. ['dairy'] if 'dont have dairy foods')."
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
    target_day: Optional[int] = Field(
        None,
        description="Day number targeted for meal edit or snack (e.g. Day 3 -> 3)."
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
            if item_clean in ("dairy foods", "dairy products", "dairy food"):
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


def _build_context_from_history(history: Optional[List[Dict[str, Any]]]) -> str:
    """Build a concise context summary from conversation history for the LLM."""
    if not history:
        return "No prior conversation history."
    
    lines = []
    for h in history[-6:]:  # Last 6 turns
        role = h.get("role", "unknown")
        content = (h.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    
    if not lines:
        return "No prior conversation history."
    
    return "Conversation history (most recent last):\n" + "\n".join(lines)


INTENT_CLASSIFICATION_PROMPT = """You are the Supervisor Intent Classifier for NutriGen, a specialized clinical meal planning assistant.

Analyze the user's message in context of the conversation history and classify the intent into EXACTLY ONE category. Extract all relevant parameters.

---

### DOMAIN BOUNDARY
NutriGen's domain is STRICTLY LIMITED to:
- Meal planning: daily menus, breakfast/lunch/dinner/snack schedules
- Dietary nutrition: calories, macronutrients (protein, carbs, fats), vitamins
- Cooking recipes, food ingredients, culinary preparation
- Food allergies, dietary preferences (vegetarian, vegan, non-veg, keto), ingredient exclusions
- Grocery shopping lists and Mandi wholesale ingredient prices
- User profile parameters and inquiries about NutriGen itself

---

### INTENT CATEGORIES (choose exactly ONE)

1. **chat**: Casual greetings, goodbyes, social pleasantries, polite small talk ("hi", "hello", "good morning", "thanks", "bye")

2. **app_info**: Questions specifically asking what NutriGen does or how its system works ("What can you do?", "How does this work?", "What features do you have?")

3. **profile_info**: User asks about their saved profile data ("Tell me about my profile", "What information does my profile hold?", "Show my profile details")

4. **out_of_scope**: ANY question/request OUTSIDE food/nutrition/meal-planning domain (iPhones, Apple CEO, pharmaceuticals, AI, politics, coding, math, general trivia)

5. **explain**: Questions about dietary nutrition, calories, macros, or food science ("How are my calories calculated?", "Why 1800 kcal?", "What is protein?")

6. **new_plan**: User requests or confirms creating a NEW multi-day meal plan
   - "Plan a 5-day vegetarian meal plan", "Generate meals for 1900 kcal"
   - "Okay go ahead and create it", "Create the plan", "Yes, make it"
   - CRITICAL: "Create it", "go ahead and create" after a proposal = new_plan, NOT approve_plan

7. **edit_meal**: User explicitly asks to MODIFY, SWAP, REPLACE, ADD, or REMOVE a meal dish/slot in an EXISTING plan
   - "Replace day 2 breakfast with oatmeal", "Swap lunch with paneer tikka"
   - "Add a snack to day 3", "Include an evening snack in day 2"
   - "Remove the snack from day 3", "Delete the snack meal on day 2"
   - "Update Good Meal: remove dairy from all days"
   - "I want to update my meal plan" (general, no specifics) → is_update_request = true
   - NEVER select for "delete a meal" or "delete meal" without specific day+slot!

8. **approve_plan**: User wants to APPROVE an already-drafted meal plan to finalize it and compile grocery lists
   - "Approve this plan", "Looks good approve it", "Approve"
   - CRITICAL: "Create it", "go ahead and create" = new_plan, NEVER approve_plan!

9. **grocery_only**: Requesting only grocery lists or mandi commodity prices ("Show grocery list", "What are mandi prices?")

10. **delete_plan**: Asking to delete a meal plan/meal, OR confirming deletion
    - "Delete a meal", "Delete meal plan", "Delete Good Meal", "I want to delete this meal plan"
    - "Yes, delete it", "Confirm deletion" (confirmation)
    - CRITICAL: "delete a meal" without specific day+slot = delete_plan, NEVER edit_meal!

11. **rename_plan**: Requesting to rename a meal plan ("Rename Plan 1 to High Protein", "Rename my meal plan")

---

### PARAMETER EXTRACTION RULES

**requested_days**: 
- "tomorrow" → 1, "for 3 days" → 3, "for a week" → 7, "5-day" → 5
- If not mentioned → default 7 days (configurable, max 14 days)

**requested_calories** (DAILY TOTAL ONLY):
- "1800 kcal", "1900 calories", "2000 cal" → 1800.0, 1900.0, 2000.0
- If message mentions "breakfast/lunch/dinner/snack" with calories → that's SLOT calories, NOT daily → set requested_calories = null
- "per day", "daily" + number → daily calories

**requested_diet_type**: "vegetarian"/"veg" → "veg", "vegan" → "vegan", "non-vegetarian"/"non-veg" → "non-veg"
- Check "non-veg" FIRST to avoid matching "veg" in "non veg"

**requested_exclusions**: Extract ingredients to avoid/exclude
- "no dairy", "without pork", "exclude beef", "dairy-free", "avoid nuts"
- Normalize: "egg" → "eggs"

**diet_conflict_confirmed** + **chosen_diet_type**: 
- True only if user explicitly CONFIRMS/CHOSES diet after a conflict prompt
- "Yes, go with vegetarian" → True, "veg"
- "Use non-veg" → True, "non-veg"
- "Create vegan plan" → True, "vegan"

**fixed_meals**: Explicitly locked meals
- "Fix breakfast as eggs and toast" → [{"meal_type": "breakfast", "dish": "eggs and toast"}]

**edit_details** (only for edit_meal intent):
- day: "day 2" → 2, "on day 3" → 3
- meal_type: "breakfast"/"lunch"/"dinner"/"snack"
- target_meal_calories: SLOT calories only (e.g. "400-500 kcal" → 450.0, "500 kcal" → 500.0)
- instructions: Full user message for context
- dish_to_replace: Specific dish mentioned ("replace paneer tikka")
- operation: "add" (for "add a snack"), "remove" (for "remove the snack"), "replace" (default for "replace/swap/change")

**explain_query**: For explain intent, the specific question

**target_plan_id**: "Plan #9", "Plan 9", "#9" → 9

**target_plan_name**: Plan name in quotes or mentioned
- "delete 'Good Meal'", "update Good Meal" → "Good Meal"
- Check quotes first: "Good Meal", 'Good Plan'

**new_plan_name**: For rename_plan, the new name
- "Rename Plan 1 to High Protein" → "High Protein"

**is_delete_confirmation**: 
- True if user confirms: "yes", "yeah", "confirm", "go ahead", "delete it", "ok"
- Check if previous assistant asked for confirmation (from history)

**is_update_request**: 
- True ONLY if general "update my meal plan" WITHOUT specific meal slot details
- False if specific instructions given (replace, swap, exclusions, etc.)

**target_day**: Day number for edits/snacks
- "for day 3", "on day 2" → 3, 2

---

### CONVERSATION CONTEXT HANDLING

Use history to resolve:
- Implicit references ("create it" → refers to previously proposed plan)
- Missing parameters (inherit days/calories/diet from previous turns)
- Confirmation flows (diet conflict, calorie guardrail, delete confirmation)
- Plan references ("update it" → which plan was discussed?)

---

### CRITICAL DISTINCTIONS

| User says... | Intent | Why |
|--------------|--------|-----|
| "Create it" / "Go ahead and create" | new_plan | Creating NEW plan |
| "Approve it" / "Approve this plan" | approve_plan | Finalizing EXISTING draft |
| "Delete a meal" | delete_plan | No day+slot = delete whole plan |
| "Replace day 2 breakfast" | edit_meal | Specific slot edit (replace) |
| "Add a snack to day 3" | edit_meal + operation=add | Add snack meal |
| "Remove the snack from day 2" | edit_meal + operation=remove | Remove snack meal |
| "I want to update my plan" | edit_meal + is_update_request=true | General, no specifics |
| "Why 1800 kcal?" | explain | Nutrition question |
| "What's my profile?" | profile_info | Profile inquiry |
| "What can you do?" | app_info | Capability question |
| "What is AI?" | out_of_scope | Outside domain |

---

### OUTPUT
Return a UserIntentExtraction object with ALL fields populated. Use null/empty defaults for non-applicable fields.
"""

async def extract_user_intent(
    user_message: str,
    client: Optional[LLMClient] = None,
    history: Optional[List[Dict[str, Any]]] = None
) -> UserIntentExtraction:
    """
    Supervisor semantic intent parser using LLM reasoning.
    Leverages LLM intelligence to classify ANY user message against NutriGen's domain boundaries.
    """
    llm = client or get_llm_client("supervisor")
    context = _build_context_from_history(history)
    
    prompt = f"{INTENT_CLASSIFICATION_PROMPT}\n\n---\n\nConversation Context:\n{context}\n\nCurrent User Message: \"{user_message}\"\n\nClassify the intent and extract all parameters."

    try:
        extraction: UserIntentExtraction = await llm.generate_structured(
            prompt=prompt,
            response_schema=UserIntentExtraction
        )
        # Post-process: convert empty EditDetails to None for consistency
        # Consider empty if all fields are None OR if operation is default 'replace' with no actual edit params
        ed = extraction.edit_details
        if ed and (
            all(v is None for v in [
                ed.day, ed.meal_type, ed.target_meal_calories, ed.dish_to_replace, ed.instructions
            ]) or (ed.operation == "replace" and all(v is None for v in [
                ed.day, ed.meal_type, ed.target_meal_calories, ed.dish_to_replace
            ]))
        ):
            extraction.edit_details = None
        
        logger.debug(f"LLM intent extraction: intent={extraction.intent}, days={extraction.requested_days}, calories={extraction.requested_calories}, diet={extraction.requested_diet_type}")
        return extraction
    except Exception as e:
        logger.warning(f"Error in LLM intent extraction: {e}. Applying safe fallback.")
        # Minimal fallback - let LLM handle it on retry, default to chat
        return UserIntentExtraction(intent="chat")