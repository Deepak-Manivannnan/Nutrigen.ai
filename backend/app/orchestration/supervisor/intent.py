import os
from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field, field_validator
from backend.app.llm.client import llm_client, LLMClient, LLMUnavailableError, get_llm_client
from backend.app.observability.logging import get_logger

logger = get_logger("supervisor.intent")

def _load_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "You are the Supervisor. Orchestrate the multi-agent workflow safely."

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
    edit_scope: Optional[str] = Field(
        None,
        description="Scope of the edit: 'slot' (one day+meal), 'day' (whole day, e.g. 'redo day 2'), or 'plan' (whole plan, e.g. 'replace all recipes', 'remove dairy everywhere'). None when unclear."
    )

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
    "list_plans",   # Request to list/show/view the user's saved meal plans
    "resume_request", # User wants to resume a pending meal planning workflow
    "cancel_request", # User wants to cancel a pending meal planning workflow
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
    requested_cuisine: Optional[str] = Field(
        None,
        description="Regional cuisine explicitly requested (e.g. 'South Indian' -> 'Indian', 'Italian food' -> 'Italian', 'Mediterranean' -> 'Mediterranean'). TheMealDB area names are: American, British, Canadian, Chinese, Croatian, Dutch, Egyptian, Filipino, French, Greek, Indian, Irish, Italian, Jamaican, Japanese, Kenyan, Malaysian, Mexican, Moroccan, Polish, Portuguese, Russian, Spanish, Thai, Tunisian, Turkish, Ukrainian, Uruguayan, Vietnamese. Map to the closest one. None if not mentioned."
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
        default=None,
        description="The chosen diet type if confirming a conflict ('veg', 'non-veg', 'vegan')."
    )
    calorie_guardrail_confirmed: bool = Field(
        default=False,
        description="True if user is confirming or choosing their calorie target after a guardrail prompt."
    )
    chosen_calorie_target: Optional[float] = Field(
        default=None,
        description="The chosen calorie target if confirming a calorie guardrail."
    )
    fixed_meals: List[FixedMealSlot] = Field(
        default_factory=list,
        description="Any meals the user explicitly wants locked (e.g. [{'meal_type': 'breakfast', 'dish': 'eggs and toast'}])."
    )
    edit_details: Optional[EditDetails] = Field(
        None,
        description="If intent is edit_meal, specifies day, meal slot, and target slot calories."
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
    use_profile_defaults: bool = Field(
        default=False,
        description="True if the user agrees to create the plan with their saved profile defaults (e.g. 'yes, use my profile', 'go ahead with my profile preferences', 'create it with my defaults') after the assistant offered that choice. Set intent='new_plan' alongside this flag."
    )
    include_snack: bool = Field(
        default=False,
        description="True if the user explicitly requested to include snacks or snack meals in the meal plan (e.g. 'with snacks', 'including snack meal', 'add snack to meals')."
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
    
    return "Conversation history (most recent last):\n" + "\n".join(lines) + "\n\n"


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

1. **chat**: Casual greetings, goodbyes, social pleasantries, polite small talk ("hi", "hello", "good morning", "thanks", "bye"), AND vague help-seeking WITHOUT an explicit create request ("can you help me create a meal plan?", "I need help with meal planning", "I want to plan my meals", "help me plan my meals", "plan my meals")
   - CRITICAL: If the message EXPLICITLY says create/make/generate/build a (new) (meal) plan — even with zero parameters — it is new_plan, NEVER chat. The chat category is only for messages with NO explicit create request at all.

2. **app_info**: Questions specifically asking what NUTRIGEN's meal-planning features do or how to use them ("What can you do?", "How does this app work?", "What features do you have for meal planning?")
   - NEVER select for questions about AI, AI agents, technology, or how the AI itself works — those are out_of_scope.

3. **profile_info**: User asks about their saved profile data ("Tell me about my profile", "What information does my profile hold?", "Show my profile details", "Look into my profile and tell me what's in it", "What do you know about me/my preferences?", "Show my details")
   - ANY question asking to see, read out, or summarize the user's saved profile/account details is profile_info — NEVER chat, even when phrased vaguely ("look into my profile and tell me").

4. **out_of_scope**: ANY question/request OUTSIDE food/nutrition/meal-planning domain (iPhones, Apple CEO, pharmaceuticals, politics, coding, math, general trivia, capital cities, nature topics like palm trees)
   - Questions about AI, AI agents, artificial intelligence, machine learning, how the AI works, or technology in general are ALWAYS out_of_scope — NEVER app_info or explain, even when phrased as "explain what is an AI agent".

5. **explain**: Questions about FOOD, dietary nutrition, calories, macros, nutrients, or food science ("How are my calories calculated?", "Why 1800 kcal?", "What is protein?", "What is an apple?", "How much protein is in chicken?", "Calories in 2 bananas?", "Is brown rice healthy?")
   - ANY question asking what a FOOD is, what nutrients a food contains, or calories/protein/carbs/fat in a food (with or without an amount) is explain — NEVER out_of_scope. Foods, fruits, vegetables, meats, grains, and ingredients are always in-domain.

6. **new_plan**: User EXPLICITLY requests or confirms creating a NEW multi-day meal plan WITH specific parameters
   - "Plan a 5-day vegetarian meal plan", "Generate meals for 1900 kcal", "Create a 7-day meal plan for 2000 kcal"
   - "Okay go ahead and create it", "Create the plan", "Yes, make it" (after a proposal)
   - An explicit create request with NO parameters yet ("Create a meal plan", "Create a plan for me", "Create a new meal plan for me", "Make me a meal plan") is ALSO new_plan with no params — the application will offer profile defaults vs. custom specs. NEVER chat for explicit "create/make/generate a (new) (meal) plan" requests.
   - Normally has at least one specific parameter (days, calories, diet type) or is an explicit confirmation after a proposal; a bare explicit create request (no params) is still new_plan — specs follow.
   - CRITICAL: "Create it", "go ahead and create" after a proposal = new_plan, NOT approve_plan

7. **edit_meal**: User explicitly asks to MODIFY, SWAP, REPLACE, ADD, or REMOVE a meal dish/slot in an EXISTING plan
   - "Replace day 2 breakfast with oatmeal", "Swap lunch with paneer tikka"
   - "Add a snack to day 3", "Include an evening snack in day 2"
   - "Remove the snack from day 3", "Delete the snack meal on day 2"
   - "Update Good Meal: remove dairy from all days"
   - "I want to update my meal plan" (general, no specifics) -> is_update_request = true
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

12. **list_plans**: User wants to SEE or LIST their saved meal plans ("Show my meal plans", "List my plans", "What plans do I have?", "View my existing plans")
    - NEVER select for create/update/delete/rename requests - only for viewing/listing saved plans

13. **resume_request**: User wants to resume a pending meal planning workflow
    - "Continue creating the plan", "Resume the plan", "Let's continue", "Yes, continue"
    - "I want to continue creating the plan", "Go ahead with the plan"

14. **cancel_request**: User wants to ABANDON a pending meal planning workflow
    - "Cancel the plan", "Stop the plan", "Forget the plan", "Start over", "Start fresh"
    - "Create a new plan INSTEAD", "Start a new plan INSTEAD", "New plan instead"
    - CRITICAL: the user must signal abandonment - "instead", "start over", "forget it", "cancel", "stop". A plain request to build a plan ("can you create a meal plan for me", "make me a 5 day plan") is NOT cancel_request, even when the conversation history shows something else in progress. It is new_plan: the application itself asks whether to continue the open work or start fresh, so classifying it as cancel_request would silently throw away work the user never abandoned.
      | "Can you please create a meal plan for me" (while something else is in progress) | new_plan | A plain create request - the app offers continue-or-restart |
      | "Create a new plan instead" | cancel_request | "instead" abandons the pending work |

---

### PARAMETER EXTRACTION RULES

**requested_days**: 
- "tomorrow" -> 1, "for 3 days" -> 3, "for a week" -> 7, "5-day" -> 5
- If not mentioned -> default 7 days (configurable, max 14 days)

**requested_calories** (DAILY TOTAL ONLY):
- "1800 kcal", "1900 calories", "2000 cal" -> 1800.0, 1900.0, 2000.0
- If message mentions "breakfast/lunch/dinner/snack" with calories -> that's SLOT calories, NOT daily -> set requested_calories = null
- "per day", "daily" + number -> daily calories

**requested_diet_type**: "vegetarian"/"veg" -> "veg", "vegan" -> "vegan", "non-vegetarian"/"non-veg" -> "non-veg"
- Check "non-veg" FIRST to avoid matching "veg" in "non veg"

**requested_exclusions**: Extract ingredients to avoid/exclude
- "no dairy", "without pork", "exclude beef", "dairy-free", "avoid nuts"
- "replace all beef dishes with chicken", "swap dishes containing pork" -> ["beef"], ["pork"]
- Normalize: "egg" -> "eggs"

**diet_conflict_confirmed** + **chosen_diet_type**: 
- True only if user explicitly CONFIRMS/CHOSES diet after a conflict prompt
- "Yes, go with vegetarian" -> True, "veg"
- "Use non-veg" -> True, "non-veg"
- "Create vegan plan" -> True, "vegan"

**calorie_guardrail_confirmed** + **chosen_calorie_target**: 
- True only if user explicitly CONFIRMS/CHOSES calorie target after a guardrail prompt
- "Yes, 2100 kcal" -> True, 2100.0
- "Go with 2100" -> True, 2100.0
- "Use recommended" -> True, <suggested_value>

**fixed_meals**: Explicitly locked meals
- "Fix breakfast as eggs and toast" -> [{"meal_type": "breakfast", "dish": "eggs and toast"}]

**edit_details** (only for edit_meal intent):
- day: "day 2" -> 2, "on day 3" -> 3
- meal_type: "breakfast"/"lunch"/"dinner"/"snack"
- target_meal_calories: SLOT calories only (e.g. "400-500 kcal" -> 450.0, "500 kcal" -> 500.0)
- instructions: Full user message for context
- dish_to_replace: Specific dish mentioned ("replace paneer tikka"). For ingredient swaps ("replace all beef dishes with chicken", "swap out the pork"), ALWAYS set this to the ingredient/dish being removed ("beef dishes", "pork") — never leave it null when the message names what goes away.
- operation: "add" (for "add a snack"), "remove" (for "remove the snack"), "replace" (default for "replace/swap/change")
- edit_scope: "slot" (one specific day+meal), "day" ("redo day 2", "change all of day 3"), "plan" ("replace all recipes", "remove dairy from every day", whole-plan changes), null when unclear or still being clarified
  - CRITICAL — only widen the scope when the message really covers EVERY meal it names:
    - Use "plan" ONLY when the message explicitly covers the WHOLE plan ("all days", "every day", "everywhere", "the whole plan", "all my meals", "replace all recipes"). Naming a single meal slot ("swap dinner", "change breakfast", "replace lunch") is NOT plan scope, even though it may repeat across days.
    - Use "day" ONLY when the message covers every meal of that one day ("redo day 2", "change all of day 3", "rebuild day 2"). "Update day 2 with a different meal" names ONE meal, so that is "slot" scope with day set.
  - When a scope is widened but the message names no day or no slot, keep day/meal_type null so the app can ask which one. Silently regenerating a whole day or the whole plan is the one outcome you must never produce.

**target_plan_id**: "Plan #9", "Plan 9", "#9" -> 9

**target_plan_name**: Plan name in quotes or mentioned
- "delete 'Good Meal'", "update Good Meal" -> "Good Meal"
- Check quotes first: "Good Meal", 'Good Plan'

**new_plan_name**: For rename_plan, the new name
- "Rename Plan 1 to High Protein" -> "High Protein"

**is_delete_confirmation**: 
- True if user confirms: "yes", "yeah", "confirm", "go ahead", "delete it", "ok"
- Check if previous assistant asked for confirmation (from history)

**is_update_request**: 
- True ONLY if general "update my meal plan" WITHOUT specific meal slot details
- False if specific instructions given (replace, swap, exclusions, etc.)

**include_snack**: 
- True if the user explicitly asks for snacks or snack meals when creating a plan (e.g. "with snack", "with snack meal", "include snacks", "plan with snack meals", "create a meal plan with snack meal also").
- False if snacks are not explicitly requested.

---

### CONVERSATION CONTEXT HANDLING

Use history to resolve:
- Implicit references ("create it" -> refers to previously proposed plan)
- Missing parameters (inherit days/calories/diet from previous turns)
- Confirmation flows (diet conflict, calorie guardrail, delete confirmation)
- Plan references ("update it" -> which plan was discussed?)
- **Pending workflow context**: If there's a pending workflow (diet conflict, calorie guardrail, edit confirmation pending), the user's response may be an answer to the pending question. Check if the user is answering a pending question, requesting to resume, or canceling the workflow.

### PENDING WORKFLOW RESPONSE HANDLING

When PENDING WORKFLOW CONTEXT is provided, the user's message may be a PARTIAL answer to a pending question. Handle as follows:

1. **If pending_conflicts includes "diet_conflict" and user expresses a diet preference** (e.g., "I prefer vegetarian", "I want vegetarian", "go with veg"):
   - Set intent = "new_plan" (or original_intent)
   - Set diet_conflict_confirmed = true
   - Set chosen_diet_type to the expressed preference ("veg", "non-veg", or "vegan")
   - Do NOT set requested_days or requested_calories unless explicitly mentioned
   - The calorie question remains pending

2. **If pending_conflicts includes "calorie_guardrail" and user specifies a calorie target** (e.g., "2100 kcal", "go with 2100"):
   - Set intent = "new_plan" (or original_intent)
   - Set requested_calories to the specified value
   - Set calorie_guardrail_confirmed = true
   - Set chosen_calorie_target to the specified value
   - The diet conflict (if any) may still be pending

3. **If user says "resume", "continue", "go ahead", "proceed" with pending workflow**:
   - Set intent = "resume_request"

4. **If user says "cancel", "stop", "no", "keep it", "don't delete", "new plan instead" with ANY pending workflow**
   (diet conflict, calorie guardrail, edit clarification, or delete confirmation):
   - Set intent = "cancel_request"
   - A lone "Cancel" message ALWAYS cancels whatever is pending — NEVER classify it as chat.

5. **If user provides a COMPLETE answer resolving ALL pending conflicts** (specifies both diet AND calories):
   - Set intent = "new_plan" (or original_intent)
   - Set all relevant confirmation flags
   - Clear pending workflow

6. **Fresh new-request override**: If the message reads as a NEW, ANOTHER, or DIFFERENT plan request — a self-contained
   fresh specification (diet and/or calories and/or days stated together as one request, e.g. "create a new veg meal
   plan for 2000 kcal for 5 days") rather than a reply to the pending question(s) — do NOT set
   diet_conflict_confirmed / calorie_guardrail_confirmed / chosen_diet_type / chosen_calorie_target. Set ONLY the
   requested_* fields with intent = "new_plan". When in doubt, treat SHORT direct replies ("I prefer vegetarian",
   "veg", "2100", "yes, 2100", "go with the recommended", "use my profile diet") as answers (rules 1-2), and treat
   longer self-contained specs — especially with new/another/different/start-over/instead language — as fresh requests.

7. **Never sideline an answer**: When pending_conflicts is non-empty and the message states a diet preference or a
   calorie number, that is an answer to the pending question (rules 1-2). NEVER classify such messages as
   chat/app_info/explain.

8. **Profile-defaults agreement**: If the assistant just offered to create the plan with the user's saved profile
   defaults (check the conversation history for that offer) and the user agrees — "yes", "go ahead", "use my profile",
   "create it with my defaults", "sounds good, do it" — set intent = "new_plan" AND use_profile_defaults = true.
   Do NOT set requested_* params in this case; the application fills them from the profile.

CRITICAL: When user answers a pending question PARTIALLY (e.g., only answers diet preference but not calories), the intent should still be the original intent (e.g., "new_plan") with the appropriate confirmation flags set. Do NOT classify as "chat" or "app_info".

---


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
| "What is an apple?" / "Protein in chicken?" | explain | Food question — always answer, never out_of_scope |
| "What's my profile?" / "Look into my profile and tell me" | profile_info | Profile inquiry, even when vague |
| "What can you do?" | app_info | Capability question |
| "What is AI?" / "Explain what is an AI agent?" | out_of_scope | AI/tech — never app_info or explain |
| "Continue the plan" / "Resume" | resume_request | Resume pending workflow |
| "Cancel the plan" / "New plan instead" / "Create a new plan instead" / "Start over" | cancel_request | Cancel pending workflow |
| "Show my meal plans" / "List my plans" | list_plans | View saved plans |
| "Create a meal plan" / "Create a plan for me" (no params) | new_plan | Bare create → profile-defaults offer |
| "Yes, use my profile" / "Go ahead with my profile" (after defaults offer) | new_plan + use_profile_defaults=true | Create with profile defaults |

---

### OUTPUT
Return a UserIntentExtraction object with ALL fields populated. Use null/empty defaults for non-applicable fields.
"""

async def extract_user_intent(
    user_message: str,
    client: Optional[LLMClient] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    pending_workflow: Optional[Any] = None
) -> UserIntentExtraction:
    """
    Supervisor semantic intent parser using LLM reasoning.
    Leverages LLM intelligence to classify ANY user message against NutriGen's domain boundaries.
    """
    llm = client or get_llm_client("supervisor")
    context = _build_context_from_history(history)
    
    # Include pending workflow context if available
    workflow_context = ""
    if pending_workflow:
        workflow_context = f"""
PENDING WORKFLOW CONTEXT:
- Workflow Type: {pending_workflow.workflow_type}
- Pending Conflicts: {', '.join(pending_workflow.pending_conflicts) if pending_workflow.pending_conflicts else 'None'}
- Questions Asked: {', '.join(pending_workflow.asked_questions) if pending_workflow.asked_questions else 'None'}
- Next Expected Decision: {pending_workflow.next_expected_decision or 'None'}
- Original Intent: {pending_workflow.original_intent or 'None'}
- Original Message: {pending_workflow.original_message or 'None'}
- Extracted Info So Far: {pending_workflow.extracted_info}
"""
        context = context + workflow_context
    
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
    except LLMUnavailableError:
        # Backend outage, not a misunderstanding: propagate so the API returns
        # an honest service-trouble message instead of a canned fallback reply.
        raise
    except Exception as e:
        logger.warning(f"Error in LLM intent extraction: {e}. Applying safe fallback.")
        # Minimal fallback - let LLM handle it on retry, default to chat
        return UserIntentExtraction(intent="chat")