import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { generateDemoPlan, generateDemoGrocery } from '../utils/workflowSimulator';
import {
  fetchCurrentProfile,
  updateProfile as updateProfileApi,
  createProfile as createProfileApi,
  fetchPlans as fetchPlansApi,
  approvePlanApi,
  deletePlanApi,
  renamePlanApi,
  adaptBackendPlan,
  adaptBackendGrocery,
} from '../utils/api';

const AppContext = createContext(null);

const DEFAULT_USER = {
  id: 1,
  name: 'Deepak',
  email: 'deepak@nutrigen.ai',
};

const DEFAULT_PROFILE = {
  id: 1,
  name: 'Deepak',
  age: 26,
  sex: 'male',
  height_cm: 178,
  weight_kg: 72,
  activity_level: 'medium',
  goal: 'maintain',
  diet_type: 'veg',
  allergies: [],
  dislikes: ['Bitter gourd'],
  created_at: new Date().toISOString(),
};

function getStoredUser() {
  try {
    const raw = localStorage.getItem('nutrigen_auth_user');
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function loadPendingWorkflow(userId) {
  if (!userId) return null;
  try {
    const raw = localStorage.getItem(`nutrigen_pending_workflow_${userId}`);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function savePendingWorkflow(userId, wf) {
  if (!userId) return;
  try {
    if (wf) {
      localStorage.setItem(`nutrigen_pending_workflow_${userId}`, JSON.stringify(wf));
    } else {
      localStorage.removeItem(`nutrigen_pending_workflow_${userId}`);
    }
  } catch {
    // ignore storage failures
  }
}

// Chat messages live in browser localStorage (per user, device-local — never
// the database) so the conversation survives a page refresh. They are wiped
// only by Clear chat or sign-out, mirroring the pending workflow.
const MAX_STORED_MESSAGES = 100;

function loadMessages(userId) {
  if (!userId) return [];
  try {
    const raw = localStorage.getItem(`nutrigen_messages_${userId}`);
    const arr = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(arr)) return [];
    return arr.filter(m => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string');
  } catch {
    return [];
  }
}

function saveMessages(userId, msgs) {
  if (!userId) return;
  try {
    const tail = (msgs || []).slice(-MAX_STORED_MESSAGES);
    if (tail.length) {
      localStorage.setItem(`nutrigen_messages_${userId}`, JSON.stringify(tail));
    } else {
      localStorage.removeItem(`nutrigen_messages_${userId}`);
    }
  } catch {
    // ignore storage failures (quota) — chat keeps working in memory
  }
}

// In-flight delivery marker: { taskId, sentAt }. Written when a message is
// sent, cleared when its reply (or a terminal failure) is applied. On boot,
// a leftover marker means "reload happened mid-run" → reconcile via inbox.
export function loadInflight(userId) {
  if (!userId) return null;
  try {
    const raw = localStorage.getItem(`nutrigen_inflight_${userId}`);
    const inf = raw ? JSON.parse(raw) : null;
    if (inf && typeof inf.taskId === 'string' && inf.taskId) return inf;
    return null;
  } catch {
    return null;
  }
}

export function saveInflight(userId, inflight) {
  if (!userId) return;
  try {
    if (inflight && inflight.taskId) {
      localStorage.setItem(`nutrigen_inflight_${userId}`, JSON.stringify(inflight));
    } else {
      localStorage.removeItem(`nutrigen_inflight_${userId}`);
    }
  } catch {
    // ignore storage failures
  }
}

export function AppProvider({ children }) {
  // Auth & profile state
  const stored = getStoredUser();
  const [user, setUser] = useState(stored ? { id: stored.id, name: stored.name, email: stored.email } : DEFAULT_USER);
  const [profile, setProfile] = useState(stored?.profile || DEFAULT_PROFILE);

  // App navigation badges
  const [plansBadge, setPlansBadge] = useState(false);
  const [groceryBadge, setGroceryBadge] = useState(false);

  // Plans data
  const [plans, setPlans] = useState([]);
  const [activePlanId, setActivePlanId] = useState(null);

  // Grocery data
  const [groceryList, setGroceryList] = useState(null);

  // Chat state
  // Messages persist per user in localStorage so a page refresh keeps the
  // conversation (deleted only via Clear chat or sign-out).
  const [messages, setMessages] = useState(() => loadMessages(stored ? stored.id : null));
  const [isAgentRunning, setIsAgentRunning] = useState(false);
  const [workflowState, setWorkflowState] = useState(null);
  const [chatStatus, setChatStatus] = useState(null);
  const [showWorkflow, setShowWorkflow] = useState(false);
  // Pending chat workflow (e.g. an unanswered diet/calorie confirmation or an
  // in-progress plan update). Kept at the app level (and persisted per user) so
  // it survives navigation between tabs - and even a page refresh - so the user
  // can continue exactly where they left off.
  const [pendingWorkflow, setPendingWorkflowState] = useState(() =>
    loadPendingWorkflow(stored ? stored.id : null)
  );

  const setPendingWorkflow = useCallback((wf) => {
    const uid = user?.id || profile?.id;
    setPendingWorkflowState(wf);
    if (uid) savePendingWorkflow(uid, wf);
  }, [user?.id, profile?.id]);

  // Persist chat messages on every change (per user). Transient run state
  // (typing indicator, running flag) is intentionally NOT persisted.
  useEffect(() => {
    const uid = user?.id || profile?.id;
    if (uid) saveMessages(uid, messages);
  }, [messages, user?.id, profile?.id]);

  // Toasts
  const [toasts, setToasts] = useState([]);

  const addToast = useCallback((message, type = 'default') => {
    const id = Date.now() + Math.random();
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 3500);
  }, []);

  // Universal helper to load user profile and user's meal plans from the backend database
  const loadUserData = useCallback(async (userId, optProfile = null) => {
    if (!userId) {
      console.warn('loadUserData called without userId');
      return;
    }
    try {
      let liveProfile = optProfile;
      if (!liveProfile) {
        liveProfile = await fetchCurrentProfile(userId);
      }

      if (liveProfile) {
        setProfile(liveProfile);
        const resolvedUser = {
          id: liveProfile.id,
          name: liveProfile.name,
          email: liveProfile.email || `${liveProfile.name.toLowerCase().replace(/\s+/g, '')}@nutrigen.ai`,
        };
        setUser(resolvedUser);
        localStorage.setItem('nutrigen_auth_user', JSON.stringify({ ...resolvedUser, profile: liveProfile }));

        // Fetch persisted meal plans for THIS specific user
        const livePlans = await fetchPlansApi(liveProfile.id);
        if (livePlans && livePlans.length > 0) {
          const validPlans = livePlans.filter(p => p.days && p.days > 0);
          if (validPlans.length > 0) {
            const adaptedPlans = validPlans.map(p => adaptBackendPlan(p, liveProfile));
            setPlans(adaptedPlans);
            setActivePlanId(adaptedPlans[0].id);

            if (adaptedPlans[0].grocery_list) {
              const adaptedGrocery = adaptBackendGrocery(adaptedPlans[0].grocery_list, adaptedPlans[0].days);
              if (adaptedGrocery) setGroceryList(adaptedGrocery);
            } else {
              setGroceryList(null);
            }
            return;
          }
        }
      }

      // No plans found for this user in database
      setPlans([]);
      setActivePlanId(null);
      setGroceryList(null);
    } catch (err) {
      console.warn('Backend sync initialized with fallback data:', err);
    }
  }, []);

  // Initial load upon startup / refresh
  useEffect(() => {
    const savedUser = getStoredUser();
    if (savedUser) {
      loadUserData(savedUser.id, savedUser.profile);
    }
  }, [loadUserData]);

  const signIn = useCallback(async (userData) => {
    try {
      setUser(userData);
      localStorage.setItem('nutrigen_auth_user', JSON.stringify(userData));
      setPendingWorkflowState(loadPendingWorkflow(userData.id));
      setMessages(loadMessages(userData.id));
      await loadUserData(userData.id, userData.profile);
    } catch (err) {
      console.warn('Sign in sync error:', err);
    }
  }, [loadUserData]);

  const signOut = useCallback(() => {
    if (user?.id) savePendingWorkflow(user.id, null);
    if (user?.id) saveMessages(user.id, null);
    if (user?.id) saveInflight(user.id, null);
    localStorage.removeItem('nutrigen_auth_user');
    setUser(null);
    setProfile(null);
    setMessages([]);
    setPlans([]);
    setGroceryList(null);
    setPlansBadge(false);
    setGroceryBadge(false);
    setWorkflowState(null);
    setIsAgentRunning(false);
    setChatStatus(null);
    setShowWorkflow(false);
    setPendingWorkflowState(null);
    setActivePlanId(null);
  }, [user?.id]);

  const createProfile = useCallback(async (profileData) => {
    try {
      // If user already signed up with an account (has user.id), update that account directly
      if (user?.id) {
        const saved = await updateProfileApi(user.id, profileData);
        if (saved) {
          setProfile(saved);
          const updatedUser = { ...user, name: saved.name, profile: saved };
          setUser(updatedUser);
          localStorage.setItem('nutrigen_auth_user', JSON.stringify(updatedUser));
          await loadUserData(user.id, saved);
          return saved;
        }
      }

      // Otherwise create a new profile record in DB
      const created = await createProfileApi(profileData);
      setProfile(created);
      const newUser = {
        id: created.id,
        name: created.name,
        email: created.email || `${created.name.toLowerCase().replace(/\s+/g, '')}@nutrigen.ai`,
      };
      setUser(newUser);
      localStorage.setItem('nutrigen_auth_user', JSON.stringify({ ...newUser, profile: created }));
      await loadUserData(created.id, created);
      return created;
    } catch (err) {
      console.warn('Backend create profile error, falling back locally:', err);
      const newProfile = {
        ...profileData,
        id: Date.now(),
        created_at: new Date().toISOString(),
      };
      setProfile(newProfile);
      return newProfile;
    }
  }, [user, loadUserData]);

  const updateProfile = useCallback(async (updates) => {
    // Optimistic UI update
    setProfile(prev => ({ ...prev, ...updates }));

    const activeId = profile?.id || user?.id;
    if (!activeId) return;

    try {
      const saved = await updateProfileApi(activeId, updates);
      if (saved) {
        setProfile(saved);
        setUser(prev => {
          const updated = { ...prev, name: saved.name || prev.name };
          localStorage.setItem('nutrigen_auth_user', JSON.stringify({ ...updated, profile: saved }));
          return updated;
        });
      }
    } catch (err) {
      console.warn('Backend update profile error:', err);
    }
  }, [profile?.id, user?.id]);

  const addPlan = useCallback((plan) => {
    setPlans(prev => [plan, ...prev.filter(p => p.id !== plan.id)]);
    setActivePlanId(plan.id);
    setPlansBadge(true);
  }, []);

  const approvePlan = useCallback(async (planId) => {
    try {
      const approvedBackendPlan = await approvePlanApi(planId);
      if (approvedBackendPlan) {
        const adapted = adaptBackendPlan(approvedBackendPlan, profile);
        setPlans(prev =>
          prev.map(p => (p.id === planId ? adapted : p))
        );
        if (approvedBackendPlan.grocery_list) {
          const grocery = adaptBackendGrocery(approvedBackendPlan.grocery_list, approvedBackendPlan.days);
          if (grocery) setGroceryList(grocery);
        }
      }
    } catch (err) {
      console.warn('Backend plan approval fallback:', err);
      setPlans(prev =>
        prev.map(p => (p.id === planId ? { ...p, status: 'approved' } : p))
      );
      const newGrocery = generateDemoGrocery();
      setGroceryList(newGrocery);
    }
    setGroceryBadge(true);
    addToast('Meal plan approved! Grocery list prepared.', 'success');
  }, [profile, addToast]);

  const renamePlan = useCallback(async (planId, newName) => {
    setPlans(prev =>
      prev.map(p => (p.id === planId ? { ...p, name: newName } : p))
    );
    try {
      await renamePlanApi(planId, newName);
      addToast('Plan renamed successfully', 'success');
    } catch (err) {
      console.warn('Backend rename plan error:', err);
      addToast('Failed to rename plan on server', 'error');
    }
  }, [addToast]);

  const refreshPlans = useCallback(async () => {
    const activeId = profile?.id || user?.id || 1;
    await loadUserData(activeId, profile);
  }, [profile, user?.id, loadUserData]);

  const deletePlan = useCallback(async (planId) => {
    setPlans(prev => {
      const remaining = prev.filter(p => p.id !== planId);
      if (activePlanId === planId) {
        setActivePlanId(remaining.length > 0 ? remaining[0].id : null);
      }
      if (remaining.length === 0) {
        setGroceryList(null);
        setPlansBadge(false);
        setGroceryBadge(false);
      }
      return remaining;
    });
    try {
      await deletePlanApi(planId);
      addToast('Meal plan deleted successfully', 'default');
    } catch (err) {
      console.warn('Backend delete plan error:', err);
      addToast('Failed to delete meal plan from server', 'error');
    }
  }, [activePlanId, addToast]);

  const setGrocery = useCallback((grocery) => {
    setGroceryList(grocery);
    setGroceryBadge(true);
  }, []);

  const addMessage = useCallback((msg) => {
    setMessages(prev => [...prev, { ...msg, id: Date.now() + Math.random(), timestamp: new Date() }]);
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setWorkflowState(null);
    setChatStatus(null);
    setShowWorkflow(false);
    setIsAgentRunning(false);
    setPendingWorkflowState(null);
    if (user?.id || profile?.id) savePendingWorkflow(user?.id || profile?.id, null);
    if (user?.id || profile?.id) saveMessages(user?.id || profile?.id, null);
    if (user?.id || profile?.id) saveInflight(user?.id || profile?.id, null);
  }, [user?.id, profile?.id]);

  const value = {
    user, setUser, profile, setProfile,
    plansBadge, setPlansBadge,
    groceryBadge, setGroceryBadge,
    plans, addPlan, setPlans, approvePlan, deletePlan, renamePlan, refreshPlans,
    activePlanId, setActivePlanId,
    groceryList, setGrocery,
    messages, addMessage, clearMessages,
    isAgentRunning, setIsAgentRunning,
    workflowState, setWorkflowState,
    chatStatus, setChatStatus,
    showWorkflow, setShowWorkflow,
    pendingWorkflow, setPendingWorkflow,
    toasts, addToast,
    signIn, signOut,
    createProfile, updateProfile,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}
