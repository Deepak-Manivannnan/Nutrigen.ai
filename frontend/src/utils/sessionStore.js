/**
 * Where the signed-in session lives.
 *
 * sessionStorage rather than localStorage, so closing the tab ends the
 * session: the next person to open this browser gets the landing page instead
 * of walking straight into whoever was signed in before them. With
 * localStorage the credential outlived the tab - closing it was not signing
 * out, and only an explicit Sign out or the 15 minute expiry ever cleared it.
 *
 * A reload keeps it. sessionStorage is per-tab but survives reloads and
 * in-page navigation, which is exactly what the in-flight delivery box needs
 * in order to keep showing "Nutrigen is typing..." and collect the reply after
 * a refresh. It is discarded when the tab goes away, which is the point.
 *
 * Note for mobile: a browser that discards a tab from the background will
 * discard this with it. That costs the session, not any data - messages and
 * plans are stored separately and come back on the next sign-in.
 *
 * Per-user content (messages, pending workflow) deliberately stays in
 * localStorage: it is keyed by user id, only ever read once that user is
 * signed in, and it would be a shame to lose a conversation over a closed tab.
 */

/** The credential itself. */
export const TOKEN_KEY = 'nutrigen_auth_token';
/** Cached identity, so the app can render before the profile round-trips. */
export const USER_KEY = 'nutrigen_auth_user';
/** A run started by this tab that has not delivered yet. */
export const inflightKey = userId => `nutrigen_inflight_${userId}`;

/**
 * Read a session key.
 *
 * On a miss it also looks in localStorage once, to carry over a session from
 * before this change, and moves it across rather than copying - a credential
 * left behind in localStorage would sit there where closing the tab could
 * never reach it.
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
    // Storage unavailable (private browsing, disabled cookies): behave as
    // signed out rather than crashing the app on boot.
    return null;
  }
}

/** Write a session key; a null value clears it from both stores. */
export function writeSession(key, value) {
  try {
    if (value === null || value === undefined) {
      sessionStorage.removeItem(key);
    } else {
      sessionStorage.setItem(key, value);
    }
    // Always sweep localStorage so no copy of the credential survives here,
    // whatever wrote it.
    localStorage.removeItem(key);
  } catch {
    // Private browsing: the session simply will not survive.
  }
}
