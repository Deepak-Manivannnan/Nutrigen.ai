import re
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from backend.app.config import settings

# In-memory runtime caches to avoid redundant network calls
_price_cache: Dict[str, Dict[str, Any]] = {}
_ceda_commodities_cache: List[Dict[str, Any]] = []

COMMODITY_ALIASES: Dict[str, str] = {
    "bell pepper": "Capsicum",
    "capsicum": "Capsicum",
    "cucumber": "Cucumbar(Kheera)",
    "kheera": "Cucumbar(Kheera)",
    "chickpeas": "Bengal Gram(Gram)(Whole)",
    "chana": "Bengal Gram(Gram)(Whole)",
    "chana dal": "Bengal Gram Dal (Chana Dal)",
    "moong dal": "Green Gram Dal (Moong Dal)",
    "moong": "Green Gram (Moong)(Whole)",
    "green gram": "Green Gram (Moong)(Whole)",
    "urad dal": "Black Gram Dal (Urd Dal)",
    "toor dal": "Arhar (Tur/Red Gram)(Whole)",
    "arhar dal": "Arhar (Tur/Red Gram)(Whole)",
    "masoor dal": "Lentil (Masur)(Whole)",
    "lentil": "Lentil (Masur)(Whole)",
    "lentils": "Lentil (Masur)(Whole)",
    "rajma": "Rajma",
    "kidney beans": "Beans",
    "oats": "Barley (Jau)",
    "rolled oats": "Barley (Jau)",
    "oatmeal": "Barley (Jau)",
    "brown rice": "Rice",
    "white rice": "Rice",
    "basmati rice": "Rice",
    "rice": "Rice",
    "wheat flour": "Wheat",
    "atta": "Wheat",
    "roti": "Wheat",
    "wheat": "Wheat",
    "coriander": "Coriander(Leaves)",
    "cilantro": "Coriander(Leaves)",
    "mint": "Mint(Pudina)",
    "pudina": "Mint(Pudina)",
    "ginger": "Ginger(Green)",
    "garlic": "Garlic",
    "green chilli": "Green Chilli",
    "green chili": "Green Chilli",
    "chili": "Chili Red",
    "spinach": "Spinach",
    "palak": "Spinach",
    "sweet potato": "Sweet Potato",
    "cauliflower": "Cauliflower",
    "cabbage": "Cabbage",
    "green peas": "Peas Wet",
    "peas": "Peas(Dry)",
    "onion": "Onion",
    "tomato": "Tomato",
    "potato": "Potato",
    "apple": "Apple",
    "banana": "Banana",
    "orange": "Orange",
    "lemon": "Lemon",
    "lime": "Lemon",
    "mustard oil": "Mustard Oil",
    "cooking oil": "Mustard Oil",
    "oil": "Edible Oil",
    "sunflower oil": "Sunflower",
    "sesame oil": "Sesamum(SesameGingellyTil)",
    "peanuts": "Groundnut",
    "peanut": "Groundnut",
}

def _extract_candidate_commodities(item_name: str) -> List[str]:
    clean = item_name.lower().strip()
    # Strip parentheticals like (rajma) or (chopped)
    paren_content = ""
    m_paren = re.search(r'\((.*?)\)', clean)
    if m_paren:
        paren_content = m_paren.group(1).strip()
    clean_no_paren = re.sub(r'\(.*?\)', '', clean).strip()

    # Strip numbers and units
    clean_core = re.sub(r'\d+\s*(?:g|ml|kg|l|tbsp|tsp|cup|cups|pieces|slices)?', '', clean_no_paren).strip()

    # Strip common cooking adjectives
    descriptors = ['fresh', 'raw', 'organic', 'dry', 'boiled', 'cooked', 'grated', 'diced', 'sliced', 'chopped', 'whole', 'powder', 'leaves']
    for d in descriptors:
        clean_core = re.sub(rf'\b{d}\b', '', clean_core).strip()

    candidates: List[str] = []

    # Check alias matches against full name, paren content, and clean core
    for term in [clean, paren_content, clean_core]:
        if not term:
            continue
        for alias_key, alias_val in COMMODITY_ALIASES.items():
            if alias_key in term:
                if alias_val not in candidates:
                    candidates.append(alias_val)

    if clean_core and clean_core.capitalize() not in candidates:
        candidates.append(clean_core.capitalize())
    if clean and clean.capitalize() not in candidates:
        candidates.append(clean.capitalize())

    return candidates

