import React, { useState, useEffect, useRef } from 'react';
import { ChevronDown } from 'lucide-react';
import { sortPlansNewestFirst } from '../utils/planHelpers';

/**
 * Toggle-bar plan selector shared by the Plans and Grocery tabs so both look
 * and behave identically.
 *
 * Callers pass an already-filtered list — the Grocery tab supplies approved
 * plans only, since drafts have no grocery list yet. Sorting happens here so
 * "recent" means created_at (not last-edited), and the newest entry is the
 * default selection whenever `activePlanId` is absent from the list.
 */
export default function PlanSelect({ plans, activePlanId, onSelect, idPrefix = 'plan-select' }) {
  const [open, setOpen] = useState(false);
  const fieldRef = useRef(null);

  const sorted = sortPlansNewestFirst(plans);

  const active = sorted.find(p => p.id === activePlanId) || sorted[0] || null;

  // Dismiss on outside click or Escape.
  useEffect(() => {
    if (!open) return;
    function handleDocPointer(e) {
      if (fieldRef.current && !fieldRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    function handleDocKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', handleDocPointer);
    document.addEventListener('touchstart', handleDocPointer);
    document.addEventListener('keydown', handleDocKey);
    return () => {
      document.removeEventListener('mousedown', handleDocPointer);
      document.removeEventListener('touchstart', handleDocPointer);
      document.removeEventListener('keydown', handleDocKey);
    };
  }, [open]);

  if (!active) return null;

  return (
    <div className="plan-select" ref={fieldRef}>
      <span className="plan-select-label" id={`${idPrefix}-label`}>Select plan</span>
      <div className="plan-select-field">
        <button
          type="button"
          className="plan-select-toggle"
          onClick={() => setOpen(v => !v)}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-labelledby={`${idPrefix}-label`}
          id={`${idPrefix}-toggle`}
        >
          <span className="plan-select-current">{active.name}</span>
          <ChevronDown size={16} className={`plan-select-caret${open ? ' open' : ''}`} />
        </button>

        {open && (
          <div className="plan-select-menu" role="listbox" aria-label="Saved meal plans" id={`${idPrefix}-menu`}>
            {sorted.map(p => (
              <button
                key={p.id}
                type="button"
                id={`plan-tab-${p.id}`}
                role="option"
                aria-selected={p.id === active.id}
                className={`plan-select-option${p.id === active.id ? ' active' : ''}`}
                onClick={() => {
                  onSelect(p.id);
                  setOpen(false);
                }}
              >
                <span className="plan-select-option-main">
                  <span className="plan-select-option-name">{p.name}</span>
                  <span className="plan-select-option-meta">
                    {p.days} {p.days === 1 ? 'day' : 'days'} · {p.calorieTarget?.toLocaleString()} kcal/day · {p.dietType}
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}