import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, Check } from 'lucide-react';

/**
 * A dropdown that actually matches the theme.
 *
 * The previous markup was a native <select>. Its closed state could be styled,
 * but the popup list is drawn by the operating system and cannot be touched by
 * CSS — hence the blue/square/OS-styled menu that clashed with the green design
 * system. This renders the list ourselves so the open state is ours too.
 *
 * Behaviour is deliberately kept equivalent to a native select: Enter/Space or
 * click opens, Arrow keys move, Enter picks, Escape closes, and a click outside
 * closes. Options keep their exact labels — only the paint changes.
 *
 * `options` accepts plain strings or { value, label } objects, matching what
 * both call sites already pass.
 */
export default function ThemedSelect({ value, onChange, options, id, ariaLabel }) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  // Set only by close paths that should hand focus back to the trigger (option
  // picked, Escape). An outside click clears it, because that click decides
  // where focus goes and we must not yank it back afterwards.
  const restoreFocusRef = useRef(false);

  const items = options.map(o => (typeof o === 'string' ? { value: o, label: o } : o));
  const selected = items.find(o => o.value === value);
  const selectedLabel = selected ? selected.label : (items[0]?.label ?? '');

  // Close when clicking anywhere outside the control.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        // The click chose where focus goes; do not override it on close.
        restoreFocusRef.current = false;
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  // Return focus to the trigger only when closing should keep focus here —
  // picking an option or pressing Escape. Never on mount, so opening the
  // Edit Profile modal cannot steal focus into a dropdown.
  useEffect(() => {
    if (open) return;
    if (restoreFocusRef.current) {
      restoreFocusRef.current = false;
      triggerRef.current?.focus();
    }
  }, [open]);

  function closeAndRestoreFocus() {
    restoreFocusRef.current = true;
    setOpen(false);
  }

  function commit(option, index) {
    onChange(option.value);
    setActiveIndex(index);
    closeAndRestoreFocus();
  }

  function handleTriggerKeyDown(e) {
    // Only opens the menu. Once it is open the root handler takes over, so
    // Arrow/Enter/Space are not swallowed here.
    if (open) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      const current = items.findIndex(o => o.value === value);
      setActiveIndex(current >= 0 ? current : 0);
      setOpen(true);
    }
  }

  // Mounted on the root, not on the <ul>: focus deliberately stays on the
  // trigger button, so a handler on the list would never receive these keys and
  // arrow navigation would silently do nothing. Bubbling from the button means
  // the keys are caught either way.
  function handleMenuKeyDown(e) {
    if (!open) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(i => (i + 1) % items.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(i => (i - 1 + items.length) % items.length);
    } else if (e.key === 'Home') {
      e.preventDefault();
      setActiveIndex(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      setActiveIndex(items.length - 1);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (items[activeIndex]) commit(items[activeIndex], activeIndex);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeAndRestoreFocus();
    } else if (e.key === 'Tab') {
      // Tab moves focus to the next field on purpose, so restoring it to the
      // trigger would fight the user's own navigation.
      restoreFocusRef.current = false;
      setOpen(false);
    }
  }

  return (
    <div className="themed-select" ref={rootRef} onKeyDown={handleMenuKeyDown}>
      <button
        type="button"
        id={id}
        ref={triggerRef}
        className={`themed-select-trigger${open ? ' open' : ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => { setActiveIndex(items.findIndex(o => o.value === value)); setOpen(o => !o); }}
        onKeyDown={handleTriggerKeyDown}
      >
        <span className="themed-select-value">{selectedLabel}</span>
        <ChevronDown size={15} className="themed-select-caret" />
      </button>

      {open && (
        <ul
          className="themed-select-menu"
          role="listbox"
          tabIndex={-1}
          // Focus stays on the trigger, so the list needs its own name for
          // screen readers to announce what it contains.
          aria-label={ariaLabel}
        >
          {items.map((option, index) => {
            const isSelected = option.value === value;
            return (
              <li
                key={option.value}
                role="option"
                aria-selected={isSelected}
                className={
                  'themed-select-option'
                  + (isSelected ? ' selected' : '')
                  + (index === activeIndex ? ' active' : '')
                }
                // onMouseDown beats the document-level close handler, so the
                // pick is not lost to the click-outside check firing first.
                onMouseDown={e => { e.preventDefault(); commit(option, index); }}
                onMouseEnter={() => setActiveIndex(index)}
              >
                <span className="themed-select-check">{isSelected && <Check size={13} />}</span>
                <span>{option.label}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
