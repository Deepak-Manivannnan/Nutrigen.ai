import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Logo } from '../components/Logo';
import TagInput from '../components/TagInput';
import { ChevronRight, ChevronLeft, Check, AlertCircle } from 'lucide-react';
import { useApp } from '../context/AppContext';
import {
  COMMON_ALLERGENS,
  isPredefinedAllergy as isCategoryAllergy,
  resolveCategory,
} from '../utils/allergenTaxonomy';

const STEPS = [
  { id: 1, label: 'Basic info' },
  { id: 2, label: 'Lifestyle' },
  { id: 3, label: 'Diet' },
  { id: 4, label: 'Allergies' },
  { id: 5, label: 'Review' },
];

function OptionBtn({ options, value, onChange, multi = false }) {
  function isSelected(opt) { return multi ? (value || []).includes(opt) : value === opt; }
  function handleClick(opt) {
    if (multi) {
      const arr = value || [];
      onChange(arr.includes(opt) ? arr.filter(v => v !== opt) : [...arr, opt]);
    } else {
      onChange(opt);
    }
  }
  return (
    <div className="option-buttons">
      {options.map(opt => (
        <button key={opt.value || opt} type="button"
          className={`option-btn${isSelected(opt.value || opt) ? ' selected' : ''}`}
          onClick={() => handleClick(opt.value || opt)}
        >
          {opt.label || opt}
        </button>
      ))}
    </div>
  );
}

const INITIAL_FORM = {
  age: '', sex: 'female', height_cm: '', weight_kg: '',
  activity_level: 'medium', goal: 'maintain',
  diet_type: 'non-veg', dislikes: [], allergies: [],
};

// Both now come from utils/allergenTaxonomy, which the Edit Profile modal also
// uses. Keeping a second copy here is what let the same typed food be resolved
// on one screen and kept verbatim on the other.
const PREDEFINED_ALLERGIES = COMMON_ALLERGENS;

function isPredefinedAllergy(allergy) {
  return isCategoryAllergy(allergy);
}

