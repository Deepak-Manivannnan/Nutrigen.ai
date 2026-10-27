import React, { useEffect, useRef } from 'react';

/**
 * Six one-digit boxes for an emailed code. Typing moves to the next box,
 * Backspace moves back, pasting a full code fills every box, and the first
 * box carries autocomplete="one-time-code" so phones can offer the code.
 */
export default function OtpInput({ value, onChange, length = 6, autoFocus = false, disabled = false }) {
  const refs = useRef([]);
  const digits = Array.from({ length }, (_, i) => value[i] || '');

  useEffect(() => {
    if (autoFocus) refs.current[0]?.focus();
  }, [autoFocus]);

  function setAt(index, digit) {
    const next = digits.slice();
    next[index] = digit;
    onChange(next.join('').slice(0, length));
  }

  function fill(text, start = 0) {
    const clean = text.replace(/\D/g, '');
    if (!clean) return;
    const next = digits.slice();
    for (let i = 0; i < clean.length && start + i < length; i++) next[start + i] = clean[i];
    onChange(next.join(''));
    refs.current[Math.min(start + clean.length, length - 1)]?.focus();
  }

  function handleChange(index, e) {
    const text = e.target.value;
    if (text.length > 1) { fill(text, index); return; } // autofill / fast typing
    const digit = text.replace(/\D/g, '');
    setAt(index, digit);
    if (digit && index < length - 1) refs.current[index + 1]?.focus();
  }

  function handleKeyDown(index, e) {
    if (e.key === 'Backspace' && !digits[index] && index > 0) {
      e.preventDefault();
      setAt(index - 1, '');
      refs.current[index - 1]?.focus();
    } else if (e.key === 'ArrowLeft' && index > 0) {
      refs.current[index - 1]?.focus();
    } else if (e.key === 'ArrowRight' && index < length - 1) {
      refs.current[index + 1]?.focus();
    }
  }

  function handlePaste(index, e) {
    e.preventDefault();
    fill(e.clipboardData.getData('text'), index);
  }

  return (
    <div className="otp-group" role="group" aria-label="Verification code">
      {digits.map((d, i) => (
        <input
          key={i}
          ref={el => { refs.current[i] = el; }}
          className="otp-box"
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          autoComplete={i === 0 ? 'one-time-code' : 'off'}
          maxLength={i === 0 ? length : 1}
          value={d}
          disabled={disabled}
          aria-label={`Digit ${i + 1}`}
          onChange={e => handleChange(i, e)}
          onKeyDown={e => handleKeyDown(i, e)}
          onPaste={e => handlePaste(i, e)}
          onFocus={e => e.target.select()}
        />
      ))}
    </div>
  );
}
