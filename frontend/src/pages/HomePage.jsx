import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../context/AppContext';
import { Flame, Utensils, ShieldCheck, Target, MoreHorizontal, ArrowRight, Calendar, ShoppingCart, Sparkles, CheckCircle2 } from 'lucide-react';
import nutrigenLogo from '../assets/nutrigen_logo.png';
import bannerFoodImg from '../assets/banner_food.png';
import thumb1 from '../assets/plan_thumb_1.png';
import thumb2 from '../assets/plan_thumb_2.png';
import thumb3 from '../assets/plan_thumb_3.png';

function calorieRange(profile) {
  if (!profile) return '2,000–2,200 kcal';
  // Use persisted calorie data from backend if available
  if (profile.min_safe_calories !== undefined && profile.max_safe_calories !== undefined && profile.suggested_target_calories !== undefined) {
    const low = Math.round(profile.min_safe_calories);
    const high = Math.round(profile.max_safe_calories);
    return `${low.toLocaleString()}–${high.toLocaleString()} kcal`;
  }
  // Fallback for backward compatibility
  const { age, sex, height_cm, weight_kg, activity_level, goal } = profile;
  if (!age || !height_cm || !weight_kg) return '2,000–2,200 kcal';
  // Mifflin-St Jeor
  let bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age;
  bmr += sex === 'male' ? 5 : -161;
  const mult = { low: 1.375, medium: 1.55, high: 1.725 }[activity_level] || 1.55;
  const tdee = bmr * mult;
  const offset = { lose: -300, maintain: 0, gain: 300 }[goal] || 0;
  const target = tdee + offset;
  const roundedTarget = Math.round(target / 50) * 50;
  const low = roundedTarget - 100;
  const high = roundedTarget + 100;
  return `${low.toLocaleString()}–${high.toLocaleString()} kcal`;
}

function formatDietType(dt) {
  return { veg: 'Vegetarian', 'non-veg': 'Non-Vegetarian', vegan: 'Vegan' }[dt] || dt || 'Vegetarian';
}

function formatGoal(g) {
  return { lose: 'Lose weight', maintain: 'Maintain weight', gain: 'Gain weight' }[g] || g || 'Maintain weight';
}

function getGreeting() {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return 'Good morning';
  if (hour >= 12 && hour < 17) return 'Good afternoon';
  return 'Good evening';
}

