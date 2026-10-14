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

  // Navigating anywhere closes the drawer. Done during render rather than in
  // an effect: React's documented "adjust state when a prop changes" pattern,
  // which avoids a wasted extra render pass on every navigation.
  const [lastPath, setLastPath] = useState(location.pathname);
  if (lastPath !== location.pathname) {
    setLastPath(location.pathname);
    if (mobileOpen) setMobileOpen(false);
  }

  // The drawer is only reachable below 768px (the hamburger is display:none
  // above it), so this only ever engages on a small screen. Without it the
  // page keeps scrolling behind the open menu.
  useEffect(() => {
    if (!mobileOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [mobileOpen]);

  // Escape closes the drawer — expected of any overlay menu.
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e) => {
      if (e.key === 'Escape') setMobileOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileOpen]);

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
