import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Mail, Lock, Eye, EyeOff, User } from 'lucide-react';
import { useApp } from '../context/AppContext';
import nutrigenLogo from '../assets/nutrigen_logo.png';

export default function SignUpPage() {
  const navigate = useNavigate();
  const { signIn, addToast } = useApp();
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' });
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  function handleChange(e) {
    setForm(p => ({ ...p, [e.target.name]: e.target.value }));
    setError('');
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.name || !form.email || !form.password) {
      setError('Please fill in all fields.');
      return;
    }
    if (form.password.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }
    if (form.password !== form.confirm) {
      setError('Passwords do not match.');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name.trim(),
          email: form.email.trim(),
          password: form.password,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to create account.');
      }
      const data = await res.json();
      await signIn(data);
      addToast('Account created! Complete your profile.', 'success');
      navigate('/onboarding');
    } catch (err) {
      setError(err.message || 'Unable to sign up. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page" id="signup-page">
      <div className="auth-card">
        <div className="auth-logo">
          <img
            src={nutrigenLogo}
            alt="Nutrigen.ai Logo"
            style={{ height: 36, width: 'auto', display: 'block', objectFit: 'contain' }}
          />
          <div className="auth-logo-text"><span className="nutri">Nutri</span><span className="gen">gen.ai</span></div>
        </div>

        <h1 className="auth-title">Create your account</h1>
        <p className="auth-subtitle">Start your personalized meal planning journey</p>

        {error && (
          <div style={{ background: 'var(--color-error-bg)', border: '1px solid #fca5a5', borderRadius: 'var(--radius-md)', padding: '10px 14px', fontSize: 'var(--text-sm)', color: 'var(--color-error)', marginBottom: 'var(--space-4)' }}>
            {error}
          </div>
        )}

        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          <div className="form-group">
            <div className="form-input-icon">
              <User className="icon" size={15} />
              <input id="signup-name" name="name" type="text" className="form-input" placeholder="Full name" value={form.name} onChange={handleChange} autoComplete="name" />
            </div>
          </div>
          <div className="form-group">
            <div className="form-input-icon">
              <Mail className="icon" size={15} />
              <input id="signup-email" name="email" type="email" className="form-input" placeholder="Email address" value={form.email} onChange={handleChange} autoComplete="email" />
            </div>
          </div>
          <div className="form-group">
            <div className="form-input-icon" style={{ position: 'relative' }}>
              <Lock className="icon" size={15} />
              <input
                id="signup-password" name="password" type={showPass ? 'text' : 'password'}
                className="form-input" placeholder="Password (min 6 chars)"
                value={form.password} onChange={handleChange}
                style={{ paddingRight: 42 }}
              />
              <button type="button" onClick={() => setShowPass(v => !v)}
                style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--gray-400)', padding: 4 }}
                tabIndex={-1}
              >
                {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>
          <div className="form-group">
            <div className="form-input-icon">
              <Lock className="icon" size={15} />
              <input id="signup-confirm" name="confirm" type="password" className="form-input" placeholder="Confirm password" value={form.confirm} onChange={handleChange} />
            </div>
          </div>
          <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '13px' }} disabled={loading} id="signup-submit">
            {loading ? 'Creating account…' : 'Create Account'}
          </button>
        </form>

        <div className="auth-footer">
          Already have an account?{' '}
          <button onClick={() => navigate('/signin')} id="goto-signin">Sign in</button>
        </div>
      </div>
    </div>
  );
}