export default function OnboardingPage() {
  const navigate = useNavigate();
  const { createProfile, addToast, user } = useApp();
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(INITIAL_FORM);
  const [ageError, setAgeError] = useState('');
  // True while the profile is being written to the server, so the last button
  // cannot be pressed twice and does not look dead on a slow connection.
  const [saving, setSaving] = useState(false);

  function set(key, val) { setForm(p => ({ ...p, [key]: val })); }

  function handleNext() {
    if (step === 1) {
      const ageVal = Number(form.age);
      // Allow proceeding to next step if all basic fields are filled
      // Age validation happens in real-time and at form submission
      if (!form.age || !form.sex || !form.height_cm || !form.weight_kg) {
        return;
      }
      setAgeError('');
    }
    if (step < 5) setStep(s => s + 1);
  }
  function handleBack() {
    if (step > 1) setStep(s => s - 1);
  }

  function canProceed() {
    if (step === 1) {
      // Next button enabled when all 4 basic fields have any input
      // Age validation happens separately in real-time and at form submission
      return form.age && form.sex && form.height_cm && form.weight_kg;
    }
    return true;
  }

  async function handleFinish() {
    // Validate age at form submission
    const ageVal = Number(form.age);
    if (!form.age || isNaN(ageVal) || ageVal < 18) {
      setAgeError('Age should be 18 or above.');
      return;
    }

    const profileData = {
      ...form,
      name: user?.name || '',
      age: Number(form.age),
      height_cm: Number(form.height_cm),
      weight_kg: Number(form.weight_kg),
      fixed_meals: {
        breakfast: form.fixed_breakfast,
        lunch: form.fixed_lunch,
        dinner: form.fixed_dinner,
      },
    };

    setSaving(true);
    try {
      // Wait for the server before saying anything about the result. Firing
      // this off unawaited is what let a failed save announce itself as
      // "Profile created!" and land the user on Home with nothing stored.
      await createProfile(profileData);
    } catch (err) {
      // Nothing was saved, so nothing here may claim otherwise: no success
      // toast, no navigation. The answers stay on screen for another attempt.
      console.warn('Profile save failed:', err);
      addToast(
        'Could not save your profile. Please check your connection and try again.',
        'error'
      );
      return;
    } finally {
      setSaving(false);
    }
    addToast('Profile created! Welcome to Nutrigen.ai 🎉', 'success');
    // replace: sign-up and onboarding are steps toward the app, not places the
    // user should be able to Back into after landing on Home.
    navigate('/app/home', { replace: true });
  }

  return (
    <div className={`onboarding-page${step === 5 ? ' is-review' : ''}`} id="onboarding-page">
      <div className="onboarding-header">
        <Logo size="sm" />
        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--gray-500)' }}>Step {step} of {STEPS.length}</span>
      </div>
      <div className="onboarding-body">
        <div className="onboarding-container">
          <div style={{ textAlign: 'center', marginBottom: 'var(--space-5)' }}>
            <h1 style={{ fontSize: 'var(--text-2xl)', fontWeight: 700, color: 'var(--gray-900)', marginBottom: 4, letterSpacing: '-0.02em' }}>Create your profile</h1>
            <p style={{ fontSize: 'var(--text-sm)', color: 'var(--gray-500)' }}>This helps us create safe and personalized meal plans for you.</p>
          </div>

          <div className="onboarding-card">
            {/* Step tabs */}
            <div className="onboarding-step-tabs">
              {STEPS.map(s => (
                <div key={s.id}
                  className={`onboarding-step-tab${step === s.id ? ' active' : step > s.id ? ' done' : ''}`}
                >
                  <div className="step-num">
                    {step > s.id ? <Check size={11} /> : s.id}
                  </div>
                  {s.label}
                </div>
              ))}
            </div>

            <div className="onboarding-content">
              {/* Step 1: Personal Info */}
              {step === 1 && (
                <>
                  <h2 className="onboarding-step-title">Basic information</h2>
                  <p className="onboarding-step-desc">Let's start with some basic details.</p>
                  <div className="onboarding-form">
                    <div className="grid-2">
                      <div className="form-group">
                        <label className="form-label">Age</label>
                        <input
                          className={`form-input${ageError ? ' form-input-error' : ''}`}
                          type="number"
                          placeholder="28"
                          value={form.age}
                          onChange={e => { 
                            set('age', e.target.value); 
                            if (ageError) setAgeError(''); 
                          }}
                          onBlur={e => {
                            const ageVal = Number(e.target.value);
                            if (e.target.value && (isNaN(ageVal) || ageVal < 18)) {
                              setAgeError('Age should be 18 or above.');
                            }
                          }}
                          id="onboard-age"
                          min="18"
                        />
                        {ageError && (
                          <div className="form-error" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
                            <AlertCircle size={13} />
                            {ageError}
                          </div>
                        )}
                      </div>
                      <div className="form-group">
                        <label className="form-label">Gender</label>
                        <OptionBtn options={[{ value: 'female', label: 'Female' }, { value: 'male', label: 'Male' }, { value: 'other', label: 'Other' }]} value={form.sex} onChange={v => set('sex', v)} />
                      </div>
                    </div>
                    <div className="grid-2">
                      <div className="form-group">
                        <label className="form-label">Height (cm)</label>
                        <input className="form-input" type="number" placeholder="165" value={form.height_cm} onChange={e => set('height_cm', e.target.value)} id="onboard-height" />
                      </div>
                      <div className="form-group">
                        <label className="form-label">Weight (kg)</label>
                        <input className="form-input" type="number" placeholder="65" value={form.weight_kg} onChange={e => set('weight_kg', e.target.value)} id="onboard-weight" />
                      </div>
                    </div>
                  </div>
                </>
              )}

              {/* Step 2: Lifestyle */}
              {step === 2 && (
                <>
                  <h2 className="onboarding-step-title">Lifestyle &amp; goals</h2>
                  <p className="onboarding-step-desc">Help us understand your daily routine and fitness goals.</p>
                  <div className="onboarding-form">
                    <div className="form-group">
                      <label className="form-label">Activity level</label>
                      <p className="form-hint">How active are you in daily life?</p>
                      <OptionBtn
                        options={[{ value: 'low', label: '🧘 Low' }, { value: 'medium', label: '🚶 Moderate' }, { value: 'high', label: '🏃 High' }]}
                        value={form.activity_level} onChange={v => set('activity_level', v)}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">Your goal</label>
                      <OptionBtn
                        options={[{ value: 'lose', label: '📉 Lose weight' }, { value: 'maintain', label: '⚖️ Maintain' }, { value: 'gain', label: '📈 Gain weight' }]}
                        value={form.goal} onChange={v => set('goal', v)}
                      />
                    </div>
                  </div>
                </>
              )}

              {/* Step 3: Diet */}
              {step === 3 && (
                <>
                  <h2 className="onboarding-step-title">Diet preferences</h2>
                  <p className="onboarding-step-desc">Tell us about your dietary preferences.</p>
                  <div className="onboarding-form">
                    <div className="form-group">
                      <label className="form-label">Diet type</label>
                      <OptionBtn
                        options={[{ value: 'veg', label: '🥦 Vegetarian' }, { value: 'non-veg', label: '🍗 Non-Vegetarian' }, { value: 'vegan', label: '🌱 Vegan' }]}
                        value={form.diet_type} onChange={v => set('diet_type', v)}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">Foods you dislike</label>
                      <p className="form-hint">Type a food and press Enter or comma to add it.</p>
                      <TagInput
                        tags={form.dislikes}
                        onAdd={t => set('dislikes', [...form.dislikes, t])}
                        onRemove={t => set('dislikes', form.dislikes.filter(d => d !== t))}
                        placeholder="e.g. broccoli, bitter gourd"
                        allowAlphaOnly
                      />
                    </div>
                  </div>
                </>
              )}

              {/* Step 4: Allergies */}
              {step === 4 && (
                <>
                  <h2 className="onboarding-step-title">Allergies &amp; restrictions</h2>
                  <p className="onboarding-step-desc">We check every ingredient against your allergies before finalizing your plan.</p>
                  <div className="onboarding-form">
                    <div className="form-group">
                      <label className="form-label">Common allergens</label>
                      <OptionBtn
                        multi
                        options={['Nuts', 'Shellfish', 'Dairy', 'Gluten', 'Eggs', 'Soy', 'Fish']}
                        value={form.allergies}
                        onChange={v => set('allergies', v)}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">Other allergies</label>
                      <p className="form-hint">Add any additional allergies not listed above.</p>
                      <TagInput
                        tags={form.allergies.filter(a => !isPredefinedAllergy(a))}
                        onAdd={t => {
                          // Same rule as Edit Profile: a food inside one of the
                          // seven groups ticks its category; anything else stays
                          // as a removable chip.
                          const category = resolveCategory(t);
                          const target = category || t;
                          const exists = form.allergies.some(a => a.toLowerCase() === target.toLowerCase());
                          if (!exists) set('allergies', [...form.allergies, target]);
                        }}
                        onRemove={t => set('allergies', form.allergies.filter(a => a.toLowerCase() !== t.toLowerCase()))}
                        placeholder="e.g. sesame, mustard"
                        allowAlphaOnly
                      />
                    </div>
                  </div>
                </>
              )}

              {/* Step 5: Review */}
              {step === 5 && (
                <>
                  <h2 className="onboarding-step-title">Review your profile</h2>
                  <p className="onboarding-step-desc">Everything looks good? You can always edit later.</p>
                  <div className="review-list" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
                    {[
                      { label: 'Name', value: user?.name || '—' },
                      { label: 'Age / Gender', value: `${form.age} years · ${form.sex}` },
                      { label: 'Height / Weight', value: `${form.height_cm} cm · ${form.weight_kg} kg` },
                      { label: 'Activity Level', value: { low: 'Low', medium: 'Moderate', high: 'High' }[form.activity_level] },
                      { label: 'Goal', value: { lose: 'Lose weight', maintain: 'Maintain weight', gain: 'Gain weight' }[form.goal] },
                      { label: 'Diet Type', value: { veg: 'Vegetarian', 'non-veg': 'Non-Vegetarian', vegan: 'Vegan' }[form.diet_type] },
                      { label: 'Allergies', value: form.allergies.length ? form.allergies.join(', ') : 'None' },
                      { label: 'Dislikes', value: form.dislikes.length ? form.dislikes.join(', ') : 'None' },
                    ].map((item, i) => (
                      <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 14px', background: 'var(--gray-50)', border: '1px solid var(--gray-200)', borderRadius: 'var(--radius-md)' }}>
                        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--gray-500)', fontWeight: 500 }}>{item.label}</span>
                        <span style={{ fontSize: 'var(--text-sm)', color: 'var(--gray-900)', fontWeight: 500, textAlign: 'right', maxWidth: '60%' }}>{item.value || '—'}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>

            <div className="onboarding-actions">
              <button
                className="btn btn-outline btn-sm"
                onClick={step === 1 ? () => navigate('/') : handleBack}
                id="onboard-back"
              >
                <ChevronLeft size={14} />
                {step === 1 ? 'Back to home' : 'Back'}
              </button>
              {step < 5 ? (
                <button className="btn btn-primary btn-sm" onClick={handleNext} disabled={!canProceed()} id="onboard-next">
                  Next <ChevronRight size={14} />
                </button>
              ) : (
                <button className="btn btn-primary" onClick={handleFinish} id="onboard-finish" disabled={saving}>
                  {saving ? 'Saving your profile…' : <>Continue to Nutrigen.ai <ChevronRight size={14} /></>}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
