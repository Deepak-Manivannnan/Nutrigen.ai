import React, { useEffect, useRef, useState } from 'react';
import { API_BASE } from '../utils/api';

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || '';
const SCRIPT_SRC = 'https://accounts.google.com/gsi/client';

let scriptPromise = null;
function loadGoogleScript() {
  if (window.google?.accounts?.id) return Promise.resolve();
  if (!scriptPromise) {
    scriptPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = SCRIPT_SRC;
      s.async = true;
      s.defer = true;
      s.onload = () => resolve();
      s.onerror = () => { scriptPromise = null; reject(new Error('Google script failed to load')); };
      document.head.appendChild(s);
    });
  }
  return scriptPromise;
}

export const googleSignInEnabled = Boolean(CLIENT_ID);

/**
 * Google's official "Continue with Google" button (Google Identity Services).
 *
 * Google returns a signed ID token; the backend checks it and answers with the
 * same session the email sign-in returns, so `onSuccess(data)` is shared.
 * Renders nothing when VITE_GOOGLE_CLIENT_ID is not set.
 */
export default function GoogleButton({ onSuccess, onError, disabled = false }) {
  const slotRef = useRef(null);
  const handlers = useRef({ onSuccess, onError });
  const [busy, setBusy] = useState(false);
  handlers.current = { onSuccess, onError };

  useEffect(() => {
    if (!CLIENT_ID) return undefined;
    let cancelled = false;

    async function handleCredential(response) {
      setBusy(true);
      try {
        const res = await fetch(`${API_BASE}/auth/google`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ credential: response.credential }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Google sign-in failed. Please try again.');
        await handlers.current.onSuccess(data);
      } catch (err) {
        handlers.current.onError?.(err.message || 'Google sign-in failed. Please try again.');
      } finally {
        setBusy(false);
      }
    }

    function render() {
      const slot = slotRef.current;
      if (!slot || cancelled) return;
      window.google.accounts.id.initialize({
        client_id: CLIENT_ID,
        callback: handleCredential,
        ux_mode: 'popup',
        auto_select: false,
        cancel_on_tap_outside: true,
      });
      slot.innerHTML = '';
      // Match the form's width (Google allows 200-400px).
      const width = Math.max(200, Math.min(400, Math.floor(slot.parentElement?.clientWidth || 320)));
      window.google.accounts.id.renderButton(slot, {
        type: 'standard',
        theme: 'outline',
        size: 'large',
        text: 'continue_with',
        shape: 'rectangular',
        logo_alignment: 'center',
        width,
      });
    }

    loadGoogleScript()
      .then(render)
      .catch(() => handlers.current.onError?.("Google sign-in couldn't load. Check your connection and refresh."));

    return () => { cancelled = true; };
  }, []);

  if (!CLIENT_ID) return null;
  return (
    <div className={`google-btn-wrap${busy || disabled ? ' is-busy' : ''}`} aria-busy={busy}>
      <div ref={slotRef} className="google-btn-slot" />
      {busy && <div className="google-btn-status">Signing in with Google…</div>}
    </div>
  );
}
