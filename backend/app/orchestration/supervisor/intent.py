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
    "unclear",      # The message cannot be understood (random characters, a lone dot, an accidental send, a typo that changes the meaning)
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
        description="The cuisine the user asked the meals to follow, kept as they described it, including regional detail (e.g. 'South Indian', 'Punjabi', 'Kerala', 'Italian', 'Mediterranean', 'Thai'). Do NOT generalise it ('South Indian' stays 'South Indian', not 'Indian'). None if no cuisine is mentioned."
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
        description="ID of the saved plan the user is referring to, taken from the SAVED PLANS list (by name, number, length, diet, or a reference like 'it'/'that one'). Null when no specific plan is pointed at or it is ambiguous."
    )
    target_plan_name: Optional[str] = Field(
        None,
        description="Name of a specific plan the user named (e.g. 'Good Meal' in 'delete Good Meal'). Null for generic references like 'my plan' or 'it'."
    )
    new_plan_name: Optional[str] = Field(
        None,
        description="New name if intent is rename_plan (e.g. 'Rename Plan 1 to High Protein' -> 'High Protein')."
    )
    is_delete_confirmation: bool = Field(
        default=False,
        description="True if user is confirming deletion (e.g. 'yes, delete it', 'yes delete plan 9', 'yes', 'confirm', 'go ahead'). Only meaningful when a delete confirmation is pending."
    )
    abandon_pending_work: bool = Field(
        default=False,
        description="True ONLY when the user explicitly tells you to drop, replace or restart the work in progress ('forget it', 'start over', 'cancel', 'never mind', 'do a new plan instead'). False for a plain new request that does not say to abandon the pending work."
    )
    defer_pending: bool = Field(
        default=False,
        description="True when a question is pending and the user wants to pause it and come back later WITHOUT refusing it ('let me think about it', 'not now, maybe later', 'let me cook first', 'give me a minute', 'I'll decide later'). Then intent must NOT be cancel_request and abandon_pending_work is false."
    )
    save_preference_answer: Optional[bool] = Field(
        None,
        description="Only when a 'save_dislikes' question is pending (the assistant asked whether to remember foods as dislikes): true if the user says yes, false if they say no / only for this plan, null if the message is not an answer to that question."
    )
    unclear_reason: Optional[str] = Field(
        None,
        description="Only when intent is 'unclear': one short sentence on why it could not be understood, and your best guess of what the user may have meant if you have one (e.g. 'only a full stop' or 'looks like a typo of \"yes\"')."
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

    style_notes: List[str] = Field(
        default_factory=list,
        description="Qualities the user wants in the food, as short phrases: 'high protein', 'light', 'spicy', 'quick to cook', 'low carb'. NOT dishes, NOT ingredients, NOT foods to exclude."
    )
    main_ingredients: List[str] = Field(
        default_factory=list,
        description="Foods the user says they have, or want the meals built around ('chicken', 'tomato'), as singular lower-case words. NEVER foods they want excluded."
    )
    ingredients_only: bool = Field(
        default=False,
        description="True only when the user says these are ALL the foods they have / the only foods to use ('I have only chicken and tomatoes'). False when they merely want some foods included."
    )
    unsupported_reason: Optional[str] = Field(
        None,
        description="Set when the request needs something this app cannot do (see 'WHAT THIS APP CAN AND CANNOT DO'): one short sentence on what is being asked, e.g. 'only dinners, not whole days'. Keep intent='new_plan' and still fill every other field. Null for ordinary requests."
    )

    @field_validator("main_ingredients", "style_notes", mode="before")
    @classmethod
    def normalize_word_lists(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            v = [v]
        seen, out = set(), []
        for item in v:
            clean = str(item).strip().lower()
            if clean and clean not in seen:
                seen.add(clean)
                out.append(clean)
        return out

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

15. **unclear**: The message cannot be understood well enough to act on or to answer sensibly.
    - A lone dot or punctuation ("." , "?", "...") , random characters ("asdfgh", "kjhsd 83jd"), a string of digits or a mix of digits and letters with no meaning ("48392", "x7q2"), an accidental send, or a word so garbled that you cannot tell what was meant.
    - Ordinary spelling mistakes and small typos do NOT make a message unclear. Read through them the way a person would ("creat a mael plan for 3 dys" is new_plan).
    - A bare number is NOT unclear when it answers a pending question (a calorie target, a day count) - use the pending workflow context.
    - Never guess an action for an unclear message and never pick edit/delete/approve for it. Set unclear_reason to a short note, including your best guess of what they may have meant if you have one.

---

### PARAMETER EXTRACTION RULES

**requested_days**: 
- "tomorrow" -> 1, "for 3 days" -> 3, "for a week" -> 7, "5-day" -> 5
- If the user does not say how many days -> null. NEVER assume a number: the application asks for it. 'a week' / 'the whole week' -> 7.

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

**fixed_meals**: Explicitly locked meals - ONLY when the user names the actual food or dish to eat in a slot
- "Fix breakfast as eggs and toast" -> [{"meal_type": "breakfast", "dish": "eggs and toast"}]
- "keep bread and eggs for breakfast every day" -> [{"meal_type": "breakfast", "dish": "bread and eggs"}]
- NEVER use fixed_meals for: a quality or goal ("a high protein dinner" -> style_notes), a meal slot on its own ("a dinner plan", "lunch only" -> unsupported_reason), or foods the user merely has at home ("I have chicken and tomatoes" -> main_ingredients). A dish value that is just the slot name or a style word is always wrong.

**style_notes**: short phrases for what the food should be like ("high protein", "light", "spicy", "low carb", "quick to cook").

**main_ingredients**: foods the user has or wants the meals built around ("I have chicken and tomatoes" -> ["chicken", "tomato"]; "use paneer and spinach" -> ["paneer", "spinach"]). Never foods they want excluded.

**ingredients_only**: true only when they say these are ALL they have or the only foods to use ("I have only chicken and tomatoes", "just these"). False for "include chicken".

**unsupported_reason**: see WHAT THIS APP CAN AND CANNOT DO below.

**edit_details** (only for edit_meal intent):
- day: "day 2" -> 2, "on day 3" -> 3
- meal_type: "breakfast"/"lunch"/"dinner"/"snack"
- target_meal_calories: SLOT calories only (e.g. "400-500 kcal" -> 450.0, "500 kcal" -> 500.0). A "light" or "small" snack with no number -> about 120.0.
- instructions: Full user message for context
- dish_to_replace: Specific dish mentioned ("replace paneer tikka"). For ingredient swaps ("replace all beef dishes with chicken", "swap out the pork"), ALWAYS set this to the ingredient/dish being removed ("beef dishes", "pork") — never leave it null when the message names what goes away.
- operation: "add" (for "add a snack"), "remove" (for "remove the snack"), "replace" (default for "replace/swap/change")
- edit_scope: "slot" (one specific day+meal), "day" ("redo day 2", "change all of day 3"), "plan" ("replace all recipes", "remove dairy from every day", whole-plan changes), null when unclear or still being clarified
  - CRITICAL — only widen the scope when the message really covers EVERY meal it names:
    - Use "plan" ONLY when the message explicitly covers the WHOLE plan ("all days", "every day", "everywhere", "the whole plan", "all my meals", "replace all recipes"). Naming a single meal slot ("swap dinner", "change breakfast", "replace lunch") is NOT plan scope, even though it may repeat across days.
    - Use "day" ONLY when the message covers every meal of that one day ("redo day 2", "change all of day 3", "rebuild day 2"). "Update day 2 with a different meal" names ONE meal, so that is "slot" scope with day set.
  - When a scope is widened but the message names no day or no slot, keep day/meal_type null so the app can ask which one. Silently regenerating a whole day or the whole plan is the one outcome you must never produce.

**target_plan_id** and **target_plan_name** (the "SAVED PLANS" list in the context shows the user's real plans):
- You are the one who works out WHICH saved plan the user means. The application will not search the message for plan names, day counts or diet words, and it will not guess "the first plan" - if you leave both fields empty it asks the user.
- Set target_plan_id to the ID of the plan when the user points at one by any means: its name, "Plan #9", its length or diet ("the 5 day one", "the vegetarian plan"), or a reference to the conversation ("it", "that plan", "the one we just made" - the list marks the plan created in the previous reply).
- Set target_plan_name only when the user names a specific plan ("Good Meal", "keto plan"), even one that is not in the list (so the app can say it does not exist). NEVER put generic words like "my plan", "the meal plan" or "it" there.
- Leave both empty when the user does not point at a specific plan, or when several plans fit and you cannot tell which.

**new_plan_name**: For rename_plan, the new name
- "Rename Plan 1 to High Protein" -> "High Protein"

**is_delete_confirmation**:
- True only if the user is confirming a deletion that is pending (check PENDING WORKFLOW CONTEXT and the previous assistant message): "yes", "yeah", "confirm", "go ahead", "delete it", "ok, do it".
- False for anything that is not a clear yes: questions, hesitation, "wait", "which one?", a change of subject. A word like "ok" or "no" inside another sentence is not a confirmation - read the whole message.

**abandon_pending_work**:
- True only when the user explicitly says to drop, replace or restart the work in progress ("forget it", "start over", "cancel that", "never mind", "do a new plan instead").
- False for a plain request that does not say to abandon what is pending.

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

4b. **Pausing is not cancelling.** If the user wants to come back to the pending question later WITHOUT saying no
   ("let me think about it", "not now, maybe later", "let me cook dinner first", "give me a minute", "I'll decide later"):
   - Set defer_pending = true, abandon_pending_work = false, intent = "chat". NEVER cancel_request.
   - Only a refusal ("no", "keep it", "cancel", "forget it", "don't") cancels. When unsure between pausing and refusing, choose pausing.

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

8b. **Days answer**: If pending_conflicts includes "days_missing" (the assistant asked how many days the plan should cover) and
   the user answers with a length ("3", "3 days", "five", "a week", "the whole week", "tomorrow", "as many as you can" -> 7):
   - Set intent = "new_plan" (or original_intent) and requested_days to that number.
   - Do NOT set diet or calorie confirmation flags; do not repeat other details, the application remembers them.
   - If the user instead asks a question about the limit ("what's the maximum?"), answer-type intents apply (chat/explain), not new_plan.

10. **Save-dislikes answer**: If pending_conflicts includes "save_dislikes" (the assistant created a plan with foods to exclude and asked whether to
   remember them as the user's disliked foods for future plans):
   - "yes", "yes please", "save them", "add them" -> save_preference_answer = true
   - "no", "just for this plan", "don't save", "only this time" -> save_preference_answer = false
   - Any other message (a new request, a question) is NOT an answer: leave save_preference_answer null and classify the message normally.

11. **Offer answers**: If pending_conflicts includes "capability_offer" (the assistant explained it can only plan whole days and offered a full-day plan) or "ingredients_offer" (the assistant explained the listed foods cannot fill a plan on their own and offered to use them as main ingredients and add other foods):
   - yes / go ahead / sounds good (with or without a number of days) -> intent = "new_plan", requested_days if given, unsupported_reason null, ingredients_only false.
   - a number of days alone ("5 days", "a week") -> intent = "new_plan" with requested_days.
   - no / cancel / never mind -> intent = "cancel_request".
   - Do not repeat the other details; the application remembers them.

8. **Profile-defaults agreement**: If the assistant just offered to create the plan with the user's saved profile
   defaults (check the conversation history for that offer) and the user agrees — "yes", "go ahead", "use my profile",
   "create it with my defaults", "sounds good, do it" — set intent = "new_plan" AND use_profile_defaults = true.
   Do NOT set requested_* params in this case; the application fills them from the profile.

CRITICAL: When user answers a pending question PARTIALLY (e.g., only answers diet preference but not calories), the intent should still be the original intent (e.g., "new_plan") with the appropriate confirmation flags set. Do NOT classify as "chat" or "app_info".

---


---

### WHAT THIS APP CAN AND CANNOT DO (use this to fill unsupported_reason)

CAN: build a meal plan of whole days - breakfast, lunch and dinner every day, plus an optional snack - for 1 to 7 days in one request; follow a diet, a cuisine, exclusions, locked meals, style notes (e.g. high protein) and main ingredients; edit a meal in a saved plan.
CANNOT: build a plan of only one kind of meal ("a dinner plan", "only lunches", "just breakfasts"), a single meal on its own ("make me a dinner"), or part of a day. (More than 7 days is handled separately: just set requested_days.)

When a request needs something in the CANNOT list, keep intent = "new_plan", fill EVERY other field you can (days, cuisine, style_notes, main_ingredients, exclusions...), and set unsupported_reason to one short sentence describing what was asked ("wants only dinners, not whole days"). Judge by meaning, not by particular words: "dinner ideas for a week", "a plan with just suppers", "only dinner for 5 days" all need it; "a 5 day plan" or "a high protein plan" do not. Do not set it for ordinary requests. It is ONLY for the structural limits in the CANNOT list: never set it because of a calorie target, a diet, a cuisine, ingredients, exclusions or anything about health or safety (the application handles those itself).

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

def summarize_plans_for_intent(plans: Optional[List[Any]], just_created_plan_id: Optional[int] = None) -> str:
    """Describe the user's saved plans so the model can say WHICH plan a message means.

    The application used to search the raw message for plan names, day counts
    and diet words after the fact. Showing the model the real plans lets it
    resolve "the 5 day one" or "it" itself; the code then only validates that
    the id/name it returns exists and belongs to the user.
    """
    if not plans:
        return "SAVED PLANS: the user has no saved plans.\n\n"
    lines = []
    for p in plans:
        pid = getattr(p, "id", None)
        name = (getattr(p, "name", None) or f"Plan #{pid}")
        days = getattr(p, "days", None)
        cal = getattr(p, "calorie_target", None)
        diet = getattr(p, "diet_type", None) or "diet not set"
        status = getattr(p, "status", None) or "draft"
        cal_txt = f"{int(cal):,} kcal/day" if cal else "calories not set"
        lines.append(f'- ID {pid}: "{name}" - {days} days, {cal_txt}, {diet}, status {status}')
    block = "SAVED PLANS (use these IDs for target_plan_id):\n" + "\n".join(lines) + "\n"
    if just_created_plan_id is not None and any(getattr(p, "id", None) == just_created_plan_id for p in plans):
        block += f"The plan created in the assistant's previous reply is ID {just_created_plan_id}.\n"
    return block + "\n"


async def extract_user_intent(
    user_message: str,
    client: Optional[LLMClient] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    pending_workflow: Optional[Any] = None,
    plans: Optional[List[Any]] = None,
    just_created_plan_id: Optional[int] = None,
) -> UserIntentExtraction:
    """
    Supervisor semantic intent parser using LLM reasoning.
    Leverages LLM intelligence to classify ANY user message against NutriGen's domain boundaries.
    """
    # Nothing readable to interpret (empty, a lone dot, only symbols): there is
    # no point asking the model, and no way to guess an action. This is input
    # hygiene, not intent matching - any message with a letter or digit still
    # goes to the model.
    if not any(ch.isalnum() for ch in (user_message or "")):
        return UserIntentExtraction(
            intent="unclear",
            unclear_reason="The message has no letters or numbers in it (empty or only punctuation/symbols).",
        )

    llm = client or get_llm_client("supervisor")
    context = _build_context_from_history(history) + summarize_plans_for_intent(plans, just_created_plan_id)
    
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
        logger.warning(f"Error in LLM intent extraction: {e}. Asking the user to rephrase.")
        # The model's answer could not be read. Guessing an intent here (the old
        # fallback silently treated it as small talk) would answer a question
        # nobody asked; say so and ask instead.
        return UserIntentExtraction(
            intent="unclear",
            unclear_reason="The assistant could not interpret this message (a temporary problem reading it).",
        )