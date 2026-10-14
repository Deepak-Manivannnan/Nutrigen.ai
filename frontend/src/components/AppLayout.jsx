import React, { useState, useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import ToastContainer from './ToastContainer';
import { useApp } from '../context/AppContext';
import { Logo } from './Logo';
import { Menu } from 'lucide-react';

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const { setActivePath, setChatBadge, setPlansBadge, setGroceryBadge } = useApp();

  // Publish the visible route to AppContext (badge helpers need it) and drop
  // the dot for whichever tab was just opened. Centralised here so the dot
  // clears on EVERY way into a tab — the sidebar, a "View Meal Plan" link, a
  // plan:// deep link, the browser back button — not just sidebar clicks.
  useEffect(() => {
    const path = location.pathname;
    setActivePath(path);
    if (path === '/app/chat') setChatBadge(false);
    if (path === '/app/plans') setPlansBadge(false);
    if (path === '/app/grocery') setGroceryBadge(false);
  }, [location.pathname, setActivePath, setChatBadge, setPlansBadge, setGroceryBadge]);

  return (
    <div className="app-layout">
      <Sidebar mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />

      <main className="main-content">
        {/* Mobile Topbar */}
        <header className="mobile-topbar">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
            id="mobile-nav-toggle"
            style={{ padding: '6px 8px' }}
          >
            <Menu size={20} />
          </button>
          <Logo size="sm" />
          <div style={{ width: 36 }} />
        </header>

        <Outlet />
      </main>

      <ToastContainer />
    </div>
  );
}
