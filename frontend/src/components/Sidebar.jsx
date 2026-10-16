import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { ChevronUp, LogOut, Edit3, X } from 'lucide-react';
import { useApp } from '../context/AppContext';
import TagInput from './TagInput';
import nutrigenLogo from '../assets/nutrigen_logo.png';

function NavHomeIcon({ active }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="nav-icon" style={{ flexShrink: 0 }}>
      <path
        d="M3 10.5L12 3l9 7.5V20a2 2 0 01-2 2h-4a1 1 0 01-1-1v-5a1 1 0 00-1-1h-2a1 1 0 00-1 1v5a1 1 0 01-1 1H5a2 2 0 01-2-2V10.5z"
        fill={active ? "#15803d" : "none"}
        stroke={active ? "#15803d" : "var(--gray-500)"}
        strokeWidth={active ? "1.2" : "1.8"}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function NavChatIcon({ active }) {
  if (active) {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="nav-icon" style={{ flexShrink: 0 }}>
        <path
          d="M21 12c0 4.418-4.03 8-9 8-1.55 0-3-.35-4.28-1l-4.72 1.3 1.35-3.8A7.78 7.78 0 013 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
          fill="#15803d"
          stroke="#15803d"
          strokeWidth="1.2"
          strokeLinejoin="round"
        />
        <circle cx="8.5" cy="12" r="1.2" fill="#ffffff" />
        <circle cx="12" cy="12" r="1.2" fill="#ffffff" />
        <circle cx="15.5" cy="12" r="1.2" fill="#ffffff" />
      </svg>
    );
  }
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="nav-icon" style={{ flexShrink: 0 }}>
      <path
        d="M21 12c0 4.418-4.03 8-9 8-1.55 0-3-.35-4.28-1l-4.72 1.3 1.35-3.8A7.78 7.78 0 013 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
        stroke="var(--gray-500)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="8.5" cy="12" r="1" fill="var(--gray-500)" />
      <circle cx="12" cy="12" r="1" fill="var(--gray-500)" />
      <circle cx="15.5" cy="12" r="1" fill="var(--gray-500)" />
    </svg>
  );
}

function NavPlansIcon({ active }) {
  if (active) {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="nav-icon" style={{ flexShrink: 0 }}>
        <rect x="4" y="4" width="16" height="17" rx="2.5" fill="#15803d" stroke="#15803d" strokeWidth="1.2" />
        <rect x="8.5" y="2" width="7" height="3.5" rx="1" fill="#166534" stroke="#ffffff" strokeWidth="1" />
        <path d="M8 10h8M8 14h8M8 18h5" stroke="#ffffff" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="nav-icon" style={{ flexShrink: 0 }}>
      <rect x="4" y="4" width="16" height="17" rx="2" stroke="var(--gray-500)" strokeWidth="1.8" />
      <path d="M9 2h6v3a1 1 0 01-1 1h-4a1 1 0 01-1-1V2z" stroke="var(--gray-500)" strokeWidth="1.6" fill="#fff" />
      <path d="M8 10h8M8 14h8M8 18h5" stroke="var(--gray-500)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function NavGroceryIcon({ active }) {
  if (active) {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="nav-icon" style={{ flexShrink: 0 }}>
        <path
          d="M6 6h15l-1.8 8.5a1.5 1.5 0 01-1.5 1.2H8.8a1.5 1.5 0 01-1.5-1.2L5.2 4H2"
          fill="#15803d"
          stroke="#15803d"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="9" cy="20" r="1.8" fill="#15803d" />
        <circle cx="17" cy="20" r="1.8" fill="#15803d" />
      </svg>
    );
  }
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" className="nav-icon" style={{ flexShrink: 0 }}>
      <path
        d="M6 6h15l-1.8 8.5a1.5 1.5 0 01-1.5 1.2H8.8a1.5 1.5 0 01-1.5-1.2L5.2 4H2"
        stroke="var(--gray-500)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="9" cy="20" r="1.8" stroke="var(--gray-500)" strokeWidth="1.8" />
      <circle cx="17" cy="20" r="1.8" stroke="var(--gray-500)" strokeWidth="1.8" />
    </svg>
  );
}

