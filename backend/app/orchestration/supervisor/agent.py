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
from backend.app.llm.client import llm_client, LLMClient, LLMUnavailableError, get_llm_client

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
        """Answers food, nutrition, and diet questions directly from LLM knowledge.

        Food questions ("what is an apple?", "protein in chicken?") are always
        answered — never deflected. Amount-specific questions are scaled to the
        asked amount; otherwise per-100g (or per typical unit) values are given.
        """
        name = state.user_profile.get("name", "there")
        history_ctx = self._format_history_context(history)

        prompt = (
            f"You are NutriGen, a knowledgeable food and nutrition companion talking with {name}.\n\n"
            f"{history_ctx}"
            f"Target calories: {state.calorie_target or 'standard'}\n"
            f"Diet: {state.user_profile.get('diet_type', 'veg')}\n\n"
            f"User Query: {user_query}\n\n"
            f"Answer their food/nutrition question directly from your knowledge in a warm, conversational tone (3 to 5 sentences).\n"
            f"RULES:\n"
            f"- If they ask what a FOOD is, briefly say what it is (taste, type, common uses) plus 1-2 key nutrition highlights.\n"
            f"- If they ask about calories/protein/carbs/fat in a food WITH a specific amount (e.g. '2 apples', '200g chicken'), scale your answer to that amount.\n"
            f"- If NO amount is given, give values per 100 grams (or per typical single unit, e.g. one medium apple ~182g) and say so.\n"
            f"- Give approximate ranges honestly (e.g. 'roughly 23-24 g protein per 100 g chicken breast').\n"
            f"- NEVER deflect with 'my expertise is focused on meal plans rather than defining foods' or similar — answering food questions IS your job.\n"
            f"- Do NOT pivot to selling meal plans. One brief, natural serving tip at most.\n"
            f"CRITICAL: Do NOT begin with 'Hey {name}' or 'Hi {name}'. Answer directly in the flow of conversation."
        )

        try:
            return await self.llm.generate_text(prompt)
        except LLMUnavailableError:
            # Backend outage: propagate for an honest service-trouble
            # message rather than a canned reply that pretends understanding.
            raise
        except Exception as e:
            return f"Good question about '{user_query}' — happy to dig into the nutrition details for you. Could you tell me which specific food or nutrient you'd like values for (and the amount, if any)?"

    async def generate_out_of_scope_decline(
        self,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        user_name: Optional[str] = None,
    ) -> str:
        """Short decline used ONLY while another workflow is pending.

        Unlike the full out-of-scope reply, this states NO factual answer at
        all — the pending question follows immediately, so the decline must not
        echo or repeat any answer content.
        """
        name = (user_name or "").strip() or "there"
        history_ctx = self._format_history_context(history)
        prompt = (
            f"You are NutriGen, a meal planning and nutrition assistant talking with {name}.\n\n"
            f"{history_ctx}"
            f"The user just asked something completely outside your scope: \"{user_message}\"\n\n"
            f"In ONE short, warm sentence, politely decline to answer it: you are strictly focused on meal "
            f"planning, nutrition, recipes, and groceries, so you can't help with that particular question.\n"
            f"CRITICAL: Do NOT answer the question itself. Do NOT state any fact from it (no capitals, no "
            f"definitions, no explanations of any kind). No greeting, no bullet points."
        )
        try:
            return await self.llm.generate_text(prompt)
        except LLMUnavailableError:
            # Backend outage: propagate for an honest service-trouble
            # message rather than a canned reply that pretends understanding.
            raise
        except Exception:
            return (
                "I can't help with that one — I'm strictly focused on meal planning, nutrition, recipes, and groceries."
            )

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
                f"Present options naturally without bullet points.\n"
                f"CRITICAL: the user has not changed anything yet. Never say or imply that you updated, "
                f"changed or saved their profile, diet preference, calorie range or settings - nothing has been "
                f"written. Whatever they pick will apply to THIS plan only."
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
                f"Present options naturally without bullet points.\n"
                f"CRITICAL: the user has not changed anything yet. Never say or imply that you updated, "
                f"changed or saved their profile, diet preference, calorie range or settings - nothing has been "
                f"written. Whatever they pick will apply to THIS plan only."
            )
        
        try:
            return await self.llm.generate_text(prompt)
        except LLMUnavailableError:
            # Backend outage: propagate for an honest service-trouble
            # message rather than a canned reply that pretends understanding.
            raise
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
        history: Optional[List[Dict[str, Any]]] = None,
        profile_snapshot: Optional[str] = None
    ) -> str:
        """
        Conversational greeting & chat response.
        The LLM reads the user's message and decides: warm reply, or — when the
        user asks for meal-planning help without giving parameters — ask 1-2
        clarifying questions. No keyword lists: understanding lives in the model.
        A compact profile snapshot grounds the model so profile-mentioning
        questions can never be answered with invented "missing details".
        """
        name = (user_name or "").strip() or "there"
        history_ctx = self._format_history_context(history)
        is_first_interaction = not history or len(history) <= 1
        profile_ctx = (
            f"Saved profile facts (REFERENCE ONLY about THIS user — use them only when the user's "
            f"message actually calls for them):\n{profile_snapshot}\n\n"
            if profile_snapshot else ""
        )

        prompt = (
            f"You are NutriGen, a helpful personal meal planning companion talking with {name}.\n"
            f"{history_ctx}"
            f"{profile_ctx}"
            f"The user says: \"{user_message}\"\n\n"
            f"Understand what the user means and respond naturally, warmly, and concisely (1 to 3 sentences maximum).\n"
            f"RULES:\n"
            f"- If this is the FIRST greeting, you may say 'Hi {name}! How can I help you today?'.\n"
            f"- In an ONGOING conversation, do NOT repeat greetings ('Hey {name}'/'Hi {name}'). Reply directly in flow.\n"
            f"- If the user asks about their profile, account details, or preferences, answer ONLY from the saved profile facts above — "
            f"never claim details are missing when they are listed there.\n"
            f"- NEVER volunteer profile details. Do NOT mention their calorie target, diet type, goal or allergies "
            f"UNLESS the user is either (a) asking about those things, or (b) creating, changing or updating a meal plan. "
            f"For a greeting, a 'how are you', a thank-you, or any small talk, reply like a normal person would: be friendly "
            f"and ask how you can help — with NO numbers, targets, or diet details quoted.\n"
            f"- If the user is asking for help creating a meal plan but has NOT given specifics (no days, calories, or diet), "
            f"acknowledge warmly by name and ask the 1-2 most useful clarifying questions "
            f"(how many days? target calories per day? diet type veg/non-veg/vegan?). End with an open question.\n"
            f"- Otherwise simply reply to what they said like a thoughtful human would.\n"
            f"- Do NOT dump an unsolicited list of features, bullet points, or instructions.\n"
            f"- Keep it natural, warm, and human."
        )
        try:
            return await self.llm.generate_text(prompt)
        except LLMUnavailableError:
            # Backend outage: propagate for an honest service-trouble
            # message rather than a canned reply that pretends understanding.
            raise
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
            f"Answer their question directly, warmly, and conversationally in 2 to 4 sentences, like a knowledgeable friend would:\n"
            f"- First acknowledge what THEY asked about (show you understood their situation, e.g. that they are new here).\n"
            f"- Then describe concretely what you can do for them: personalized multi-day meal plans, calorie targets matched to their profile, diet preferences and allergy safety, plan edits, approvals with grocery lists and mandi prices.\n"
            f"- End with one natural next-step question (e.g. what they would like to plan first).\n"
            f"- No repetitive greetings ('Hey', 'Hi') in ongoing dialogue, no rigid brochure tone."
        )
        try:
            return await self.llm.generate_text(prompt)
        except LLMUnavailableError:
            # Backend outage: propagate for an honest service-trouble
            # message rather than a canned reply that pretends understanding.
            raise
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
        Conversational and warm: acknowledge the actual question, state the boundary
        naturally, and offer in-domain help. Never a canned refusal sentence.
        """
        history_ctx = self._format_history_context(history)

        prompt = (
            f"You are NutriGen, a dedicated meal planning and nutrition assistant with a warm, human conversational style.\n\n"
            f"{history_ctx}"
            f"The user asked: \"{user_message}\"\n\n"
            f"Reply in 2 to 3 natural sentences, like a helpful friend:\n"
            f"- Briefly acknowledge what they asked about so they feel heard.\n"
            f"- Explain conversationally that you are built specifically for meal planning, nutrition, recipes, and groceries, so this particular question is outside what you can help with.\n"
            f"- Offer something genuinely useful you CAN do instead, tied to their context where possible "
            f"(e.g. plan their meals, explain a nutrition topic, check their profile).\n"
            f"- CRITICAL: Do NOT start with 'Hey' or greetings. Do NOT recite a rigid boilerplate refusal. "
            f"Vary your wording; sound human, not scripted.\n"
            f"Do NOT use bullet points or structured lists."
        )
        try:
            return await self.llm.generate_text(prompt)
        except LLMUnavailableError:
            # Backend outage: propagate for an honest service-trouble
            # message rather than a canned reply that pretends understanding.
            raise
        except Exception:
            return (
                f"That's an interesting question about '{user_message}' — honestly, I'm built specifically for meal planning, "
                f"recipes, and nutrition, so I can't do that one justice. "
                f"But I'd love to help with your meals, diet questions, or grocery planning instead!"
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
        except LLMUnavailableError:
            # Backend outage: propagate for an honest service-trouble
            # message rather than a canned reply that pretends understanding.
            raise
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
            return await self.generate_chat_response(
                user_name, user_message, history=history,
                profile_snapshot=self._compact_profile_snapshot(state.user_profile)
            )
        elif intent == "app_info":
            return await self.generate_app_info_response(user_name, user_message, history=history)
        elif intent == "out_of_scope":
            return await self.generate_out_of_scope_response(user_message, history=history)
        elif intent == "explain":
            return await self.generate_explanation(state, user_message, history=history)
        return await self.generate_chat_response(
            user_name, user_message, history=history,
            profile_snapshot=self._compact_profile_snapshot(state.user_profile)
        )

    @staticmethod
    def _compact_profile_snapshot(user_profile: Optional[Dict[str, Any]]) -> Optional[str]:
        """One-line ground truth about the user for chat grounding (~40 tokens)."""
        if not user_profile:
            return None
        diet = {"veg": "Vegetarian", "non-veg": "Non-Vegetarian", "vegan": "Vegan"}.get(
            str(user_profile.get("diet_type", "")).lower(), str(user_profile.get("diet_type", "veg")))
        bits = [f"diet preference: {diet}"]
        try:
            if user_profile.get("suggested_target_calories") is not None:
                bits.append(f"suggested ~{int(user_profile['suggested_target_calories']):,} kcal/day")
        except (TypeError, ValueError):
            pass
        if user_profile.get("goal"):
            bits.append(f"goal: {user_profile['goal']}")
        if user_profile.get("allergies"):
            bits.append(f"allergies: {', '.join(user_profile['allergies'])}")
        return "- " + "; ".join(bits) if bits else None

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
        except LLMUnavailableError:
            # Backend outage: propagate for an honest service-trouble message
            # rather than a canned reply that pretends understanding.
            raise
        except Exception as e:
            # This fallback backs EVERY composed reply (plan created, diet-conflict
            # question, approval, rename, delete confirm...). It must never claim
            # something was done: on a failure nothing happened, and a false
            # success message leaves the user believing a plan exists.
            logger.warning(
                f"[SupervisorAgent] LLM conversational generation failed: {type(e).__name__}: {e}. "
                "Falling back to an honest retry prompt."
            )
            return (
                "Sorry — I hit a snag just now and wasn't able to finish that. "
                "Could you try again in a moment?"
            )
