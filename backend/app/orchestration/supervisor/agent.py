import os
from typing import Dict, Any, Optional, List
from backend.app.schemas.state import PlanState
from backend.app.schemas.messages import AgentMessage
from backend.app.schemas.domain import MealPlan, NutritionReport, AllergyReport, GroceryList
from backend.app.orchestration.supervisor.routing import decide_next_step, RouteDecision
from backend.app.guardrails.input_checks import validate_input_request
from backend.app.guardrails.output_checks import verify_output_safety
from backend.app.observability.trace import trace_hub
from backend.app.observability.logging import get_logger
from backend.app.llm.client import llm_client, LLMClient, get_llm_client

logger = get_logger("supervisor.agent")

def _load_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "You are the Supervisor. Orchestrate the multi-agent workflow safely."

class SupervisorAgent:
    """
    Supervisor Agent: Central coordinator of the meal planner multi-agent system.
    Manages workflow state, enforces guardrails, communicates with isolated worker agents,
    evaluates reports via deterministic routing, and logs audit traces.
    """
    def __init__(self, client: Optional[LLMClient] = None):
        self.system_prompt = _load_prompt()
        self.llm = client or get_llm_client("supervisor")

    async def log_step(
        self,
        state: PlanState,
        from_agent: str,
        to_agent: str,
        msg_type: str,
        payload: Dict[str, Any],
        session: Any = None,
        content: Optional[str] = None
    ) -> AgentMessage:
        msg = AgentMessage(
            task_id=state.task_id,
            from_agent=from_agent,
            to_agent=to_agent,
            type=msg_type,
            content=content or str(payload.get("message") or payload.get("summary") or payload.get("task") or payload.get("reason") or ""),
            payload=payload,
            plan_version=state.plan_version
        )
        state.trace.append(msg)
        await trace_hub.log_event(msg, session=session)
        return msg

    def check_input_guardrail(
        self,
        user_profile: Dict[str, Any],
        requested_calories: Optional[float],
        requested_days: Optional[int],
        fixed_meals: Optional[Dict[str, Any]] = None,
        requested_diet_type: Optional[str] = None
    ):
        return validate_input_request(
            user_profile=user_profile,
            requested_calories=requested_calories,
            requested_days=requested_days,
            fixed_meals=fixed_meals,
            requested_diet_type=requested_diet_type
        )

    def evaluate_reviews(self, state: PlanState) -> RouteDecision:
        return decide_next_step(
            current_round=state.round,
            nutrition_report=state.nutrition_report,
            allergy_report=state.allergy_report,
            max_rounds=state.max_rounds
        )

    async def generate_explanation(
        self,
        state: PlanState,
        user_query: str,
        history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        name = state.user_profile.get("name", "there")
        history_ctx = self._format_history_context(history)

        prompt = (
            f"You are the Supervisor in NutriGen explaining a nutrition decision to {name} in friendly, conversational language.\n\n"
            f"{history_ctx}"
            f"Target calories: {state.calorie_target or 'standard'}\n"
            f"Diet: {state.user_profile.get('diet_type', 'veg')}\n\n"
            f"User Query: {user_query}\n\n"
            f"Provide a clear, helpful, non-technical explanation answering their question directly in 2 to 3 sentences.\n"
            f"CRITICAL: Do NOT begin with 'Hey {name}' or 'Hi {name}'. Answer directly in the flow of conversation."
        )

        try:
            return await self.llm.generate_text(prompt)
        except Exception as e:
            return f"Regarding your question about '{user_query}': I'm here to ensure your meals are balanced and safe. Let me know if you'd like me to adjust anything!"

    def _format_history_context(self, history: Optional[List[Dict[str, Any]]]) -> str:
        if not history:
            return ""
        lines = []
        for h in history[-4:]:
            role = "User" if h.get("role") == "user" else "Assistant"
            c = (h.get("content") or "").strip()
            if c:
                lines.append(f"{role}: {c}")
        if not lines:
            return ""
        return "Conversation history:\n" + "\n".join(lines) + "\n\n"

    async def generate_diet_conflict_prompt(
        self,
        user_name: str,
        profile_diet: str,
        requested_diet: str,
        cal_val: int,
        days_val: int,
        is_calorie_unsafe: bool,
        meta: Dict[str, Any],
        history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Generates a natural, conversational prompt for diet conflict resolution.
        Uses LLM intelligence instead of hardcoded templates.
        """
        name = (user_name or "").strip() or "there"
        history_ctx = self._format_history_context(history)
        
        profile_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(profile_diet, profile_diet.capitalize())
        requested_display = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(requested_diet, requested_diet.capitalize())
        
        if is_calorie_unsafe:
            suggested = int(meta.get("suggested_target", 2250.0))
            min_safe = int(meta.get("min_safe", 2150.0))
            max_safe = int(meta.get("max_safe", 2350.0))
            prompt = (
                f"You are NutriGen, a helpful meal planning companion talking with {name}.\n\n"
                f"{history_ctx}"
                f"Situation: The user's profile diet preference is **{profile_display}**, but they just requested a **{requested_display}** meal plan "
                f"for {days_val} days at {cal_val:,} kcal/day.\n\n"
                f"Additionally, {cal_val:,} kcal/day is below their safe maintenance range of {min_safe:,}–{max_safe:,} kcal. "
                f"The recommended target is {suggested:,} kcal/day.\n\n"
                f"Your task: Explain both conflicts (diet + calories) naturally in 2-3 sentences. "
                f"Ask whether they want to: (1) proceed with {requested_display} at {cal_val:,} kcal, "
                f"or (2) use the recommended {suggested:,} kcal with {requested_display} diet, "
                f"or (3) use their profile {profile_display} diet.\n"
                f"CRITICAL: Do NOT start with 'Hey {name}' or greetings. Be direct, warm, and clear. "
                f"Present options naturally without bullet points."
            )
        else:
            prompt = (
                f"You are NutriGen, a helpful meal planning companion talking with {name}.\n\n"
                f"{history_ctx}"
                f"Situation: The user's profile diet preference is **{profile_display}**, but they just requested a **{requested_display}** meal plan "
                f"for {days_val} days at {cal_val:,} kcal/day.\n\n"
                f"Your task: Explain the diet preference mismatch naturally in 2-3 sentences. "
                f"Ask whether they want to: (1) proceed with {requested_display}, "
                f"or (2) use their profile {profile_display} diet.\n"
                f"CRITICAL: Do NOT start with 'Hey {name}' or greetings. Be direct, warm, and clear. "
                f"Present options naturally without bullet points."
            )
        
        try:
            return await self.llm.generate_text(prompt)
        except Exception as e:
            # Fallback to simple template if LLM fails
            if is_calorie_unsafe:
                suggested = int(meta.get("suggested_target", 2250.0))
                return (
                    f"Your profile preference is {profile_display}, but you requested a {requested_display} meal plan at {cal_val:,} kcal/day. "
                    f"That's below your safe range ({min_safe:,}–{max_safe:,} kcal). "
                    f"Would you like me to create the {requested_display} plan at {cal_val:,} kcal, "
                    f"or at the recommended {suggested:,} kcal, or use your {profile_display} profile instead?"
                )
            return (
                f"Your profile preference is {profile_display}, but you requested a {requested_display} meal plan. "
                f"Would you like me to proceed with {requested_display} or use your {profile_display} profile diet instead?"
            )

    async def generate_chat_response(
        self,
        user_name: str,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Conversational greeting & chat response.
        Friendly, natural human-to-human tone without repetitive greetings in ongoing dialog.
        Handles meal planning help requests by asking clarifying questions.
        """
        name = (user_name or "").strip() or "there"
        history_ctx = self._format_history_context(history)
        is_first_interaction = not history or len(history) <= 1

        # Check if user is asking for help with meal planning (general request without specific params)
        msg_lower = user_message.lower()
        is_meal_planning_help = any(phrase in msg_lower for phrase in [
            "help me create", "help me create a meal plan", "help me plan",
            "create a meal plan", "create meal plan", "meal plan for me",
            "plan my meals", "plan meals", "need a meal plan",
            "want a meal plan", "want to create a meal plan",
            "help with meal plan", "help with meal planning",
            "help me create a plan", "create a plan for me", "plan for me",
            "help me with a plan", "make a plan", "make a meal plan",
            "can you help me create a plan", "can you create a plan"
        ])

        if is_meal_planning_help:
            prompt = (
                f"You are NutriGen, a helpful personal meal planning companion.\n"
                f"User's name: {name}\n\n"
                f"{history_ctx}\n"
                f"The user says: \"{user_message}\"\n\n"
                f"The user is asking for help creating a meal plan but hasn't provided specific parameters yet.\n\n"
                f"Respond naturally, conversationally, and concisely (2-3 sentences max).\n"
                f"RULES:\n"
                f"- Acknowledge their request warmly and address them by name.\n"
                f"- Ask 1-2 clarifying questions to get the minimum needed parameters:\n"
                f"  * How many days? (default 7)\n"
                f"  * Target calories per day? (or I can calculate from their profile)\n"
                f"  * Diet type? (veg/non-veg/vegan - default from profile)\n"
                f"- Do NOT dump features, bullet points, or instructions.\n"
                f"- Keep it natural, warm, and conversational.\n"
                f"- If this is NOT the first interaction, do NOT say 'Hey {name}' or 'Hi {name}' again!\n"
                f"- End with an open question inviting them to provide details."
            )
        else:
            prompt = (
                f"You are NutriGen, a helpful personal meal planning companion.\n"
                f"User's name: {name}\n\n"
                f"{history_ctx}\n"
                f"The user says: \"{user_message}\"\n\n"
                f"Respond naturally, conversationally, and concisely (1 to 2 sentences maximum).\n"
                f"RULES:\n"
                f"- If this is the FIRST greeting message, you may say 'Hi {name}! How can I help you today?'.\n"
                f"- If this is an ONGOING conversation, do NOT say 'Hey {name}' or 'Hi {name}' again! Simply reply directly to what they said in conversation flow.\n"
                f"- Do NOT dump an unsolicited list of features, bullet points, or instructions.\n"
                f"- Keep it natural, warm, and human."
            )
        try:
            return await self.llm.generate_text(prompt)
        except Exception:
            if is_first_interaction:
                return f"Hi {name}! How can I help you today?"
            return "How can I help you with your meal planning today?"

    async def generate_app_info_response(
        self,
        user_name: str,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Answers inquiries about what NutriGen can do or what information it provides.
        Responds directly and conversationally without repeating greetings or rigid brochures.
        """
        name = (user_name or "").strip() or "there"
        history_ctx = self._format_history_context(history)

        prompt = (
            f"You are NutriGen, a helpful personal meal planning assistant talking with {name}.\n\n"
            f"{history_ctx}"
            f"The user asks: \"{user_message}\"\n\n"
            f"Answer their question directly, warmly, and concisely in 1 to 2 sentences:\n"
            f"- Do NOT recite marketing filler, promotional pitches, or phrases like 'Beyond crafting these balanced menus...'.\n"
            f"- Jump straight into answering what was asked in a clean, professional, conversational tone.\n"
            f"- No repetitive greetings ('Hey', 'Hi') in ongoing dialogue."
        )
        try:
            return await self.llm.generate_text(prompt)
        except Exception:
            return (
                "I can help you create personalized daily meal plans based on your calorie goals and dietary preferences, "
                "make sure dishes avoid any allergens you have, and provide estimated grocery shopping costs. "
                "What would you like to plan or know about today?"
            )

    async def generate_out_of_scope_response(
        self,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Polite, brief domain guardrail message when the user asks questions outside of
        meal planning, recipes, nutrition, or grocery shopping (e.g., 'what is an AI').
        Keeps it concise, friendly, and human without repetitive greetings or lectures.
        """
        history_ctx = self._format_history_context(history)

        prompt = (
            f"You are NutriGen, a dedicated meal planning and nutrition assistant.\n\n"
            f"{history_ctx}"
            f"The user asked: \"{user_message}\"\n\n"
            f"In 1 to 2 natural, polite sentences, explain that your expertise is dedicated exclusively to meal planning, nutrition, recipes, and groceries, so you cannot assist with topics outside of food like \"{user_message}\".\n"
            f"CRITICAL: Do NOT start with 'Hey' or greetings. State clearly that you cannot assist with topics outside of food.\n"
            f"Do NOT use bullet points or structured lists."
        )
        try:
            return await self.llm.generate_text(prompt)
        except Exception:
            return (
                f"My expertise is dedicated exclusively to meal planning, recipes, and nutrition, so I cannot assist with topics outside of food. "
                f"Feel free to ask me anything about your diet, meals, or groceries though!"
            )

    async def generate_profile_info_response(
        self,
        state: PlanState,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Provides detailed, structured response listing the exact parameters
        held in the user's profile.
        """
        p = state.user_profile or {}
        name = p.get("name", "there")
        age = p.get("age", "Not set")
        sex = (str(p.get("sex", "Not set"))).capitalize()
        height = p.get("height_cm", "Not set")
        weight = p.get("weight_kg", "Not set")
        activity = (str(p.get("activity_level", "Medium"))).capitalize()
        goal = {"lose": "Lose weight", "maintain": "Maintain weight", "gain": "Gain weight"}.get(p.get("goal"), (str(p.get("goal", "Maintain weight"))).capitalize())
        diet = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(p.get("diet_type"), (str(p.get("diet_type", "Vegetarian"))).capitalize())
        dislikes = ", ".join(p.get("dislikes", [])) if p.get("dislikes") else "None specified"
        allergies = ", ".join(p.get("allergies", [])) if p.get("allergies") else "None recorded"

        from backend.app.guardrails.calorie_rules import calculate_mifflin_st_jeor
        cal_info = calculate_mifflin_st_jeor(
            age=int(p.get("age", 26)),
            sex=str(p.get("sex", "male")),
            height_cm=float(p.get("height_cm", 175)),
            weight_kg=float(p.get("weight_kg", 70)),
            activity_level=str(p.get("activity_level", "medium")),
            goal=str(p.get("goal", "maintain"))
        )
        min_cal = int(cal_info["min_safe_calories"])
        max_cal = int(cal_info["max_safe_calories"])
        target_cal = int(cal_info["suggested_target"])

        profile_summary = (
            f"User profile details for {name}:\n"
            f"- Age: {age} years\n"
            f"- Gender: {sex}\n"
            f"- Height: {height} cm\n"
            f"- Weight: {weight} kg\n"
            f"- Activity Level: {activity}\n"
            f"- Health Goal: {goal}\n"
            f"- Dietary Preference: {diet}\n"
            f"- Daily Recommended Calorie Range: {min_cal:,} – {max_cal:,} kcal (Midpoint target: {target_cal:,} kcal)\n"
            f"- Disliked Foods: {dislikes}\n"
            f"- Allergies & Restrictions: {allergies}\n"
        )
        context_prompt = (
            f"The user asked: '{user_message}'.\n"
            f"Here are the exact biometric and dietary parameters retrieved from their profile:\n"
            f"{profile_summary}\n\n"
            f"Present these details clearly, accurately, and naturally in friendly, helpful conversational English. "
            f"You may format the key profile parameters as clean bullet points and explain how NutriGen uses them to balance meals, portion sizes, and dietary boundaries."
        )
        try:
            return await self.generate_conversational_response(
                user_message=user_message,
                context=context_prompt,
                history=history,
                user_name=name
            )
        except Exception as e:
            logger.warning(f"LLM generation failed for profile info: {e}")
            return (
                f"Here are the specific details currently stored in your profile, {name}:\n\n"
                f"• **Age**: {age} years\n"
                f"• **Gender**: {sex}\n"
                f"• **Height & Weight**: {height} cm | {weight} kg\n"
                f"• **Activity Level**: {activity}\n"
                f"• **Health Goal**: {goal}\n"
                f"• **Dietary Preference**: {diet}\n"
                f"• **Daily Recommended Calorie Range**: {min_cal:,} – {max_cal:,} kcal (Midpoint target: {target_cal:,} kcal)\n"
                f"• **Disliked Foods**: {dislikes}\n"
                f"• **Allergies & Restrictions**: {allergies}\n\n"
                f"I use these biometric parameters and boundaries to balance your meals, calculate portion sizes, and ensure all dishes adhere strictly to your preferences!"
            )

    async def handle_conversational_intent(
        self,
        state: PlanState,
        user_message: str,
        intent: str,
        history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Dispatches non-planning user messages to appropriate friendly responses.
        """
        user_name = state.user_profile.get("name", "there")
        if intent == "profile_info":
            return await self.generate_profile_info_response(state, user_message, history=history)
        elif intent == "chat":
            return await self.generate_chat_response(user_name, user_message, history=history)
        elif intent == "app_info":
            return await self.generate_app_info_response(user_name, user_message, history=history)
        elif intent == "out_of_scope":
            return await self.generate_out_of_scope_response(user_message, history=history)
        elif intent == "explain":
            return await self.generate_explanation(state, user_message, history=history)
        return await self.generate_chat_response(user_name, user_message, history=history)

    async def generate_conversational_response(
        self,
        user_message: str,
        context: str,
        history: Optional[List[Dict[str, Any]]] = None,
        user_name: Optional[str] = None
    ) -> str:
        """
        Dynamically generates a natural, warm, conversational response using LLM intelligence.
        Takes the executed tool/workflow output and contextual background, and expresses it
        in simple, friendly, polite conversational English without rigid predefined static text.
        """
        name = (user_name or "").strip() or "there"
        history_ctx = self._format_history_context(history)

        prompt = (
            f"You are NutriGen, a helpful, friendly, and knowledgeable personal meal planning companion talking with {name}.\n\n"
            f"{history_ctx}"
            f"User's message: \"{user_message}\"\n\n"
            f"Internal task outcome and context:\n"
            f"{context}\n\n"
            f"Instructions for your response:\n"
            f"1. Respond directly to the user in natural, polite, simple English (2 to 3 sentences).\n"
            f"2. Use your own conversational intelligence—never recite rigid boilerplate or marketing brochure text.\n"
            f"3. Explain clearly and warmly what was done or what the user needs to know based on the context above.\n"
            f"4. If in an ongoing dialogue, do NOT start with repetitive greetings ('Hey {name}' or 'Hi'). Answer directly in conversation flow.\n"
            f"5. Maintain a supportive, encouraging, professional tone.\n"
            f"6. CRITICAL: You have full access to manage all meal plans directly in the database. NEVER ask the user to 'paste the plan here', 'paste your plan', or provide plan text. You can directly inspect and update their active plans."
        )
        try:
            return await self.llm.generate_text(prompt)
        except Exception as e:
            logger.warning(f"[SupervisorAgent] LLM conversational generation failed: {e}. Falling back to clean context.")
            c_low = context.lower()
            if "don't have an active meal plan yet" in c_low or "0 active meal plans" in c_low or "0 saved meal plans" in c_low:
                return "You don't have an active meal plan yet to update. Would you like me to create one for you? Just let me know your calorie target or preferences!"
            if "already approved" in c_low:
                return "Your meal plan is already approved! You can view its full itemized grocery checklist in the Grocery tab."
            if "approved" in c_low:
                return "Your meal plan is now approved! You can check your grocery checklist in the Grocery tab."
            if "deleted" in c_low:
                return "Your meal plan has been permanently deleted. Let me know whenever you would like to create a new one!"
            if "renamed" in c_low:
                return "Your meal plan has been renamed successfully!"
            if "snack" in c_low:
                return "I've added the healthy snack to your meal plan in the Plans tab. Take a look and let me know if you would like any other adjustments!"
            return "I've updated your meal plan! You can review the details in your Plans tab."
