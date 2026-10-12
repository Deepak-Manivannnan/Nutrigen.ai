/**
 * Nutrigen.ai — Backend API Integration Service
 * Connects frontend with FastAPI multi-agent backend endpoints & SSE streaming.
 */

const API_BASE = '/api';

/**
 * Health check
 */
export async function fetchHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('Backend health check failed:', err);
    return null;
  }
}

/**
 * Fetch current user profile (tries /profile/me, then /profile/1)
 */
export async function fetchCurrentProfile(userId) {
  try {
    const url = userId ? `${API_BASE}/profile/${userId}` : `${API_BASE}/profile/me`;
    let res = await fetch(url);
    if (!res.ok && userId) {
      res = await fetch(`${API_BASE}/profile/me?user_id=${userId}`);
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('Could not fetch profile from backend:', err);
    return null;
  }
}

/**
 * Create user profile
 */
export async function createProfile(profileData) {
  const payload = {
    name: profileData.name || 'Member',
    age: Number(profileData.age) || 28,
    sex: profileData.sex || 'female',
    height_cm: Number(profileData.height_cm) || 168,
    weight_kg: Number(profileData.weight_kg) || 62,
    activity_level: profileData.activity_level || 'medium',
    goal: profileData.goal || 'maintain',
    diet_type: profileData.diet_type || 'veg',
    dislikes: Array.isArray(profileData.dislikes) ? profileData.dislikes : [],
    allergies: Array.isArray(profileData.allergies) ? profileData.allergies : [],
  };

  const res = await fetch(`${API_BASE}/profile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Failed to create profile: ${res.statusText}`);
  return await res.json();
}

/**
 * Update user profile
 */
export async function updateProfile(userId, updates) {
  const payload = { ...updates };
  if (payload.age !== undefined) payload.age = Number(payload.age);
  if (payload.height_cm !== undefined) payload.height_cm = Number(payload.height_cm);
  if (payload.weight_kg !== undefined) payload.weight_kg = Number(payload.weight_kg);

  const res = await fetch(`${API_BASE}/profile/${userId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Failed to update profile: ${res.statusText}`);
  return await res.json();
}

/**
 * Fetch calorie range
 */
export async function fetchCalorieRange(userId) {
  try {
    const res = await fetch(`${API_BASE}/profile/${userId}/calorie-range`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('Could not fetch calorie range:', err);
    return null;
  }
}

/**
 * Fetch all plans for a user
 */
export async function fetchPlans(userId = 1) {
  try {
    const res = await fetch(`${API_BASE}/plans?user_id=${userId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('Could not fetch plans from backend:', err);
    return [];
  }
}

/**
 * Fetch plan by ID
 */
export async function fetchPlanById(planId) {
  const res = await fetch(`${API_BASE}/plans/${planId}`);
  if (!res.ok) throw new Error(`Plan not found: ${res.status}`);
  return await res.json();
}

/**
 * Approve plan (Human-in-the-Loop)
 */
export async function approvePlanApi(planId) {
  const res = await fetch(`${API_BASE}/plans/${planId}/approve`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to approve plan: ${res.status}`);
  return await res.json();
}

/**
 * Delete plan
 */
export async function deletePlanApi(planId) {
  const res = await fetch(`${API_BASE}/plans/${planId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`Failed to delete plan: ${res.status}`);
  return await res.json();
}

/**
 * Rename meal plan
 */
export async function renamePlanApi(planId, newName) {
  const res = await fetch(`${API_BASE}/plans/${planId}/rename`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newName }),
  });
  if (!res.ok) throw new Error(`Failed to rename plan: ${res.status}`);
  return await res.json();
}

/**
 * Send chat message to orchestrator
 */
export async function sendChatMessage(userId, message, taskId, history = []) {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: userId,
      message,
      task_id: taskId,
      history,
    }),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || `Chat request failed with ${res.status}`);
  }
  return await res.json();
}

/**
 * Connect to SSE timeline stream for task
 */
export function connectStreaming(taskId, onEvent, onError) {
  const url = `${API_BASE}/streaming/${taskId}`;
  const es = new EventSource(url);

  es.addEventListener('agent_message', (e) => {
    try {
      const data = JSON.parse(e.data);
      if (onEvent) onEvent(data);
    } catch (err) {
      console.warn('SSE message parse error:', err);
    }
  });

  es.addEventListener('ping', () => {
    // keep-alive heartbeat
  });

  es.onerror = (err) => {
    if (onError) onError(err);
  };

  return es;
}

/**
 * Adapt backend plan structure to frontend presentation model
 */
export function adaptBackendPlan(rawPlan, profile) {
  if (!rawPlan) return null;

  const totalDays = rawPlan.days || 7;
  const daysMap = {};
  for (let i = 1; i <= totalDays; i++) {
    daysMap[i] = [];
  }

  (rawPlan.meals || []).forEach(m => {
    const dayNum = m.day || 1;
    if (!daysMap[dayNum]) daysMap[dayNum] = [];
    const macros = m.macros || {};
    daysMap[dayNum].push({
      id: m.id,
      type: m.meal_type ? (m.meal_type.charAt(0).toUpperCase() + m.meal_type.slice(1)) : 'Meal',
      name: m.recipe_name || 'Healthy Recipe',
      calories: Math.round(m.calories || 0),
      protein: Math.round(macros.protein_g || 0),
      carbs: Math.round(macros.carbs_g || 0),
      fat: Math.round(macros.fat_g || 0),
      locked: Boolean(m.locked),
      source: m.source || 'api',
      ingredients: m.ingredients || [],
      instructions: m.instructions || '',
    });
  });

  const planDays = Object.keys(daysMap).map(dayStr => {
    const dayNum = parseInt(dayStr, 10);
    const dayMeals = daysMap[dayNum];
    const totalCals = dayMeals.reduce((sum, m) => sum + (m.calories || 0), 0);
    return {
      day: dayNum,
      label: `Day ${dayNum}`,
      totalCalories: totalCals || Math.round(rawPlan.calorie_target || 1800),
      meals: dayMeals,
    };
  });

  const planDiet = rawPlan.diet_type || profile?.diet_type || 'veg';
  const dietLabel = planDiet === 'veg'
    ? 'Vegetarian'
    : planDiet === 'non-veg'
      ? 'Non-Vegetarian'
      : planDiet === 'vegan'
        ? 'Vegan'
        : (planDiet.charAt(0).toUpperCase() + planDiet.slice(1));

  return {
    id: rawPlan.id,
    name: rawPlan.name || `${totalDays}-Day Meal Plan`,
    days: totalDays,
    calorieTarget: Math.round(rawPlan.calorie_target || 1800),
    dietType: dietLabel,
    rawDietType: planDiet,
    status: rawPlan.status || 'draft',
    version: rawPlan.version || 1,
    grocery_list: rawPlan.grocery_list || null,
    created_at: rawPlan.created_at,
    updated_at: rawPlan.updated_at,
    approved_at: rawPlan.approved_at,
    planDays,
    meals: rawPlan.meals || [],
  };
}

/**
 * Adapt backend grocery list to frontend presentation model
 */
export function adaptBackendGrocery(rawGrocery, planDays = 7) {
  if (!rawGrocery) return null;
  if (rawGrocery.categories) return rawGrocery; // already in UI format

  const items = rawGrocery.items || [];
  const categories = {};
  const LIQUID_REGEX = /\b(milk|soya milk|soy milk|almond milk|oat milk|juice|water|oil|olive oil|broth|tea|coffee|vinegar|syrup)\b/i;

  items.forEach(item => {
    const rawCat = item.category || 'Produce';
    const catName = rawCat.charAt(0).toUpperCase() + rawCat.slice(1).toLowerCase();
    if (!categories[catName]) categories[catName] = [];

    const isLiquid = LIQUID_REGEX.test(item.name || '');
    let qtyStr = '';
    if (isLiquid) {
      if (item.total_grams >= 1000) {
        qtyStr = `${(item.total_grams / 1000).toFixed(1)} L`;
      } else {
        qtyStr = `${Math.round(item.total_grams)} ml`;
      }
    } else {
      if (item.total_grams >= 1000) {
        qtyStr = `${(item.total_grams / 1000).toFixed(1)} kg`;
      } else {
        qtyStr = `${Math.round(item.total_grams)} g`;
      }
    }

    categories[catName].push({
      name: item.name,
      qty: qtyStr,
      price: Math.round(item.estimated_cost || 0),
      source: item.price_source || item.source || 'ai_estimate',
    });
  });

  return {
    categories,
    total: Math.round(rawGrocery.total_estimated_cost || 0),
    currency: rawGrocery.currency === 'INR' ? '₹' : (rawGrocery.currency || '₹'),
    duration: planDays,
  };
}
