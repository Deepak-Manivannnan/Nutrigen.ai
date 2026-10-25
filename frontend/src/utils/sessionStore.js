/**
 * Where the signed-in session lives.
 *
 * Two kinds of storage, on purpose:
 *
 * - Persistent (localStorage): the short-lived access token, the long-lived
 *   refresh key and the cached identity. They survive closing the tab or the
 *   browser, so coming back to the app signs you straight in (the same way
 *   Gmail or ChatGPT behave). The refresh key is what makes that safe: it is
 *   revocable on the server, rotates every time it is used, and Sign out
 *   cancels it. Only an explicit Sign out, or staying away for the refresh
 *   key's whole lifetime, ends the session.
 *
 * - Per tab (sessionStorage): the "waiting for a reply" note. It answers "is
 *   THIS tab waiting on a run?", so it must not outlive the tab - a note that
 *   survived would only produce a stale "that run didn't finish" message.
 *
 * Per-user content (messages, pending workflow) stays in localStorage keyed by
 * user id, as before.
 */

/** Short-lived access token, sent with every request. */
export const TOKEN_KEY = 'nutrigen_auth_token';
/** Long-lived key that is only ever shown to /auth/refresh. */
export const REFRESH_KEY = 'nutrigen_refresh_token';
/** Cached identity, so the app can render before the profile round-trips. */
export const USER_KEY = 'nutrigen_auth_user';
/** A run started by this tab that has not delivered yet. */
export const inflightKey = userId => `nutrigen_inflight_${userId}`;

/**
 * Read a persistent key (survives closing the browser).
 *
 * On a miss it also looks in sessionStorage once, to carry over a session that
 * was saved there before sign-in became persistent, so deploying this does not
 * sign everyone out.
 */
export function readPersistent(key) {
  try {
    const current = localStorage.getItem(key);
    if (current !== null) return current;

    const legacy = sessionStorage.getItem(key);
    if (legacy === null) return null;

    localStorage.setItem(key, legacy);
    sessionStorage.removeItem(key);
    return legacy;
  } catch {
    // Storage unavailable (private browsing, disabled cookies): behave as
    // signed out rather than crashing the app on boot.
    return null;
  }
}

/** Write a persistent key; a null value clears it from both stores. */
export function writePersistent(key, value) {
  try {
    if (value === null || value === undefined) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, value);
    }
    sessionStorage.removeItem(key);
  } catch {
    // Private browsing: the session simply will not survive.
  }
}

/**
 * Read a per-tab key (gone when the tab closes).
 *
 * On a miss it also looks in localStorage once and moves what it finds across,
 * for values saved there by an earlier version.
 */
export function readSession(key) {
  try {
    const current = sessionStorage.getItem(key);
    if (current !== null) return current;

    const legacy = localStorage.getItem(key);
    if (legacy === null) return null;

    sessionStorage.setItem(key, legacy);
    localStorage.removeItem(key);
    return legacy;
  } catch {
    return null;
  }
}

/** Write a per-tab key; a null value clears it from both stores. */
export function writeSession(key, value) {
  try {
    if (value === null || value === undefined) {
      sessionStorage.removeItem(key);
    } else {
      sessionStorage.setItem(key, value);
    }
    localStorage.removeItem(key);
  } catch {
    // Private browsing: the session simply will not survive.
  }
}
