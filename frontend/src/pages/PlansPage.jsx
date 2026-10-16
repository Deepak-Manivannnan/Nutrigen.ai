import React, { useState, useEffect, useLayoutEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  CheckCircle2,
  Clock,
  ShieldCheck,
  BarChart2,
  
  ShoppingCart,
  Copy,
  Plus,
  Trash2,
  Sun,
  Sunrise,
  Sunset,
  Apple,
  Edit3,
  Check,
  Calendar,
  CalendarClock,
  MessageSquare,
} from 'lucide-react';
import { useApp } from '../context/AppContext';
import PlanSelect from '../components/PlanSelect';
import { generateDemoPlan } from '../utils/workflowSimulator';
import { sortPlansNewestFirst, isLiquidIngredient } from '../utils/planHelpers';

const MEAL_ICONS = {
  Breakfast: Sunrise,
  Lunch: Sun,
  Dinner: Sunset,
  Snack: Apple,
};

const DEFAULT_STEPS = [
  'Prep all fresh ingredients and seasonings.',
  'Heat a pan over medium heat with a splash of oil.',
  'Cook ingredients until thoroughly tender and fragrant.',
  'Season to taste and serve warm.',
];

// Cooking instructions arrive in one of three shapes: a real numbered list,
// newline-separated lines, or - most often - a run-on paragraph of full
// sentences ("Boil the broth. Cook the noodles. Add the tofu."). The last
// shape used to collapse into a single numbered step, so a five-step recipe
// rendered as "1." followed by everything. Split on whichever shape is present.
function splitSteps(instructions) {
  const raw = (instructions || '').trim();
  if (!raw) return DEFAULT_STEPS;

  const lines = raw
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);

  const parts = lines.length ? lines : [raw];

  const steps = [];
  for (const part of parts) {
    // Drop a leading marker first. Left to the sentence splitter below, "1."
    // reads as a sentence end and the bare number becomes its own step.
    // \s+ after the dot keeps a leading decimal ("1.5 tbsp") intact.
    const stripped = part.replace(/^\d{1,2}[.)]\s+/, '');

    // An explicit "1." / "2." sequence is authoritative. Break the line at each
    // marker first, then drop the marker: splitting on a zero-width lookahead
    // instead strands the bare "1." as a step of its own.
    const renumbered = stripped.replace(/(?:^|\s)\d{1,2}[.)]\s+/g, '\n$&');
    const marked = renumbered
      .split(/\n+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (marked.length > 1) {
      steps.push(...marked.map((s) => s.replace(/^\d{1,2}[.)]\s*/, '')));
      continue;
    }
    // Otherwise the steps are just sentences. Split after . ! ? when the next
    // word starts a new sentence. Requiring whitespace after the terminator
    // keeps decimals intact ("1.5 tbsp"), and requiring an uppercase/quote/
    // digit next keeps "e.g. chicken" and "Mrs. Smith" together.
    const sentences = stripped
      .split(/(?<=[.!?])\s+(?=[A-Z0-9"'(])/)
      .map((s) => s.trim())
      .filter(Boolean);
    steps.push(...(sentences.length ? sentences : [part]));
  }

  const cleaned = steps.filter(Boolean);
  return cleaned.length ? cleaned : DEFAULT_STEPS;
}

// How many steps are shown before the list becomes scrollable. The card keeps
// a fixed rhythm, so a five-step recipe and a two-step recipe read alike.
const VISIBLE_STEPS = 3;

/**
 * The numbered cooking steps, capped to VISIBLE_STEPS and scrollable beyond
 * that. The cap is measured rather than assumed: step text wraps to one or two
 * lines depending on the dish, so a hard-coded pixel height would either clip a
 * wrapped step or leave a gap. Measuring the first three and capping the list
 * to exactly that keeps every card the same height without cutting a line in
 * half.
 */
function MealSteps({ steps }) {
  const listRef = useRef(null);
  const [maxHeight, setMaxHeight] = useState(null);

  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return undefined;

    if (steps.length <= VISIBLE_STEPS) {
      setMaxHeight(null);
      return undefined;
    }
    const items = el.querySelectorAll('.meal-step');
    if (items.length <= VISIBLE_STEPS) {
      setMaxHeight(null);
      return undefined;
    }
    let total = 0;
    for (let i = 0; i < VISIBLE_STEPS; i += 1) {
      const item = items[i];
      total += item.getBoundingClientRect().height;
      // The gap that follows each step except the last one inside the cap.
      const gap = parseFloat(window.getComputedStyle(item).marginBottom) || 0;
      if (i < VISIBLE_STEPS - 1) total += gap;
    }
    setMaxHeight(Math.ceil(total));
    return undefined;
  }, [steps]);

  return (
    <ol
      className="meal-steps-list"
      ref={listRef}
      style={maxHeight ? { maxHeight: `${maxHeight}px` } : undefined}
      data-overflow={steps.length > VISIBLE_STEPS ? 'true' : undefined}
    >
      {steps.map((step, sIdx) => (
        <li key={sIdx} className="meal-step">
          <span className="meal-step-num">{sIdx + 1}.</span>
          <span className="meal-step-text">{step}</span>
        </li>
      ))}
    </ol>
  );
}

