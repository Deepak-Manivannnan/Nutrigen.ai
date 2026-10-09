from backend.app.config import settings

def is_round_exceeded(current_round: int) -> bool:
    return current_round > settings.max_rounds

def is_step_exceeded(step_count: int) -> bool:
    return step_count > settings.max_agent_steps
