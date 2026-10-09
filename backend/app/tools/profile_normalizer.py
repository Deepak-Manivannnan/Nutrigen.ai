from typing import List, Tuple
from pydantic import BaseModel, Field
from backend.app.llm.client import get_llm_client
from backend.app.observability.logging import get_logger

logger = get_logger("profile.normalizer")

class NormalizedFoodRestrictions(BaseModel):
    cleaned_dislikes: List[str] = Field(
        default_factory=list,
        description="Clean, canonical, lowercased food item names without typos or misspellings (e.g. 'broccoli', 'eggplant', 'brussels sprouts', 'bitter gourd')."
    )
    cleaned_allergies: List[str] = Field(
        default_factory=list,
        description="Standardized, lowercased allergen or food category names (e.g. 'peanuts', 'shellfish', 'dairy', 'gluten', 'eggs', 'soy', 'fish')."
    )

async def normalize_food_restrictions(raw_dislikes: List[str], raw_allergies: List[str]) -> Tuple[List[str], List[str]]:
    """
    Normalizes free-text food dislikes and allergies entered during onboarding or profile editing:
    - Parses free-text strings or tags that may contain typos (e.g. 'brocoli' -> 'broccoli', 'brussel sprouts' -> 'brussels sprouts').
    - Standardizes culinary names to canonical singular lowercased items.
    - Ensures clean structured entries for database persistence and reliable exclusion by the Planner/Allergy agents.
    """
    if not raw_dislikes and not raw_allergies:
        return [], []

    llm = get_llm_client("supervisor")
    prompt = (
        "You are a clinical nutrition food taxonomy specialist.\n"
        "Normalize the user's food dislikes and allergies into clean, standardized, singular/canonical culinary names.\n"
        "Rules:\n"
        "1. Correct any spelling mistakes or typos (e.g. 'brocoli' -> 'broccoli', 'brussel sprouts' -> 'brussels sprouts', 'peenuts' -> 'peanuts', 'eggplants' -> 'eggplant', 'capsicums' -> 'capsicum').\n"
        "2. Lowercase all names.\n"
        "3. Remove duplicate entries.\n"
        "4. If a single string contains multiple items (e.g. 'milk and cheese'), split them into separate items.\n\n"
        f"User entered Dislikes: {raw_dislikes}\n"
        f"User entered Allergies: {raw_allergies}\n"
    )

    try:
        res: NormalizedFoodRestrictions = await llm.generate_structured(
            prompt=prompt,
            response_schema=NormalizedFoodRestrictions
        )
        logger.info(f"Normalized restrictions: dislikes={res.cleaned_dislikes}, allergies={res.cleaned_allergies}")
        return res.cleaned_dislikes, res.cleaned_allergies
    except Exception as e:
        logger.warning(f"Error normalizing food restrictions with LLM: {e}. Falling back to basic sanitization.")
        clean_d = list({d.strip().lower() for d in raw_dislikes if d.strip()})
        clean_a = list({a.strip().lower() for a in raw_allergies if a.strip()})
        return clean_d, clean_a
