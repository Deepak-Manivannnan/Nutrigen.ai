import React, { useEffect, useRef, useState } from 'react';
import { API_BASE } from '../utils/api';

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || '';
const SCRIPT_SRC = 'https://accounts.google.com/gsi/client';

let scriptPromise = null;
function loadGoogleScript() {
  if (window.google?.accounts?.oauth2) return Promise.resolve();
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

function GoogleLogo() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 38.2 44 33 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}

/**
 * Our own "Continue with Google" button.
 *
 * Google's ready-made button turns into "Continue as <name>" once the browser
 * remembers the account. This button always reads the same; clicking opens
 * Google's account picker in a popup (prompt: select_account), and the access
 * token Google returns is checked by the backend (/auth/google) before any
 * session is issued. Renders nothing when VITE_GOOGLE_CLIENT_ID is not set.
 */
export default function GoogleButton({ onSuccess, onError, disabled = false }) {
  const clientRef = useRef(null);
  const handlers = useRef({ onSuccess, onError });
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  handlers.current = { onSuccess, onError };

  useEffect(() => {
    if (!CLIENT_ID) return undefined;
    let cancelled = false;

    async function exchange(response) {
      if (response.error || !response.access_token) {
        setBusy(false);
        if (response.error !== 'access_denied') {
          handlers.current.onError?.('Google sign-in failed. Please try again.');
        }
        return;
      }
      try {
        const res = await fetch(`${API_BASE}/auth/google`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ access_token: response.access_token }),
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

    loadGoogleScript()
      .then(() => {
        if (cancelled) return;
        clientRef.current = window.google.accounts.oauth2.initTokenClient({
          client_id: CLIENT_ID,
          scope: 'openid email profile',
          prompt: 'select_account',
          callback: exchange,
          // Popup closed or blocked: just stop the spinner, no error banner.
          error_callback: () => setBusy(false),
        });
        setReady(true);
      })
      .catch(() => handlers.current.onError?.("Google sign-in couldn't load. Check your connection and refresh."));

    return () => { cancelled = true; };
  }, []);

  if (!CLIENT_ID) return null;

  function handleClick() {
    if (!clientRef.current) return;
    setBusy(true);
    clientRef.current.requestAccessToken();
  }

  return (
    <button
      type="button"
      className="google-btn"
      onClick={handleClick}
      disabled={!ready || busy || disabled}
      id="google-signin"
    >
      <GoogleLogo />
      <span>{busy ? 'Signing in with Google…' : 'Continue with Google'}</span>
    </button>
  );
}