export default function HomePage() {
  const navigate = useNavigate();
  const { profile, plans, setActivePlanId } = useApp();

  const userPlans = (plans || []).filter(p => p.days && p.days.length > 0);
  const thumbs = [thumb1, thumb2, thumb3];

  return (
    <div className="page-content home-page" id="home-page">
      {/* Greeting */}
      <div className="dash-greeting-wrap">
        <h1 className="dash-title">
          {getGreeting()}, {profile?.name?.split(' ')[0] || 'there'} 👋
        </h1>
        <p className="dash-subtitle">Ready to plan your next meal?</p>
      </div>

      {/* 4 KPI Cards */}
      <div className="dash-kpi-grid">
        <div className="dash-kpi-card kpi-card-cal">
          <div className="dash-kpi-icon icon-bg-green">
            <Flame size={22} color="#16a34a" />
          </div>
          <div className="dash-kpi-content">
            <div className="dash-kpi-label">Daily calorie target</div>
            <div className="dash-kpi-value">{calorieRange(profile)}</div>
            <div className="dash-kpi-sub">Based on your profile</div>
          </div>
        </div>

        <div className="dash-kpi-card kpi-card-diet">
          <div className="dash-kpi-icon icon-bg-blue">
            <Utensils size={20} color="#0284c7" />
          </div>
          <div className="dash-kpi-content">
            <div className="dash-kpi-label">Diet type</div>
            <div className="dash-kpi-value">{formatDietType(profile?.diet_type)}</div>
            <div className="dash-kpi-sub">As per your preferences</div>
          </div>
        </div>

        <div className="dash-kpi-card kpi-card-allergies">
          <div className="dash-kpi-icon icon-bg-red">
            <ShieldCheck size={22} color="#ef4444" />
          </div>
          <div className="dash-kpi-content">
            <div className="dash-kpi-label">Allergies</div>
            <div className="dash-kpi-value">
              {profile?.allergies && profile.allergies.length > 0
                ? profile.allergies.join(', ')
                : 'None'}
            </div>
            <div className="dash-kpi-sub">
              {profile?.allergies && profile.allergies.length > 0
                ? 'Checked in every plan'
                : 'No restrictions declared'}
            </div>
          </div>
        </div>

        <div className="dash-kpi-card kpi-card-goal">
          <div className="dash-kpi-icon icon-bg-green">
            <Target size={22} color="#16a34a" />
          </div>
          <div className="dash-kpi-content">
            <div className="dash-kpi-label">Your goal</div>
            <div className="dash-kpi-value">{formatGoal(profile?.goal)}</div>
            <div className="dash-kpi-sub">Based on your profile</div>
          </div>
        </div>
      </div>

      {/* Create Plan CTA */}
      <div className="dash-cta-banner">
        <div className="dash-cta-left">
          <div className="dash-cta-icon-circle">
            <img src={nutrigenLogo} alt="Sprout" className="dash-cta-sprout" />
          </div>
          <div className="dash-cta-text-area">
            <h2 className="dash-cta-title">Create a new meal plan</h2>
            <p className="dash-cta-desc">
              Tell us what you'd like to eat, and we'll create a nutritious meal plan with recipes and a complete grocery list – tailored to your goals.
            </p>
            <button
              className="dash-cta-btn"
              onClick={() => navigate('/app/chat')}
              id="start-planning-btn"
            >
              Start planning <ArrowRight size={16} />
            </button>
          </div>
        </div>
        <div className="dash-cta-img-wrap">
          <img
            src={bannerFoodImg}
            alt="Nutritious fresh meal bowl"
            className="dash-cta-img"
          />
        </div>
      </div>

      {/* Recent Plans OR How It Works (for new user / when no plans exist) */}
      <div className="dash-plans-section">
        {userPlans.length > 0 ? (
          <>
            <div className="dash-plans-header">
              <h2 className="dash-plans-title">Your recent plans</h2>
              <button
                className="dash-view-all-btn"
                onClick={() => navigate('/app/plans')}
                id="view-all-plans-btn"
              >
                View all <ArrowRight size={15} />
              </button>
            </div>

            <div className="dash-plans-grid">
              {userPlans.slice(0, 3).map((plan, idx) => {
                const createdStr = plan.created_at
                  ? `Created ${new Date(plan.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
                  : 'Active Plan';
                const thumb = thumbs[idx % thumbs.length];
                return (
                  <div
                    key={plan.id || idx}
                    className="dash-plan-card"
                    onClick={() => {
                      if (plan.id) setActivePlanId(plan.id);
                      navigate('/app/plans');
                    }}
                    id={`plan-card-${plan.id || idx}`}
                  >
                    <img
                      src={thumb}
                      alt={plan.name}
                      className="dash-plan-thumb"
                    />
                    <div className="dash-plan-info">
                      <div className="dash-plan-name">{plan.name}</div>
                      <div className="dash-plan-meta">
                        {plan.calorieTarget ? `${plan.calorieTarget.toLocaleString()} kcal/day  •  ` : ''}
                        {plan.days?.length || plan.daysCount || 7} Days
                      </div>
                      <div className="dash-plan-date">
                        <Calendar size={13} style={{ flexShrink: 0 }} />
                        <span>{createdStr}</span>
                      </div>
                    </div>
                    <div className="dash-plan-actions">
                      <button
                        type="button"
                        className="dash-plan-dots-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate('/app/plans');
                        }}
                        aria-label="Plan options"
                      >
                        <MoreHorizontal size={16} />
                      </button>
                      <div className="dash-plan-arrow-circle">
                        <ArrowRight size={14} />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          /* How It Works 4-Step Card Row for new users */
          <>
            <div className="dash-plans-header">
              <div>
                <h2 className="dash-plans-title">How Nutrigen.ai Works</h2>
                <p style={{ fontSize: 13, color: 'var(--gray-500)', margin: '4px 0 0' }}>
                  Our autonomous clinical 4-step workflow creates balanced, allergen-safe meals for you
                </p>
              </div>
              <button
                className="dash-view-all-btn"
                onClick={() => navigate('/app/chat')}
                id="create-first-plan-btn"
              >
                Create your first plan <ArrowRight size={15} />
              </button>
            </div>

            <div className="how-it-works-grid">
              <div className="how-step-card">
                <div className="how-step-badge">1</div>
                <div className="how-step-icon icon-bg-blue">
                  <Utensils size={22} color="#0284c7" />
                </div>
                <div className="how-step-title">Meal Plan</div>
                <div className="how-step-desc">
                  Custom daily meal schedules tailored to your biometric calorie target and food preferences.
                </div>
              </div>

              <div className="how-step-card">
                <div className="how-step-badge">2</div>
                <div className="how-step-icon icon-bg-green">
                  <Flame size={22} color="#16a34a" />
                </div>
                <div className="how-step-title">Nutrition Check</div>
                <div className="how-step-desc">
                  Precision macronutrient verification balancing protein, carbs, and fats within ±5% of your target.
                </div>
              </div>

              <div className="how-step-card">
                <div className="how-step-badge">3</div>
                <div className="how-step-icon icon-bg-red">
                  <ShieldCheck size={22} color="#ef4444" />
                </div>
                <div className="how-step-title">Allergy Check</div>
                <div className="how-step-desc">
                  Clinical zero-tolerance screening cross-checking all dishes and seasonings against your allergens.
                </div>
              </div>

              <div className="how-step-card">
                <div className="how-step-badge">4</div>
                <div className="how-step-icon icon-bg-green">
                  <ShoppingCart size={22} color="#15803d" />
                </div>
                <div className="how-step-title">Grocery List</div>
                <div className="how-step-desc">
                  Automated consolidated shopping basket with estimated Indian wholesale Mandi commodity prices.
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
