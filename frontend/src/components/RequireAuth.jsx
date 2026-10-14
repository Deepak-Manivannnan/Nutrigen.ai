import React from 'react';
import { Navigate } from 'react-router-dom';
import { useApp } from '../context/AppContext';
import AuthLoadingScreen from './AuthLoadingScreen';

/**
 * Route guard for every page under /app.
 *
 * The app runs entirely on a client-side router, which means the URL alone
 * decides what renders. Without a guard here, anyone could reach /app/home by
 * typing the address, pressing the browser's Forward button after signing out,
 * or opening a stale bookmark - the app had no check at the entrance, it only
 * checked once at startup.
 *
 * This re-checks on every navigation:
 *   no session          -> sign-in
 *   session, no profile -> onboarding (signed up but never finished the form)
 *   both                -> let them in
 *
 * `authReady` is awaited first. Sign-in and onboarding both navigate the
 * moment their async work resolves, so without this the guard would catch
 * them mid-flight - a user whose profile has not landed yet - and bounce them
 * backwards.
 */
export default function RequireAuth({ children }) {
  const { user, profile, authReady } = useApp();

  if (!authReady) return <AuthLoadingScreen />;

  if (!user) {
    return <Navigate to="/signin" replace />;
  }

  if (!profile) {
    return <Navigate to="/onboarding" replace />;
  }

  return children;
}