import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppProvider } from './context/AppContext';
import AppLayout, { ProtectedRoute, PublicOnlyRoute } from './components/AppLayout';
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
          <Route path="/" element={<LandingPage />} />
          <Route path="/signin" element={<PublicOnlyRoute><SignInPage /></PublicOnlyRoute>} />
          <Route path="/signup" element={<PublicOnlyRoute><SignUpPage /></PublicOnlyRoute>} />
          <Route path="/onboarding" element={<PublicOnlyRoute><OnboardingPage /></PublicOnlyRoute>} />

          {/* Authenticated Application Routes */}
          <Route path="/app" element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
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
