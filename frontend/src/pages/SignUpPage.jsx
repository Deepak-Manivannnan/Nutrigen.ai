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

  function handleSocial(provider) {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      signIn({ id: 1, email: `user@${provider.toLowerCase()}.com`, name: 'New User' });
      addToast(`Signed up with ${provider}`, 'success');
      navigate('/onboarding');
    }, 800);
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

        <div className="auth-divider"><span>or continue with</span></div>
        <div className="social-buttons">
          <button className="social-btn" onClick={() => handleSocial('Google')} disabled={loading} id="google-signup">
            <svg width="16" height="16" viewBox="0 0 24 24"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4" /><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" /><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" /><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" /></svg>
            Google
          </button>
          <button className="social-btn" onClick={() => handleSocial('Microsoft')} disabled={loading} id="microsoft-signup">
            <svg width="16" height="16" viewBox="0 0 21 21"><rect x="1" y="1" width="9" height="9" fill="#f25022" /><rect x="11" y="1" width="9" height="9" fill="#7fba00" /><rect x="1" y="11" width="9" height="9" fill="#00a4ef" /><rect x="11" y="11" width="9" height="9" fill="#ffb900" /></svg>
            Microsoft
          </button>
        </div>

        <div className="auth-footer">
          Already have an account?{' '}
          <button onClick={() => navigate('/signin')} id="goto-signin">Sign in</button>
        </div>
      </div>
    </div>
  );
}