export default function Sidebar({ mobileOpen, onMobileClose }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { profile, user, signOut, chatBadge, plansBadge, groceryBadge } = useApp();
  const [showProfileDrop, setShowProfileDrop] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const footerRef = useRef(null);

  // Close profile dropdown when clicking outside
  useEffect(() => {
    function handleDocClick(e) {
      if (footerRef.current && !footerRef.current.contains(e.target)) {
        setShowProfileDrop(false);
      }
    }
    if (showProfileDrop) {
      document.addEventListener('mousedown', handleDocClick);
      document.addEventListener('touchstart', handleDocClick);
    }
    return () => {
      document.removeEventListener('mousedown', handleDocClick);
      document.removeEventListener('touchstart', handleDocClick);
    };
  }, [showProfileDrop]);

  const navItems = [
    { path: '/app/home', label: 'Home', icon: NavHomeIcon },
    { path: '/app/chat', label: 'Chat', icon: NavChatIcon, badge: chatBadge },
    { path: '/app/plans', label: 'Plans', icon: NavPlansIcon, badge: plansBadge },
    { path: '/app/grocery', label: 'Grocery', icon: NavGroceryIcon, badge: groceryBadge },
  ];

  const displayName = profile?.name || user?.name || 'User';
  const initials = displayName
    .split(' ')
    .filter(Boolean)
    .map(n => n[0])
    .join('')
    .slice(0, 2)
    .toUpperCase() || 'U';

  function handleNav(path) {
    setShowProfileDrop(false);
    navigate(path);
    if (onMobileClose) onMobileClose();
  }

  function handleSignOut() {
    setShowProfileDrop(false);
    signOut();
    navigate('/');
  }

  function handleEditProfile() {
    setShowProfileDrop(false);
    setShowEditModal(true);
  }

  return (
    <>
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.3)', zIndex: 199 }}
          onClick={onMobileClose}
        />
      )}

      <aside className={`sidebar${mobileOpen ? ' mobile-open' : ''}`}>
        <div className="sidebar-brand">
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <img
              src={nutrigenLogo}
              alt="Nutrigen.ai Logo"
              style={{ height: 26, width: 'auto', display: 'block', objectFit: 'contain' }}
            />
            <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1 }}>
              <span style={{ color: '#16a34a' }}>Nutri</span>
              <span style={{ color: '#0f172a' }}>gen.ai</span>
            </span>
          </div>
        </div>

        <nav className="sidebar-nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = location.pathname === item.path;
            return (
              <button
                key={item.path}
                className={`sidebar-nav-item${isActive ? ' active' : ''}`}
                onClick={() => handleNav(item.path)}
                id={`nav-${item.label.toLowerCase()}`}
              >
                <Icon active={isActive} />
                {item.label}
                {/* Never dot the tab you are already looking at. */}
                {item.badge && !isActive && (
                  <span className="nav-badge-dot" title="New content available" />
                )}
              </button>
            );
          })}
        </nav>

        <div className="sidebar-footer" ref={footerRef}>
          {showProfileDrop && (
            <div className="profile-dropdown" id="profile-dropdown-menu">
              <button className="dropdown-item" onClick={handleEditProfile} id="edit-profile-menu-item">
                <Edit3 size={14} />
                Edit Profile
              </button>
              <div className="dropdown-divider" />
              <button className="dropdown-item danger" onClick={handleSignOut} id="sign-out-menu-item">
                <LogOut size={14} />
                Sign Out
              </button>
            </div>
          )}

          <button
            className="profile-trigger"
            onClick={() => setShowProfileDrop(v => !v)}
            id="profile-menu-trigger"
            aria-expanded={showProfileDrop}
          >
            <div className="profile-avatar">{initials}</div>
            <div className="profile-info">
              <div className="profile-name">{displayName}</div>
            </div>
            <ChevronUp size={14} style={{ color: 'var(--gray-400)', flexShrink: 0, transform: showProfileDrop ? 'none' : 'rotate(180deg)', transition: 'transform 0.2s' }} />
          </button>
        </div>
      </aside>

      {showEditModal && (
        <EditProfileModal onClose={() => setShowEditModal(false)} />
      )}
    </>
  );
}