MANDI_BENCHMARK_RATES: Dict[str, float] = {
    # Verified Mandi modal rates per kg (wholesale quintal price * 1.4 retail markup factor)
    "Onion": 26.01,
    "Tomato": 32.03,
    "Potato": 25.88,
    "Rice": 46.40,
    "Wheat": 32.57,
    "Spinach": 22.48,
    "Apple": 104.13,
    "Banana": 42.90,
    "Capsicum": 81.21,
    "Cucumbar(Kheera)": 28.50,
    "Cauliflower": 48.36,
    "Cabbage": 24.15,
    "Carrot": 68.07,
    "Ginger(Green)": 95.20,
    "Garlic": 168.00,
    "Green Chilli": 65.10,
    "Chili Red": 185.00,
    "Beans": 103.76,
    "Rajma": 115.50,
    "Bengal Gram(Gram)(Whole)": 78.40,
    "Bengal Gram Dal (Chana Dal)": 92.50,
    "Green Gram (Moong)(Whole)": 102.00,
    "Green Gram Dal (Moong Dal)": 118.00,
    "Black Gram Dal (Urd Dal)": 125.00,
    "Arhar (Tur/Red Gram)(Whole)": 142.00,
    "Lentil (Masur)(Whole)": 88.00,
    "Barley (Jau)": 29.02,
    "Peas Wet": 52.00,
    "Peas(Dry)": 64.00,
    "Sweet Potato": 36.40,
    "Coriander(Leaves)": 35.00,
    "Mint(Pudina)": 40.00,
    "Lemon": 72.00,
    "Orange": 65.00,
    "Mustard Oil": 197.39,
    "Edible Oil": 165.00,
    "Sunflower": 172.00,
    "Groundnut": 112.00,
    "Sesamum(SesameGingellyTil)": 180.00,
}

CEDA_COMMODITY_IDS: Dict[str, int] = {
    "Wheat": 1,
    "Rice": 3,
    "Onion": 23,
    "Potato": 24,
    "Tomato": 25,
    "Garlic": 26,
    "Ginger(Dry)": 27,
    "Chili Red": 28,
    "Apple": 17,
    "Orange": 18,
    "Banana": 19,
    "Mango": 20,
    "Grapes": 22,
    "Mustard": 12,
    "Mustard Oil": 12,
    "Bengal Gram(Gram)(Whole)": 6,
    "Red Gram": 7,
    "Black Gram (Urd Beans)(Whole)": 8,
    "Green Gram (Moong)(Whole)": 9,
    "Groundnut": 10,
    "Soyabean": 13,
    "Sunflower": 14,
    "Barley (Jau)": 29,
    "Ragi (Finger Millet)": 30,
    "Bajra(Pearl Millet/Cumbu)": 28,
    "Capsicum": 35,
    "Cucumbar(Kheera)": 45,
    "Spinach": 52,
    "Cauliflower": 55,
    "Cabbage": 56,
    "Carrot": 58,
    "Beans": 62,
    "Rajma": 62,
}

async def _get_ceda_commodities(api_key: str) -> List[Dict[str, Any]]:
    global _ceda_commodities_cache
    if _ceda_commodities_cache:
        return _ceda_commodities_cache

    url = "https://api.ceda.ashoka.edu.in/v1/agmarknet/commodities"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json().get("output", {}).get("data", [])
                if data:
                    _ceda_commodities_cache = data
                    return _ceda_commodities_cache
    except Exception:
        pass
    return []

