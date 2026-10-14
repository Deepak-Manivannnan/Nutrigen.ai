import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Mail, Lock, Eye, EyeOff } from 'lucide-react';
import { useApp } from '../context/AppContext';
import nutrigenLogo from '../assets/nutrigen_logo.png';

export default function SignInPage() {
  const navigate = useNavigate();
  const { signIn, addToast } = useApp();
  const [form, setForm] = useState({ email: '', password: '' });
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  function handleChange(e) {
    const { name, value } = e.target;
    setForm(p => ({ ...p, [name]: value }));
    setError('');
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.email || !form.password) {
      setError('Please enter your email and password.');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: form.email.trim(), password: form.password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Invalid email or password.');
      }
      const data = await res.json();
      signIn(data);
      addToast(`Welcome back, ${data.name}!`, 'success');
      if (data.has_profile) {
        // replace, not push: the sign-in page must not be left in history, or
        // Back from Home lands the user on a login form they already passed.
        navigate('/app/home', { replace: true });
      } else {
        navigate('/onboarding', { replace: true });
      }
    } catch (err) {
      setError(err.message || 'Unable to sign in. Please check your credentials.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page" id="signin-page">
      <div className="auth-card">
        <div className="auth-logo">
          <img
            src={nutrigenLogo}
            alt="Nutrigen.ai Logo"
            style={{ height: 36, width: 'auto', display: 'block', objectFit: 'contain' }}
          />
          <div className="auth-logo-text"><span className="nutri">Nutri</span><span className="gen">gen.ai</span></div>
        </div>

        <h1 className="auth-title">Welcome back</h1>
        <p className="auth-subtitle">Sign in to your account</p>

        {error && (
          <div style={{ background: 'var(--color-error-bg)', border: '1px solid #fca5a5', borderRadius: 'var(--radius-md)', padding: '10px 14px', fontSize: 'var(--text-sm)', color: 'var(--color-error)', marginBottom: 'var(--space-4)' }}>
            {error}
          </div>
        )}

        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          <div className="form-group">
            <div className="form-input-icon">
              <Mail className="icon" size={15} />
              <input
                id="signin-email"
                name="email"
                type="email"
                className="form-input"
                placeholder="Email address"
                value={form.email}
                onChange={handleChange}
                autoComplete="email"
              />
            </div>
          </div>

          <div className="form-group">
            <div className="form-input-icon" style={{ position: 'relative' }}>
              <Lock className="icon" size={15} />
              <input
                id="signin-password"
                name="password"
                type={showPass ? 'text' : 'password'}
                className="form-input"
                placeholder="Password"
                value={form.password}
                onChange={handleChange}
                autoComplete="current-password"
                style={{ paddingRight: 42 }}
              />
              <button
                type="button"
                onClick={() => setShowPass(v => !v)}
                style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--gray-400)', padding: 4 }}
                tabIndex={-1}
                id="toggle-password"
              >
                {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            style={{ width: '100%', padding: '13px', marginTop: 'var(--space-2)' }}
            disabled={loading}
            id="signin-submit"
          >
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>

        <div className="auth-footer">
          Don&apos;t have an account?{' '}
          <button onClick={() => navigate('/signup')} id="goto-signup">Create one</button>
        </div>
      </div>
    </div>
  );
}
