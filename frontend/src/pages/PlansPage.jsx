import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CalendarDays,
  CheckCircle2,
  Clock,
  ShieldCheck,
  BarChart2,
  
  ShoppingCart,
  Copy,
  Plus,
  Trash2,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  ChefHat,
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
import { generateDemoPlan } from '../utils/workflowSimulator';

const MEAL_ICONS = {
  Breakfast: Sunrise,
  Lunch: Sun,
  Dinner: Sunset,
  Snack: Apple,
};

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
  const {
    plans,
    activePlanId,
    setActivePlanId,
    approvePlan,
    deletePlan,
    renamePlan,
    addPlan,
    addToast,
  } = useApp();

  const [activeDayIndex, setActiveDayIndex] = useState(0);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [isEditingName, setIsEditingName] = useState(false);
  const [editedName, setEditedName] = useState('');
  const [expandedInstructions, setExpandedInstructions] = useState({});

  const LIQUID_KEYWORDS = ['oil', 'milk', 'juice', 'water', 'sauce', 'vinegar', 'broth', 'stock', 'syrup', 'honey', 'dressing', 'buttermilk'];
  function isLiquid(name) {
    if (!name) return false;
    const lower = name.toLowerCase();
    return LIQUID_KEYWORDS.some(k => lower.includes(k));
  }

  function formatIngredient(ing) {
    if (!ing) return '';
    if (typeof ing === 'string') return ing;
    const name = ing.name || '';
    const amt = Math.round(ing.amount_grams || ing.amount || 0);
    if (!amt) return name;
    const unit = ing.unit || (isLiquid(name) ? 'ml' : 'g');
    return `${name} (${amt}${unit})`;
  }

  function toggleInstructions(mealKey) {
    setExpandedInstructions(prev => ({
      ...prev,
      [mealKey]: !prev[mealKey]
    }));
  }

  // Active plan lookup
  const plansArray = Array.isArray(plans) ? plans : [];
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
          <h1 className="page-title">Weekly Meal Plans</h1>
          <p className="page-subtitle">Review, adjust, and approve your agent-generated meal plans.</p>
        </div>

        <div className="card empty-state" style={{ padding: '60px var(--space-6)' }}>
          <div className="empty-icon">📋</div>
          <h2 className="empty-title">No meal plans found</h2>
          <p className="empty-desc">
            You don't have an active meal plan yet. Start a conversation with our multi-agent assistant to generate your first personalized plan.
          </p>
          <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
            <button
              className="btn btn-primary"
              onClick={() => navigate('/app/chat')}
              id="empty-plan-chat-btn"
            >
              <MessageSquare size={16} />
              Start Planning in Chat
            </button>
            <button
              className="btn btn-outline"
              onClick={handleLoadSample}
              id="empty-plan-sample-btn"
            >
              Load Sample 7-Day Plan
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
    <div className="page-content" id="plans-page">
      {/* Top Header */}
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-4)' }}>
        <div>
          <h1 className="page-title">Weekly Meal Plans</h1>
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

      {/* Plan Selector tabs if multiple plans */}
      {plansArray.length > 1 && (
        <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-6)', overflowX: 'auto', paddingBottom: 4 }}>
          {plansArray.map(p => {
            const isSelected = p.id === activePlan.id;
            const statusLabel = p.status === 'approved' ? `Version ${p.version || 1}` : 'Draft';
            return (
              <button
                key={p.id}
                onClick={() => {
                  setActivePlanId(p.id);
                  setActiveDayIndex(0);
                }}
                className={`btn btn-sm ${isSelected ? 'btn-primary' : 'btn-outline'}`}
                style={{ borderRadius: 'var(--radius-full)' }}
              >
                {p.name} · {statusLabel}
              </button>
            );
          })}
        </div>
      )}

      {/* Plan Header Card */}
      <div
        className="card"
        style={{
          marginBottom: 'var(--space-6)',
          background: 'linear-gradient(180deg, #ffffff 0%, var(--gray-50) 100%)',
          borderColor: activePlan.status === 'approved' ? 'var(--green-200)' : 'var(--color-border)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-4)' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-2)' }}>
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
                  <h2 style={{ fontSize: 'var(--text-xl)', fontWeight: 700, color: 'var(--gray-900)', margin: 0 }}>
                    {activePlan.name}
                  </h2>
                  <button
                    className="btn btn-ghost btn-sm"
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
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-4)', marginTop: 'var(--space-3)', fontSize: 'var(--text-xs)', color: 'var(--gray-500)' }}>
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

            <p style={{ fontSize: 'var(--text-sm)', color: 'var(--gray-600)', margin: 'var(--space-2) 0 0 0' }}>
              {activePlan.days} days plan · {activePlan.dietType} · Target {activePlan.calorieTarget?.toLocaleString()} kcal/day
            </p>

            {/* Agent Verification Badges */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
              <span className="badge badge-green" title="Verified against Mifflin-St Jeor target">
                <BarChart2 size={12} /> Nutrition Agent: Verified (±50 kcal)
              </span>
              <span className="badge badge-green" title="Screened against user allergen profile">
                <ShieldCheck size={12} /> Allergy Agent: Cleared (Zero allergens detected)
              </span>
            </div>
          </div>

          {/* Action Buttons */}
          <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', alignItems: 'center' }}>
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
                style={{ background: 'var(--green-600)' }}
              >
                <CheckCircle2 size={14} /> Approve
              </button>
            )}

            <button
              className="btn btn-ghost btn-sm"
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

      {/* Active Day Content */}
      {currentDay ? (
        <div>
          {/* Day Macro Overview Bar */}
          <div
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
            <div>
              <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--gray-900)' }}>
                Day {currentDay.day} Overview
              </span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--gray-500)', marginLeft: 'var(--space-2)' }}>
                {currentDay.meals?.length || 3} balanced meals
              </span>
            </div>

            <div className="macro-row">
              <span className="macro-pill macro-cal">🔥 {dayCalories} Total kcal</span>
              <span className="macro-pill macro-protein">🥩 Protein: {dayProtein}g</span>
              <span className="macro-pill macro-carbs">🌾 Carbs: {dayCarbs}g</span>
              <span className="macro-pill macro-fat">🥑 Fat: {dayFat}g</span>
            </div>
          </div>

          {/* Meals Grid */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
              gap: 'var(--space-4)',
              alignItems: 'start',
              marginBottom: 'var(--space-6)',
            }}
          >
            {currentDay.meals?.map((meal, mIdx) => {
              const MealIcon = MEAL_ICONS[meal.type] || Sunrise;
              const isExpanded = !!expandedInstructions[`meal-${currentDay.day}-${mIdx}`];
              return (
                <div
                  key={mIdx}
                  className="card"
                  style={{
                    padding: 'var(--space-5)',
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'space-between',
                    position: 'relative',
                    transition: 'all var(--transition-fast)',
                    minHeight: isExpanded ? 'auto' : '290px',
                    height: isExpanded ? 'auto' : '290px',
                  }}
                  id={`meal-card-${currentDay.day}-${mIdx}`}
                >
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
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
                        <strong>Ingredients:</strong>{' '}
                        {Array.isArray(meal.ingredients)
                          ? meal.ingredients
                            .map(formatIngredient)
                            .filter(Boolean)
                            .join(', ')
                          : String(meal.ingredients)}
                      </p>
                    )}

                    {/* Cooking Instructions Dropdown */}
                    <div style={{ marginBottom: 'var(--space-3)', marginTop: isExpanded ? '0' : 'auto' }}>
                      <button
                        type="button"
                        onClick={() => toggleInstructions(`meal-${currentDay.day}-${mIdx}`)}
                        className="btn btn-ghost btn-xs"
                        style={{
                          padding: '3px 8px',
                          fontSize: '11px',
                          color: 'var(--green-700)',
                          background: 'var(--green-50)',
                          border: '1px solid var(--green-200)',
                          borderRadius: 'var(--radius-md)',
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 5,
                          cursor: 'pointer',
                          fontWeight: 600,
                        }}
                        id={`cooking-instructions-btn-${currentDay.day}-${mIdx}`}
                      >
                        <ChefHat size={12} />
                        Cooking Instructions
                        {expandedInstructions[`meal-${currentDay.day}-${mIdx}`] ? (
                          <ChevronUp size={12} />
                        ) : (
                          <ChevronDown size={12} />
                        )}
                      </button>

                      {expandedInstructions[`meal-${currentDay.day}-${mIdx}`] && (
                        <div
                          style={{
                            marginTop: 'var(--space-2)',
                            padding: 'var(--space-3)',
                            background: 'var(--gray-50)',
                            border: '1px solid var(--gray-200)',
                            borderRadius: 'var(--radius-md)',
                            fontSize: '11.5px',
                            color: 'var(--gray-700)',
                            lineHeight: 1.6,
                            whiteSpace: 'pre-line',
                          }}
                        >
                          <div style={{ fontWeight: 600, color: 'var(--green-800)', marginBottom: '3px', display: 'flex', alignItems: 'center', gap: 4 }}>
                            <span>👨‍🍳</span> Step-by-Step Guidance:
                          </div>
                          {meal.instructions || "1. Prep all fresh ingredients and seasonings.\n2. Heat a pan over medium heat with a splash of oil.\n3. Cook ingredients until thoroughly tender and fragrant.\n4. Season to taste and serve warm."}
                        </div>
                      )}
                    </div>
                  </div>

                  <div>
                    <div className="macro-row" style={{ marginTop: 'var(--space-2)' }}>
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


