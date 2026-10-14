import React from 'react';

/**
 * Shown while we work out whether a session exists.
 *
 * A real screen rather than a blank frame: on a cold start there is a brief
 * moment where the stored identity is known but the profile behind it has not
 * arrived yet. Rendering the app then would flash an empty dashboard before
 * redirecting, which reads as "broken" rather than "one moment".
 */
export default function AuthLoadingScreen() {
  return (
    <div className="app-auth-loading" role="status" aria-live="polite">
      <div className="status-spinner" style={{ width: 22, height: 22 }} />
      <span>Loading your workspace…</span>
    </div>
  );
}