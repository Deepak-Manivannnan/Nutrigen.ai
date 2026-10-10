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
        fixed_meals: Optional[Dict[str, Any]] = None
    ):
        return validate_input_request(
            user_profile=user_profile,
            requested_calories=requested_calories,
            requested_days=requested_days,
            fixed_meals=fixed_meals
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

    async def generate_chat_response(
        self,
        user_name: str,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Conversational greeting & chat response.
        Friendly, natural human-to-human tone without repetitive greetings in ongoing dialog.
        """
        name = (user_name or "").strip() or "there"
        history_ctx = self._format_history_context(history)
        is_first_interaction = not history or len(history) <= 1

        prompt = (
            f"You are NutriGen, a helpful personal meal planning companion.\n"
            f"User's name: {name}\n\n"
            f"{history_ctx}"
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
            f"In 1 to 2 natural, polite sentences, explain that your focus is dedicated exclusively to meal planning, nutrition, recipes, and groceries, so you cannot assist with topics outside of food like \"{user_message}\".\n"
            f"CRITICAL: Do NOT start with 'Hey' or greetings. State the boundary directly and conversationally.\n"
            f"Do NOT use bullet points or structured lists."
        )
        try:
            return await self.llm.generate_text(prompt)
        except Exception:
            return (
                f"I'm specifically dedicated to meal planning, recipes, and nutrition, so I can't help with general topics like that. "
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
