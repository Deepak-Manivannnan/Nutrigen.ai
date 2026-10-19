/**
 * Nutrigen.ai — Backend API Integration Service
 * Connects frontend with FastAPI multi-agent backend endpoints & SSE streaming.
 */
import { isLiquidIngredient } from './planHelpers';
import { TOKEN_KEY, USER_KEY, readSession, writeSession } from './sessionStore';

// Backend origin. `/api` on its own only resolves during `npm run dev`, where
// vite.config.js proxies it to http://127.0.0.1:8000. A production build has no
// such proxy, so VITE_API_BASE must point at the deployed backend (include the
// trailing /api). Vite inlines env vars at BUILD time, so changing it on the
// hosting platform requires a rebuild, not just a restart.
export const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000/api';

// ---------------------------------------------------------------------------
// Session token
//
// The backend identifies the caller from a signed bearer token, so every
// request carries it. Without this header the API answers 401: the user id in
// a request body or URL no longer grants access to anything.
// ---------------------------------------------------------------------------
// TOKEN_KEY comes from sessionStore.js - the session is per-tab, not per
// browser, so closing the tab ends it.

// A 401 from a request that carried a token means the session is no longer
// accepted by the server. With a short token lifetime that is routine, and
// several requests can fail together during one navigation - so the app is
// told once, not once per failed request. A fresh sign-in re-arms it.
let unauthorizedAnnounced = false;

export function getAuthToken() {
  return readSession(TOKEN_KEY) || '';
}

export function setAuthToken(token) {
  writeSession(TOKEN_KEY, token || null);
  if (token) unauthorizedAnnounced = false;
}

export function authFetch(url, options = {}) {
  const token = getAuthToken();
  return fetch(url, {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  }).then(res => {
    // Any 401 from this wrapper means the server did not accept the session:
    // every endpoint behind it answers 401 only for a missing, malformed or
    // expired token, and 403 for anything else. Sign-in and sign-up use plain
    // fetch on purpose, so a wrong password still reaches the form as its own
    // message rather than being mistaken for an expired session.
    //
    // Deliberately not conditioned on a token being present: a cached user
    // with no token - left over from before sessions existed - also has to
    // land on sign-in instead of being bounced to onboarding.
    //
    // It IS conditioned on a cached user existing, so an in-flight request
    // that 401s after a deliberate sign-out does not tell someone who just
    // logged out that their session expired.
    const hasSession = Boolean(readSession(USER_KEY));
    if (res.status === 401 && hasSession && !unauthorizedAnnounced) {
      unauthorizedAnnounced = true;
      window.dispatchEvent(new Event('nutrigen:session-expired'));
    }
    return res;
  });
}

/**
 * Does this profile hold the personal details the app actually needs?
 *
 * The API returns HTTP 200 with an all-null object for an account that exists
 * but has never been through onboarding (age/height/weight are all null). That
 * object is truthy, so a plain `if (profile)` read it as "profile exists" and
 * every route guard waved a brand-new user straight through to the home page,
 * skipping onboarding entirely.
 *
 * These three mirror the backend's own `has_profile` test in auth.py, so the
 * sign-in redirect and the route guards can never disagree. `goal` is
 * deliberately not part of it: the API defaults it to "maintain", so it is set
 * even on an account that was never onboarded.
 */
export function hasCompletedProfile(profile) {
  return Boolean(profile && profile.age && profile.height_cm && profile.weight_kg);
}

/**
 * Fetch current user profile.
 *
 * Returns null when the account has no completed profile - not the empty
 * shell the API sends - so callers can treat null as "still needs onboarding".
 */
export async function fetchCurrentProfile(userId) {
  try {
    const url = userId ? `${API_BASE}/profile/${userId}` : `${API_BASE}/profile/me`;
    const res = await authFetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return hasCompletedProfile(data) ? data : null;
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

  const res = await authFetch(`${API_BASE}/profile`, {
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

  const res = await authFetch(`${API_BASE}/profile/${userId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Failed to update profile: ${res.statusText}`);
  return await res.json();
}

/**
 * Fetch all plans for a user
 */
export async function fetchPlans(userId = 1) {
  try {
    // The backend scopes this to the signed-in user; the id is no longer what
    // grants access, so it is not sent.
    const res = await authFetch(`${API_BASE}/plans`);
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
  const res = await authFetch(`${API_BASE}/plans/${planId}`);
  if (!res.ok) throw new Error(`Plan not found: ${res.status}`);
  return await res.json();
}

/**
 * Approve plan (Human-in-the-Loop)
 */
export async function approvePlanApi(planId) {
  const res = await authFetch(`${API_BASE}/plans/${planId}/approve`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to approve plan: ${res.status}`);
  return await res.json();
}

/**
 * Delete plan
 */
export async function deletePlanApi(planId) {
  const res = await authFetch(`${API_BASE}/plans/${planId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`Failed to delete plan: ${res.status}`);
  return await res.json();
}

/**
 * Rename meal plan
 */
export async function renamePlanApi(planId, newName) {
  const res = await authFetch(`${API_BASE}/plans/${planId}/rename`, {
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
export async function sendChatMessage(userId, message, taskId, history = [], pendingWorkflow = null, justCreatedPlanId = null) {
  const res = await authFetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: userId,
      message,
      task_id: taskId,
      history,
      pending_workflow: pendingWorkflow || null,
      just_created_plan_id: justCreatedPlanId || null,
    }),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || `Chat request failed with ${res.status}`);
  }
  return await res.json();
}

/**
 * Fetch a finished chat response by task_id (delivery inbox, used after a
 * reload to collect a reply whose original request was aborted).
 * Returns the ChatResponse object, or null when not (yet) available.
 */
export async function fetchChatResult(taskId) {
  const res = await authFetch(`${API_BASE}/chat/result/${encodeURIComponent(taskId)}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || `Result request failed with ${res.status}`);
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

  items.forEach(item => {
    const rawCat = item.category || 'Produce';
    const catName = rawCat.charAt(0).toUpperCase() + rawCat.slice(1).toLowerCase();
    if (!categories[catName]) categories[catName] = [];

    const isLiquid = isLiquidIngredient(item.name || '');
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
