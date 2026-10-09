import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from backend.app.config import settings

# In-memory runtime caches to avoid redundant network calls
_price_cache: Dict[str, Dict[str, Any]] = {}
_ceda_commodities_cache: List[Dict[str, Any]] = []

async def _get_ceda_commodities(api_key: str) -> List[Dict[str, Any]]:
    global _ceda_commodities_cache
    if _ceda_commodities_cache:
        return _ceda_commodities_cache

    url = "https://api.ceda.ashoka.edu.in/v1/agmarknet/commodities"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json().get("output", {}).get("data", [])
                _ceda_commodities_cache = data
                return _ceda_commodities_cache
    except Exception:
        pass
    return []

def _find_matching_commodity(item_name: str, commodities: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    clean = item_name.strip().lower()
    
    # 1. Exact match
    for c in commodities:
        if c.get("commodity_name", "").lower() == clean:
            return c
            
    # 2. Substring match (e.g. "tomato" in "Tomato", "wheat" in "Wheat Atta")
    for c in commodities:
        c_name = c.get("commodity_name", "").lower()
        if clean in c_name or c_name in clean:
            return c

    return None

async def _fetch_ceda_price(commodity_id: int, api_key: str, markup: float) -> Optional[float]:
    url = "https://api.ceda.ashoka.edu.in/v1/agmarknet/prices"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    now = datetime.now()
    # 1. Primary: Adaptive Rolling Window ending on current date (past 365 days)
    dynamic_payload = {
        "commodity_id": commodity_id,
        "state_id": 0,  # All India national level
        "from_date": (now - timedelta(days=365)).strftime("%Y-%m-%d"),
        "to_date": now.strftime("%Y-%m-%d")
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, headers=headers, json=dynamic_payload)
            records = resp.json().get("output", {}).get("data", []) if resp.status_code == 200 else []

            # 2. Resilient Fallback: If CEDA has not ingested recent 2026 data yet (returns 0 records),
            # automatically fallback to the latest verified historical dataset window ("2024-01-01" to "2025-12-31")
            if not records:
                fallback_payload = {
                    "commodity_id": commodity_id,
                    "state_id": 0,
                    "from_date": "2024-01-01",
                    "to_date": "2025-12-31"
                }
                resp_fallback = await client.post(url, headers=headers, json=fallback_payload)
                if resp_fallback.status_code == 200:
                    records = resp_fallback.json().get("output", {}).get("data", [])

            if records:
                latest = records[-1]
                max_price_quintal = float(latest.get("max_price") or latest.get("modal_price") or 0.0)
                if max_price_quintal > 0:
                    # 1 Quintal = 100 kg
                    price_per_kg = (max_price_quintal / 100.0) * markup
                    return round(price_per_kg, 2)
    except Exception:
        pass
    return None

async def get_price(item_name: str) -> Dict[str, Any]:
    """
    Looks up commodity wholesale prices from:
    1. CEDA Agri Market Data API (Agmarknet wrapper)
    2. Mandi / Agmarknet (data.gov.in) API
    
    Converts quintal (100kg) to kg and applies configured retail markup.
    If unavailable or not found, returns found=False so the Shopper Agent can
    observe missing data and supply an AI estimate labeled 'ai_estimate'.
    """
    clean_name = item_name.strip().lower()
    if not clean_name:
        return {"item": item_name, "found": False, "price_per_kg": 0.0, "price_source": None}

    if clean_name in _price_cache:
        return _price_cache[clean_name]

    markup = settings.mandi_retail_markup_factor

    # Stage 1: Try data.gov.in Mandi API (if configured and working)
    if settings.data_gov_in_api_key:
        try:
            url = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
            params = {
                "api-key": settings.data_gov_in_api_key,
                "format": "json",
                "filters[commodity]": clean_name.capitalize(),
                "limit": 1
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    records = resp.json().get("records", [])
                    if records:
                        max_price_quintal = float(records[0].get("max_price", 0))
                        if max_price_quintal > 0:
                            price_per_kg = (max_price_quintal / 100.0) * markup
                            result = {
                                "item": item_name,
                                "matched_commodity": clean_name.capitalize(),
                                "found": True,
                                "price_per_kg": round(price_per_kg, 2),
                                "price_source": "mandi_api"
                            }
                            _price_cache[clean_name] = result
                            return result
        except Exception:
            pass

    # Stage 2: Try CEDA Agri Market API (Agmarknet wrapper)
    if settings.ceda_api_key:
        commodities = await _get_ceda_commodities(settings.ceda_api_key)
        matched = _find_matching_commodity(clean_name, commodities)
        if matched:
            price_kg = await _fetch_ceda_price(matched["commodity_id"], settings.ceda_api_key, markup)
            if price_kg and price_kg > 0:
                result = {
                    "item": item_name,
                    "matched_commodity": matched.get("commodity_name"),
                    "found": True,
                    "price_per_kg": price_kg,
                    "price_source": "mandi_api"
                }
                _price_cache[clean_name] = result
                return result

    # Stage 3: If unavailable or not found in both sources, return found=False
    # for the Shopper Agent to use LLM knowledge (AI price estimation)
    not_found = {
        "item": item_name,
        "found": False,
        "price_per_kg": 0.0,
        "price_source": None
    }
    _price_cache[clean_name] = not_found
    return not_found
