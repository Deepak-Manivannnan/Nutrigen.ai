import React, { useState } from 'react';

/**
 * Free-text entry that turns each typed item into a removable chip.
 *
 * Shared by the onboarding step and the sidebar Edit Profile modal so both
 * behave identically: Enter or comma commits the current text, clicking the
 * chip container focuses the input, and each chip removes itself with ×.
 */
export default function TagInput({ tags, onAdd, onRemove, placeholder, allowAlphaOnly = false, id }) {
  const [input, setInput] = useState('');

  function handleKeyDown(e) {
    if ((e.key === 'Enter' || e.key === ',') && input.trim()) {
      e.preventDefault();
      const val = input.trim().replace(/,$/, '');
      if (val && !tags.some(t => t.toLowerCase() === val.toLowerCase())) onAdd(val);
      setInput('');
    }
  }

  function handleChange(e) {
    let val = e.target.value;
    if (allowAlphaOnly) {
      val = val.replace(/[^a-zA-Z\s,-]/g, '');
    }
    setInput(val);
  }

  return (
    <div className="tag-input-container" onClick={e => e.currentTarget.querySelector('input')?.focus()}>
      {tags.map((t, i) => (
        <span key={`${t}-${i}`} className="tag-chip">
          {t}
          <button
            type="button"
            className="tag-chip-remove"
            onClick={() => onRemove(t)}
            aria-label={`Remove ${t}`}
          >
            ×
          </button>
        </span>
      ))}
      <input
        className="tag-raw-input"
        placeholder={tags.length === 0 ? placeholder : ''}
        value={input}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        id={id}
      />
    </div>
  );
}