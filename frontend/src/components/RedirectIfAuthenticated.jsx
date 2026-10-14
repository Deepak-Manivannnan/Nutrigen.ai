import React from 'react';
import { Navigate } from 'react-router-dom';
import { useApp } from '../context/AppContext';
import AuthLoadingScreen from './AuthLoadingScreen';

/**
 * The mirror image of RequireAuth, for the public auth pages.
 *
 * A login form should never render for somebody who is already signed in. The
 * browser's Back button can still land on /signin - history is not something a
 * site can delete from - so rather than trying to remove the entry, this makes
 * the page close itself: arrive already authenticated, and you are sent
 * straight back into the app.
 *
 * The redirect REPLACES the entry rather than pushing a new one. That is what
 * stops this turning into a Back loop - arriving on /signin consumes it, so
 * the next Back goes wherever the user genuinely came from instead of
 * bouncing off /signin forever.
 *
 * Pairs with navigating into the app with `replace` (see the auth pages), so
 * the entry is normally never created in the first place. This covers what
 * `replace` cannot: old bookmarks, stale tabs, sessions that outlived a login.
 */
export default function RedirectIfAuthenticated({ children }) {
  const { user, profile, authReady } = useApp();

  if (!authReady) return <AuthLoadingScreen />;

  if (user && profile) {
    return <Navigate to="/app/home" replace />;
  }

  // Signed in but never finished onboarding - send them there, not home, or
  // RequireAuth would bounce them straight back out to /signin.
  if (user && !profile) {
    return <Navigate to="/onboarding" replace />;
  }

  return children;
}