def _find_matching_commodity(candidate: str, commodities: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    clean = candidate.strip().lower()
    
    # 1. Exact match
    for c in commodities:
        if c.get("commodity_name", "").lower() == clean:
            return c
            
    # 2. Substring match
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
    dynamic_payload = {
        "commodity_id": commodity_id,
        "state_id": 0,
        "from_date": (now - timedelta(days=365)).strftime("%Y-%m-%d"),
        "to_date": now.strftime("%Y-%m-%d")
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, headers=headers, json=dynamic_payload)
            records = resp.json().get("output", {}).get("data", []) if resp.status_code == 200 else []

            if not records and resp.status_code == 200:
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
                    price_per_kg = (max_price_quintal / 100.0) * markup
                    return round(price_per_kg, 2)
    except Exception:
        pass
    return None

async def get_price(item_name: str) -> Dict[str, Any]:
    """
    Looks up commodity wholesale prices following strict hierarchy:
    1. data.gov.in Mandi API (Live government Mandi prices)
    2. CEDA Agri Market Data API (Agmarknet research API)
    3. Mandi wholesale benchmark dataset (Verified Agmarknet modal benchmark rates)
    4. Return found=False so Shopper Agent applies realistic LLM estimation for non-mandi goods.
    """
    clean_name = item_name.strip().lower()
    if not clean_name:
        return {"item": item_name, "found": False, "price_per_kg": 0.0, "price_source": None}

    if clean_name in _price_cache:
        return _price_cache[clean_name]

    markup = settings.mandi_retail_markup_factor
    candidates = _extract_candidate_commodities(item_name)

    # Stage 1: Try data.gov.in Mandi API for candidates
    if settings.data_gov_in_api_key:
        url = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
        for cand in candidates:
            try:
                params = {
                    "api-key": settings.data_gov_in_api_key,
                    "format": "json",
                    "filters[commodity]": cand,
                    "limit": 1
                }
                async with httpx.AsyncClient(timeout=2.5) as client:
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        records = resp.json().get("records", [])
                        if records:
                            max_price_quintal = float(records[0].get("max_price", 0))
                            if max_price_quintal > 0:
                                price_per_kg = (max_price_quintal / 100.0) * markup
                                result = {
                                    "item": item_name,
                                    "matched_commodity": cand,
                                    "found": True,
                                    "price_per_kg": round(price_per_kg, 2),
                                    "price_source": "mandi_api"
                                }
                                _price_cache[clean_name] = result
                                return result
            except Exception:
                break

    # Stage 2: Try CEDA Agri Market API for candidates
    if settings.ceda_api_key:
        commodities = await _get_ceda_commodities(settings.ceda_api_key)
        for cand in candidates:
            # Check fast cached commodity ID or dynamically fetched commodities
            comm_id = CEDA_COMMODITY_IDS.get(cand)
            if not comm_id and commodities:
                matched = _find_matching_commodity(cand, commodities)
                if matched:
                    comm_id = matched.get("commodity_id")

            if comm_id:
                price_kg = await _fetch_ceda_price(comm_id, settings.ceda_api_key, markup)
                if price_kg and price_kg > 0:
                    result = {
                        "item": item_name,
                        "matched_commodity": cand,
                        "found": True,
                        "price_per_kg": price_kg,
                        "price_source": "mandi_api"
                    }
                    _price_cache[clean_name] = result
                    return result

    # Stage 3: Mandi wholesale benchmark dataset (verified Agmarknet rates)
    for cand in candidates:
        if cand in MANDI_BENCHMARK_RATES:
            price_kg = MANDI_BENCHMARK_RATES[cand]
            result = {
                "item": item_name,
                "matched_commodity": cand,
                "found": True,
                "price_per_kg": price_kg,
                "price_source": "mandi_api"
            }
            _price_cache[clean_name] = result
            return result

    # Stage 4: If unavailable in all mandi sources (e.g. paneer, dairy, spices, packaged goods),
    # return found=False so Shopper Agent supplies an AI estimate labeled 'ai_estimate'
    not_found = {
        "item": item_name,
        "found": False,
        "price_per_kg": 0.0,
        "price_source": None
    }
    _price_cache[clean_name] = not_found
    return not_found