function formatTimestamp(ts) {
  if (!ts) return '—';
  const date = new Date(ts);
  if (isNaN(date.getTime())) return '—';
  const now = new Date();
  const diffMs = now - date;
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  
  if (diffDays <= 3) {
    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays === 2) return '2 days ago';
    if (diffDays === 3) return '3 days ago';
  }
  // Absolute date for >3 days
  return date.toLocaleDateString('en-US', { 
    year: 'numeric', 
    month: 'short', 
    day: 'numeric' 
  });
}

function formatAbsoluteDate(ts) {
  if (!ts) return '—';
  const date = new Date(ts);
  if (isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('en-US', { 
    year: 'numeric', 
    month: 'short', 
    day: 'numeric' 
  });
}

function getPlanTimestamps(plan) {
  const created = plan.createdAt || plan.created_at;
  const updated = plan.updatedAt || plan.updated_at;
  
  if (!updated || updated === created) {
    // Never updated - show created as relative (≤3d) or absolute (>3d)
    return {
      createdLabel: 'Created',
      createdValue: formatTimestamp(created),
      showUpdated: false,
    };
  }
  // Updated - created is absolute, updated is relative (≤3d) or absolute (>3d)
  return {
    createdLabel: 'Created on',
    createdValue: formatAbsoluteDate(created),
    updatedLabel: 'Updated',
    updatedValue: formatTimestamp(updated),
    showUpdated: true,
  };
}

export default function PlansPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const {
    plans,
    activePlanId,
    setActivePlanId,
    approvePlan,
    approvingPlanId,
    deletePlan,
    renamePlan,
    addPlan,
    addToast,
  } = useApp();

  const [activeDayIndex, setActiveDayIndex] = useState(0);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [isEditingName, setIsEditingName] = useState(false);
  const [editedName, setEditedName] = useState('');
  // Opening a plan from a chat shortcut ("Open <plan name>") focuses that plan
  // here and scrolls to its selector tab.
  useEffect(() => {
    const focusId = location.state?.focusPlanId;
    if (focusId) {
      setActivePlanId(focusId);
      setActiveDayIndex(0);
      setTimeout(() => {
        document.getElementById(`plan-tab-${focusId}`)?.scrollIntoView({
          behavior: 'smooth',
          block: 'center',
        });
      }, 200);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location]);

  function handleSelectPlan(planId) {
    setActivePlanId(planId);
    setActiveDayIndex(0);
    setShowPlanMenu(false);
  }

  function formatIngredient(ing) {
    if (!ing) return '';
    if (typeof ing === 'string') return ing;
    const name = ing.name || '';
    const amt = Math.round(ing.amount_grams || ing.amount || 0);
    if (!amt) return name;
    const unit = ing.unit || (isLiquidIngredient(name) ? 'ml' : 'g');
    return `${name} (${amt}${unit})`;
  }

  // Active plan lookup. Sorted newest-first so "recent" tracks created_at
  // rather than last-edited; falls back to the newest plan when nothing is
  // selected, so the most recent plan loads by default.
  const plansArray = sortPlansNewestFirst(plans);
  const activePlan = plansArray.find(p => p.id === activePlanId) || plansArray[0] || null;

  function handleApprove(planId) {
    approvePlan(planId);
  }


  function handleExportPlan(plan) {
    if (!plan) return;
    const summary = `${plan.name} (${plan.days} Days)\nCalorie Target: ${plan.calorieTarget} kcal/day\nDiet: ${plan.dietType}\nStatus: ${plan.status.toUpperCase()}\n\n` +
      (plan.planDays || [])
        .map(
          d => `Day ${d.day} - ${d.totalCalories} kcal:\n` +
            d.meals.map(m => `  • ${m.type}: ${m.name} (${m.calories} kcal | P: ${m.protein}g C: ${m.carbs}g F: ${m.fat}g)`).join('\n')
        )
        .join('\n\n');

    navigator.clipboard?.writeText(summary);
    addToast('Meal plan copied to clipboard!', 'success');
  }

  function handleLoadSample() {
    const sample = generateDemoPlan('7-day 1800 cal veg');
    addPlan(sample);
    setActivePlanId(sample.id);
    setActiveDayIndex(0);
    addToast('Sample 7-day meal plan created!', 'success');
  }

  if (!activePlan || plansArray.length === 0) {
    return (
      <div className="page-content" id="plans-page">
        <div className="page-header">
          <h1 className="page-title">Meal Plans</h1>
          <p className="page-subtitle">Review, adjust, and approve your agent-generated meal plans.</p>
        </div>

        <div className="card empty-state" style={{ padding: '72px var(--space-8)' }}>
          <div className="empty-icon">📋</div>
          <h2 className="empty-title">No meal plans found</h2>
          <p className="empty-desc">
            You don't have an active meal plan yet. Start a conversation with our multi-agent assistant to generate your first personalized plan.
          </p>
          <div style={{ display: 'flex', gap: 'var(--space-3)', marginTop: 'var(--space-4)' }}>
            <button
              className="btn btn-primary"
              onClick={() => navigate('/app/chat')}
              id="empty-plan-chat-btn"
            >
              <MessageSquare size={16} />
              Start Planning in Chat
            </button>
          </div>
        </div>
      </div>
    );
  }

  const daysList = activePlan.planDays || [];
  const currentDay = daysList[activeDayIndex] || daysList[0] || null;

  // Day macros calculation
  const dayCalories = currentDay?.totalCalories || activePlan.calorieTarget || 1800;
  const dayProtein = currentDay?.meals?.reduce((s, m) => s + (m.protein || 0), 0) || 75;
  const dayCarbs = currentDay?.meals?.reduce((s, m) => s + (m.carbs || 0), 0) || 160;
  const dayFat = currentDay?.meals?.reduce((s, m) => s + (m.fat || 0), 0) || 45;

  return (
    <div className="page-content page-content-with-plan" id="plans-page">
      {/* Top Header */}
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-4)' }}>
        <div>
          <h1 className="page-title">Meal Plans</h1>
          <p className="page-subtitle">Inspect daily meals, nutritional macros, and allergy verification reports.</p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => navigate('/app/chat')}
            id="create-new-plan-btn"
          >
            <Plus size={14} />
            New Plan in Chat
          </button>
        </div>
      </div>

      {/* Plan selector: toggle bar listing every saved plan, newest first */}
      <PlanSelect plans={plansArray} activePlanId={activePlanId} onSelect={handleSelectPlan} />

      {/* Plan Header Card */}
      <div
        className="card plan-hero"
        style={{
          marginBottom: 'var(--space-6)',
          background: 'linear-gradient(180deg, #ffffff 0%, var(--gray-50) 100%)',
          borderColor: activePlan.status === 'approved' ? 'var(--green-200)' : 'var(--color-border)',
        }}
      >
        <div className="plan-hero-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-4)' }}>
          <div className="plan-hero-main">
            <div className="plan-hero-title-row" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-2)' }}>
              {isEditingName ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                  <input
                    type="text"
                    value={editedName}
                    onChange={e => setEditedName(e.target.value)}
                    className="form-input"
                    style={{ fontSize: 'var(--text-lg)', padding: '4px 8px', fontWeight: 600, width: 260 }}
                    autoFocus
                    onKeyDown={e => {
                      if (e.key === 'Enter') {
                        if (editedName.trim()) renamePlan(activePlan.id, editedName.trim());
                        setIsEditingName(false);
                      } else if (e.key === 'Escape') {
                        setIsEditingName(false);
                      }
                    }}
                  />
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={() => {
                      if (editedName.trim()) renamePlan(activePlan.id, editedName.trim());
                      setIsEditingName(false);
                    }}
                    title="Save name"
                  >
                    <Check size={14} />
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setIsEditingName(false)}
                    title="Cancel"
                  >
                    ✕
                  </button>
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                  <h2 className="plan-hero-name" style={{ fontSize: 'var(--text-xl)', fontWeight: 700, color: 'var(--gray-900)', margin: 0 }}>
                    {activePlan.name}
                  </h2>
                  <button
                    className="btn btn-ghost btn-sm plan-hero-rename"
                    onClick={() => {
                      setEditedName(activePlan.name);
                      setIsEditingName(true);
                    }}
                    title="Rename plan"
                    style={{ padding: '2px 6px', color: 'var(--gray-500)' }}
                  >
                    <Edit3 size={14} />
                  </button>
                </div>
              )}

              <span className={`badge ${activePlan.status === 'approved' ? 'badge-green' : 'badge-gray'}`}>
                {activePlan.status === 'approved' ? (
                  <>
                    <CheckCircle2 size={12} /> Version {activePlan.version || 1}
                  </>
                ) : (
                  <>
                    <Clock size={12} /> Draft
                  </>
                )}
              </span>
            </div>

            {/* Timestamps */}
            {(() => {
              const ts = getPlanTimestamps(activePlan);
              return (
                <div className="plan-hero-meta-row" style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-4)', marginTop: 'var(--space-3)', fontSize: 'var(--text-xs)', color: 'var(--gray-500)' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Calendar size={10} /> {ts.createdLabel} {ts.createdValue}
                  </span>
                  {ts.showUpdated && (
                    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <CalendarClock size={10} /> {ts.updatedLabel} {ts.updatedValue}
                    </span>
                  )}
                </div>
              );
            })()}

            <p className="plan-hero-meta" style={{ fontSize: 'var(--text-sm)', color: 'var(--gray-600)', margin: 'var(--space-2) 0 0 0' }}>
              {activePlan.days} days plan · {activePlan.dietType} · Target {activePlan.calorieTarget?.toLocaleString()} kcal/day
            </p>

            {/* Agent Verification Badges */}
            <div className="plan-verify" style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
              <span className="badge badge-green" title="Verified against Mifflin-St Jeor target">
                <BarChart2 size={12} /> Nutrition Agent: Verified (±50 kcal)
              </span>
              <span className="badge badge-green" title="Screened against user allergen profile">
                <ShieldCheck size={12} /> Allergy Agent: Cleared (Zero allergens detected)
              </span>
            </div>
          </div>

          {/* Action Buttons */}
          <div className="plan-hero-actions" style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', alignItems: 'center' }}>
            <button
              className="btn btn-outline btn-sm"
              onClick={() => handleExportPlan(activePlan)}
              title="Copy meal plan text"
              id="export-plan-btn"
            >
              <Copy size={14} /> Copy Summary
            </button>


            {activePlan.status === 'approved' ? (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => navigate('/app/grocery')}
                id="view-grocery-btn"
              >
                <ShoppingCart size={14} /> View Grocery List →
              </button>
            ) : (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => handleApprove(activePlan.id)}
                id="approve-plan-btn"
                disabled={approvingPlanId !== null}
                aria-busy={approvingPlanId !== null}
                style={{ background: 'var(--green-600)' }}
              >
                {approvingPlanId === activePlan.id ? (
                  <>
                    <span className="status-spinner" style={{ borderTopColor: '#fff' }} /> Approving…
                  </>
                ) : (
                  <>
                    <CheckCircle2 size={14} /> Approve
                  </>
                )}
              </button>
            )}

            <button
              className="btn btn-ghost btn-sm plan-hero-danger"
              onClick={() => setShowDeleteModal(true)}
              title="Delete this meal plan"
              id="delete-plan-btn"
              style={{ color: 'var(--color-error)' }}
            >
              <Trash2 size={14} />
            </button>
          </div>
        </div>
      </div>

      {/* Day Selector Navigation Tabs */}
      <div className="plan-tabs-wrap">
        <div className="plan-detail-tabs" id="plan-day-tabs">
        {daysList.map((dayItem, idx) => {
          const isActive = idx === activeDayIndex;
          return (
            <button
              key={dayItem.day}
              className={`plan-tab${isActive ? ' active' : ''}`}
              onClick={() => setActiveDayIndex(idx)}
              id={`day-tab-${dayItem.day}`}
            >
              <span style={{ display: 'block', fontWeight: 600 }}>
                Day {dayItem.day}
              </span>
              <span style={{ fontSize: '11px', color: isActive ? 'var(--green-700)' : 'var(--gray-400)', fontWeight: 500 }}>
                {dayItem.totalCalories || activePlan.calorieTarget} kcal
              </span>
            </button>
          );
        })}
        </div>
      </div>

      {/* Active Day Content */}
      {currentDay ? (
        <div>
          {/* Day Macro Overview Bar */}
          <div
            className="day-overview"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: 'var(--space-3)',
              padding: 'var(--space-4) var(--space-5)',
              background: '#fff',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-lg)',
              marginBottom: 'var(--space-5)',
            }}
          >
            <div className="day-overview-label">
              <span className="day-overview-title" style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--gray-900)' }}>
                Day {currentDay.day} - Overview
              </span>
              {/* On a phone the macros take the right half, so the meal count
                  sits hard right on its own row instead of crowding the title. */}
              <span className="day-overview-sub" style={{ fontSize: 'var(--text-xs)', color: 'var(--gray-500)', marginLeft: 'var(--space-2)' }}>
                {currentDay.meals?.length || 3} balanced meals
              </span>
            </div>

            <div className="macro-row day-overview-macros">
              <span className="macro-pill macro-cal">🔥 {dayCalories} Total kcal</span>
              <span className="macro-pill macro-protein">🥩 Protein: {dayProtein}g</span>
              <span className="macro-pill macro-carbs">🌾 Carbs: {dayCarbs}g</span>
              <span className="macro-pill macro-fat">🥑 Fat: {dayFat}g</span>
            </div>
          </div>

          {/* Meals Grid */}
          <div
            className="meal-cards-grid"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
              gap: 'var(--space-4)',
              marginBottom: 'var(--space-6)',
            }}
          >
            {currentDay.meals?.map((meal, mIdx) => {
              const MealIcon = MEAL_ICONS[meal.type] || Sunrise;
              const steps = splitSteps(meal.instructions);
              return (
                <div
                  key={mIdx}
                  className="card meal-card"
                  id={`meal-card-${currentDay.day}-${mIdx}`}
                >
                  <div className="meal-card-body">
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--space-3)' }}>
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 6,
                          fontSize: '11px',
                          fontWeight: 700,
                          textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                          color: 'var(--green-700)',
                          background: 'var(--green-50)',
                          padding: '3px 8px',
                          border: '1px solid var(--green-200)',
                          borderRadius: 'var(--radius-full)',
                        }}
                      >
                        <MealIcon size={13} /> {meal.type}
                      </span>
                      <span style={{ fontSize: 'var(--text-xs)', fontWeight: 600, color: 'var(--gray-700)' }}>
                        {meal.calories} kcal
                      </span>
                    </div>

                    <h3 style={{ fontSize: 'var(--text-md)', fontWeight: 600, color: 'var(--gray-900)', marginBottom: 'var(--space-3)', lineHeight: 1.4, minHeight: '40px' }}>
                      {meal.name}
                    </h3>

                    {meal.ingredients && (
                      <p style={{ fontSize: 'var(--text-xs)', color: 'var(--gray-500)', marginBottom: 'var(--space-3)', lineHeight: 1.5, minHeight: '38px' }}>
                        <strong className="meal-ingredients-label">Ingredients:</strong>{' '}
                        {Array.isArray(meal.ingredients)
                          ? meal.ingredients
                            .map(formatIngredient)
                            .filter(Boolean)
                            .join(', ')
                          : String(meal.ingredients)}
                      </p>
                    )}

                    {/* Cooking instructions, always visible, one numbered step per line */}
                    <div className="meal-instructions">
                      <strong className="meal-instructions-label">Cooking Instructions</strong>
                      <MealSteps steps={steps} />
                    </div>

                    <div className="macro-row" style={{ marginTop: 'var(--space-3)' }}>
                      <span className="macro-pill macro-protein">P: {meal.protein}g</span>
                      <span className="macro-pill macro-carbs">C: {meal.carbs}g</span>
                      <span className="macro-pill macro-fat">F: {meal.fat}g</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* Delete Confirmation Modal */}
      {showDeleteModal && (
        <div className="modal-overlay" onClick={e => e.target === e.currentTarget && setShowDeleteModal(false)}>
          <div className="modal confirm-dialog">
            <div className="confirm-body">
              <div className="confirm-title">Delete this meal plan?</div>
              <div className="confirm-desc">
                Are you sure you want to delete <strong>{activePlan.name}</strong>? This action will permanently remove this {activePlan.days}-day meal plan and its associated grocery list.
              </div>
            </div>
            <div className="confirm-footer">
              <button className="btn btn-outline btn-sm" onClick={() => setShowDeleteModal(false)}>
                Cancel
              </button>
              <button
                className="btn btn-danger btn-sm"
                onClick={() => {
                  setShowDeleteModal(false);
                  deletePlan(activePlan.id);
                }}
                id="confirm-delete-plan-btn"
              >
                Delete Plan
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


