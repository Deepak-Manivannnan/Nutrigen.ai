from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    app_name: str = "NutriGen.ai"
    app_env: str = "development"
    # Verbose DEBUG logging. Off by default; switch on in your local .env only
    # (DEBUG=true). Debug logs can contain user messages and internal details.
    debug: bool = False

    # Websites allowed to call this API from a browser, comma separated, e.g.
    # FRONTEND_ORIGINS=https://nutrigen-frontend.onrender.com
    # Left empty: local development allows http://localhost / 127.0.0.1 on any
    # port, while production allows NO website until this is set.
    frontend_origins: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def cors_origins(self) -> list:
        return [o.strip().rstrip("/") for o in self.frontend_origins.split(",") if o.strip()]
    
    # Database
    # No default on purpose: the real connection string (including username and
    # password) must come from the DATABASE_URL environment variable or a local
    # .env file, never be committed to source control.
    database_url: str = ""
    
    # API Keys
    usda_api_key: str = "DEMO_KEY"
    data_gov_in_api_key: Optional[str] = None
    ceda_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    gemini_api_key_1: Optional[str] = None
    gemini_api_key_2: Optional[str] = None

    # Model settings per agent (Optimized for RPM Quota Isolation)
    supervisor_model: str = "gemini-3.1-flash-lite"
    planner_model: str = "gemini-3.5-flash-lite"
    nutrition_model: str = "gemini-3.5-flash-lite"
    allergy_model: str = "gemini-3.5-flash"
    shopper_model: str = "gemini-3.1-flash-lite"
    # Used ONLY for the Nutrition Agent's two mechanical calls: rewriting a
    # dish name into a searchable food word, and supplying macros when the
    # database has nothing. Both are lookups, not reasoning, so the cheaper
    # non-reasoning model is used and the agent's own model is restored after.
    ingredient_lookup_model: str = "gemini-2.5-flash"
    
    # Agent Guardrails & Limits
    max_rounds: int = 3
    max_agent_steps: int = 6
    agent_timeout_seconds: int = 30

    # HMAC key used to sign session tokens. Set AUTH_TOKEN_SECRET in the
    # deployment environment; the fallback only exists so a fresh clone runs,
    # and it means anyone could forge tokens if it is left in place.
    auth_token_secret: str = ""
    # How long a "stay signed in" key lasts without being used. Every use hands
    # out a fresh key, so someone who keeps using the app is never signed out;
    # someone who stays away this long has to sign in again.
    refresh_token_ttl_days: int = 30
    default_calorie_buffer_pct: float = 0.05  # ±5%
    min_calories_adult: int = 1200
    max_calories_adult: int = 4000
    min_age_allowed: int = 18
    # Longest plan built in a single run. A 7-day plan is the largest that
    # reliably fits one structured write from the planner agent; longer plans
    # came back truncated or internally inconsistent, so anything above this is
    # declined with an offer of a 7-day plan instead.
    max_plan_days: int = 7

    # Retail markup over wholesale mandi prices (e.g. 25% markup)
    mandi_retail_markup_factor: float = 1.25

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