function EditProfileModal({ onClose }) {
  const { profile, updateProfile, addToast } = useApp();
  const [form, setForm] = useState({
    name: profile?.name || '',
    age: profile?.age || '',
    height_cm: profile?.height_cm || '',
    weight_kg: profile?.weight_kg || '',
    activity_level: profile?.activity_level || 'medium',
    goal: profile?.goal || 'maintain',
    diet_type: profile?.diet_type || 'veg',
  });

  const [errors, setErrors] = useState({
    age: '',
  });

  const [selectedAllergies, setSelectedAllergies] = useState(
    Array.isArray(profile?.allergies) ? [...profile.allergies] : []
  );

  const COMMON_ALLERGENS = ['Nuts', 'Shellfish', 'Dairy', 'Gluten', 'Eggs', 'Soy', 'Fish'];

  // Mirrors the server-side allergen taxonomy. When a typed food belongs to
  // one of the seven categories the backend collapses it into that category
  // on save, so the modal selects the category too rather than flashing a
  // chip that would disappear on save. Foods outside these groups (mutton,
  // lamb, beef, ...) are kept as-is by the backend and show as chips.
  const CATEGORY_ALIASES = {
    'Nuts': ['nut', 'nuts', 'peanut', 'peanuts', 'groundnut', 'tree nut', 'tree nuts', 'almond', 'almonds', 'walnut', 'walnuts', 'cashew', 'pistachio', 'pecan', 'hazelnut', 'macadamia'],
    'Shellfish': ['shellfish', 'shrimp', 'prawn', 'prawns', 'crab', 'lobster', 'clam', 'clams', 'mussel', 'mussels', 'oyster', 'oysters', 'scallop', 'scallops', 'squid', 'calamari', 'octopus', 'crawfish'],
    'Dairy': ['dairy', 'milk', 'cow milk', 'cheese', 'butter', 'cream', 'yogurt', 'yogurt', 'curd', 'paneer', 'ghee', 'casein', 'whey'],
    'Gluten': ['gluten', 'wheat', 'barley', 'rye', 'spelt', 'seitan', 'semolina', 'couscous', 'maida', 'atta'],
    'Eggs': ['egg', 'eggs', 'mayo', 'mayonnaise', 'albumen', 'albumin', 'meringue'],
    'Soy': ['soy', 'soya', 'soybean', 'soybeans', 'edamame', 'tofu', 'tempeh', 'miso', 'tamari'],
    'Fish': ['fish', 'salmon', 'tuna', 'cod', 'tilapia', 'trout', 'halibut', 'sardine', 'anchovy', 'mackerel', 'bass', 'snapper'],
  };

  function resolveCategory(value) {
    const clean = String(value || '').trim().toLowerCase();
    if (!clean) return null;
    return COMMON_ALLERGENS.find(cat =>
      (CATEGORY_ALIASES[cat] || []).some(alias => alias === clean)
    ) || null;
  }

  const isPredefinedAllergy = (a) => COMMON_ALLERGENS.some(p => p.toLowerCase() === a.toLowerCase());

  // Anything the user typed that is not one of the seven categories. These
  // render as removable chips so they can actually be seen and undone —
  // previously they were saved but invisible, leaving no way to remove them.
  const customAllergies = selectedAllergies.filter(a => !isPredefinedAllergy(a));

  // Validation function
  const validateForm = useCallback(() => {
    const newErrors = { age: '' };
    
    // Age validation
    const ageValue = Number(form.age);
    if (form.age !== '' && (isNaN(ageValue) || ageValue < 18)) {
      newErrors.age = 'Age must be 18 or above';
    }
    
    setErrors(newErrors);
    return Object.values(newErrors).every(e => e === '');
  }, [form.age]);

  // Check if form has any errors
  const hasErrors = Object.values(errors).some(e => e !== '');

  function toggleAllergen(allergen) {
    setSelectedAllergies(prev => {
      const exists = prev.some(a => a.toLowerCase() === allergen.toLowerCase());
      if (exists) {
        return prev.filter(a => a.toLowerCase() !== allergen.toLowerCase());
      } else {
        return [...prev, allergen];
      }
    });
  }

  function addCustomAllergy(value) {
    const category = resolveCategory(value);
    // Belongs to one of the seven categories: select it and skip the chip,
    // matching what the backend will store.
    if (category) {
      setSelectedAllergies(prev =>
        prev.some(a => a.toLowerCase() === category.toLowerCase()) ? prev : [...prev, category]
      );
      return;
    }
    setSelectedAllergies(prev =>
      prev.some(a => a.toLowerCase() === value.toLowerCase()) ? prev : [...prev, value]
    );
  }

  function removeCustomAllergy(value) {
    setSelectedAllergies(prev => prev.filter(a => a !== value));
  }

  function handleSave(e) {
    e.preventDefault();
    if (!validateForm()) return;
    
    updateProfile({
      ...form,
      age: Number(form.age),
      height_cm: Number(form.height_cm),
      weight_kg: Number(form.weight_kg),
      allergies: selectedAllergies,
    });
    addToast('Profile updated successfully', 'success');
    onClose();
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" id="edit-profile-modal">
        <div className="modal-header">
          <span className="modal-title">Edit Profile</span>
          <button className="modal-close" onClick={onClose}><X size={14} /></button>
        </div>
        <form onSubmit={handleSave}>
          <div className="modal-body">
            {[
              { key: 'name', label: 'Name', type: 'text' },
              { key: 'age', label: 'Age', type: 'number', error: errors.age },
              { key: 'height_cm', label: 'Height (cm)', type: 'number' },
              { key: 'weight_kg', label: 'Weight (kg)', type: 'number' },
            ].map(f => (
              <div className="form-group" key={f.key}>
                <label className="form-label">{f.label}</label>
                <input
                  className={`form-input${f.error ? ' form-input-error' : ''}`}
                  type={f.type}
                  value={form[f.key]}
                  onChange={e => {
                    setForm(p => ({ ...p, [f.key]: e.target.value }));
                    if (f.key === 'age') {
                      const ageValue = Number(e.target.value);
                      if (e.target.value !== '' && (isNaN(ageValue) || ageValue < 18)) {
                        setErrors(prev => ({ ...prev, age: 'Age must be 18 or above' }));
                      } else {
                        setErrors(prev => ({ ...prev, age: '' }));
                      }
                    }
                  }}
                  onBlur={validateForm}
                />
                {f.error && (
                  <div className="form-error" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, color: 'var(--red-600)', fontSize: 'var(--text-xs)' }}>
                    {f.error}
                  </div>
                )}
              </div>
            ))}
            <div className="form-group">
              <label className="form-label">Activity Level</label>
              <select className="form-select" value={form.activity_level} onChange={e => setForm(p => ({ ...p, activity_level: e.target.value }))}>
                <option value="low">Low (Sedentary)</option>
                <option value="medium">Moderate (Active)</option>
                <option value="high">High (Athletic)</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Goal</label>
              <select className="form-select" value={form.goal} onChange={e => setForm(p => ({ ...p, goal: e.target.value }))}>
                <option value="lose">Lose Weight</option>
                <option value="maintain">Maintain Weight</option>
                <option value="gain">Gain Weight</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Diet Type</label>
              <select className="form-select" value={form.diet_type} onChange={e => setForm(p => ({ ...p, diet_type: e.target.value }))}>
                <option value="veg">Vegetarian</option>
                <option value="non-veg">Non-Vegetarian</option>
                <option value="vegan">Vegan</option>
              </select>
            </div>

            {/* Allergies & Restrictions */}
            <div className="form-group">
              <label className="form-label" style={{ margin: 0 }}>Allergies & Restrictions</label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                {COMMON_ALLERGENS.map(item => {
                  const isChecked = selectedAllergies.some(a => a.toLowerCase() === item.toLowerCase());
                  return (
                    <button
                      key={item}
                      type="button"
                      className={`option-btn${isChecked ? ' selected' : ''}`}
                      onClick={() => toggleAllergen(item)}
                      style={{ padding: '5px 11px', fontSize: 12 }}
                      id={`allergen-toggle-${item.toLowerCase()}`}
                    >
                      {isChecked ? '✓ ' : '+ '}{item}
                    </button>
                  );
                })}
              </div>
              <TagInput
                tags={customAllergies}
                onAdd={addCustomAllergy}
                onRemove={removeCustomAllergy}
                placeholder="Type other allergy and press Enter..."
                allowAlphaOnly
                id="custom-allergy-input"
              />
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-outline btn-sm" onClick={onClose}>Cancel</button>
            <button 
              type="submit" 
              className={`btn btn-primary btn-sm${hasErrors ? ' disabled' : ''}`}
              id="save-profile-btn"
              disabled={hasErrors}
            >
              Save Changes
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}