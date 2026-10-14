import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppProvider } from './context/AppContext';
import AppLayout from './components/AppLayout';
import RequireAuth from './components/RequireAuth';
import RedirectIfAuthenticated from './components/RedirectIfAuthenticated';
import LandingPage from './pages/LandingPage';
import SignInPage from './pages/SignInPage';
import SignUpPage from './pages/SignUpPage';
import OnboardingPage from './pages/OnboardingPage';
import HomePage from './pages/HomePage';
import ChatPage from './pages/ChatPage';
import PlansPage from './pages/PlansPage';
import GroceryPage from './pages/GroceryPage';

export default function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <Routes>
          {/* Public & Auth Routes */}
          {/* Every public entry point self-closes for a signed-in user. The
              redirect REPLACES the history entry, so pressing Back consumes it
              rather than stacking: Home becomes the floor, and no amount of
              Back reveals a login form or the marketing page again.
              Sign Out is unaffected - it clears the user first, so / renders
              normally once you're logged out. */}
          <Route
            path="/"
            element={
              <RedirectIfAuthenticated>
                <LandingPage />
              </RedirectIfAuthenticated>
            }
          />
          <Route
            path="/signin"
            element={
              <RedirectIfAuthenticated>
                <SignInPage />
              </RedirectIfAuthenticated>
            }
          />
          <Route
            path="/signup"
            element={
              <RedirectIfAuthenticated>
                <SignUpPage />
              </RedirectIfAuthenticated>
            }
          />
          <Route path="/onboarding" element={<OnboardingPage />} />

          {/* Authenticated Application Routes */}
          <Route
            path="/app"
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          >
            <Route index element={<Navigate to="/app/home" replace />} />
            <Route path="home" element={<HomePage />} />
            <Route path="chat" element={<ChatPage />} />
            <Route path="plans" element={<PlansPage />} />
            <Route path="grocery" element={<GroceryPage />} />
          </Route>

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AppProvider>
  );
}
