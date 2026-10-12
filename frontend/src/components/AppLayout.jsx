import React from 'react';
import { Navigate, Outlet } from 'react-router-dom';
import { useApp } from '../context/AppContext';
import Sidebar from './Sidebar';
import ToastContainer from './ToastContainer';
import { Logo } from './Logo';
import { Menu } from 'lucide-react';

export function ProtectedRoute() {
  const { user } = useApp();
  
  if (!user) {
    return <Navigate to="/signin" replace />;
  }
  
  return <Outlet />;
}

export function PublicOnlyRoute({ children }) {
  const { user } = useApp();
  
  if (user) {
    return <Navigate to="/app/home" replace />;
  }
  
  return children;
}

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false);

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
