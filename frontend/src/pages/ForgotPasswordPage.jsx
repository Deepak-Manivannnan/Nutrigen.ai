import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Mail, Lock, Eye, EyeOff, CheckCircle2 } from 'lucide-react';
import { useApp } from '../context/AppContext';
import { API_BASE, setAuthToken } from '../utils/api';
import nutrigenLogo from '../assets/nutrigen_logo.png';
import OtpInput from '../components/OtpInput';

async function postJson(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'Something went wrong. Please try again.');
  return data;
}

/**
 * Forgot password: email -> code -> new password. Also how a Google-only
 * account adds a password for email sign-in.
 */
export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { signIn, addToast } = useApp();
  const [step, setStep] = useState('email'); // email | code | password
  const [form, setForm] = useState({ email: location.state?.email || '', code: '', password: '', confirm: '' });
  const [ticket, setTicket] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const t = setTimeout(() => setCooldown(c => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  function handleChange(e) {
    const { name, value } = e.target;
    setForm(p => ({ ...p, [name]: name === 'code' ? value.replace(/\D/g, '').slice(0, 6) : value }));
    setError('');
  }

  async function sendCode(e) {
    e?.preventDefault();
    if (!form.email.trim()) { setError('Please enter your email.'); return; }
    setLoading(true);
    try {
      const data = await postJson('/auth/password/send-code', { email: form.email.trim() });
      setInfo(form.email.trim());
      setCooldown(data.resend_after || 60);
      setForm(p => ({ ...p, code: '' }));
      setStep('code');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function verifyCode(e) {
    e.preventDefault();
    if (form.code.length !== 6) { setError('Please enter the code from your email.'); return; }
    setLoading(true);
    try {
      const data = await postJson('/auth/password/verify-code', { email: form.email.trim(), code: form.code });
      setTicket(data.verification_token);
      setInfo('');
      setStep('password');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function resetPassword(e) {
    e.preventDefault();
    if (form.password.length < 8) { setError('Password must be at least 8 characters.'); return; }
    if (form.password !== form.confirm) { setError('Passwords do not match.'); return; }
    setLoading(true);
    try {
      const data = await postJson('/auth/password/reset', {
        email: form.email.trim(), verification_token: ticket, password: form.password,
      });
      setAuthToken(data.token);
      await signIn(data);
      addToast('Password updated. You are signed in.', 'success');
      navigate(data.has_profile ? '/app/home' : '/onboarding', { replace: true });
    } catch (err) {
      setError(err.message);
      if (/verif/i.test(err.message)) { setTicket(''); setStep('email'); }
    } finally {
      setLoading(false);
    }
  }

  function changeEmail() {
    setStep('email'); setTicket(''); setInfo(''); setError('');
    setForm(p => ({ ...p, code: '', password: '', confirm: '' }));
  }

  const onSubmit = step === 'email' ? sendCode : step === 'code' ? verifyCode : resetPassword;

  return (
    <div className="auth-page" id="forgot-page">
      <div className="auth-card">
        <div className="auth-logo">
          <img src={nutrigenLogo} alt="Nutrigen.ai Logo" style={{ height: 36, width: 'auto', display: 'block', objectFit: 'contain' }} />
          <div className="auth-logo-text"><span className="nutri">Nutri</span><span className="gen">gen.ai</span></div>
        </div>

        <h1 className="auth-title">Reset your password</h1>
        <p className="auth-subtitle">We'll email you a code to confirm it's you</p>

        {error && (
          <div role="alert" style={{ background: 'var(--color-error-bg)', border: '1px solid #fca5a5', borderRadius: 'var(--radius-md)', padding: '10px 14px', fontSize: 'var(--text-sm)', color: 'var(--color-error)', marginBottom: 'var(--space-4)' }}>
            {error}
          </div>
        )}

        <form className="auth-form" onSubmit={onSubmit} noValidate>
          <div className="form-group">
            <div className="form-input-icon" style={{ position: 'relative' }}>
              <Mail className="icon" size={15} />
              <input id="forgot-email" name="email" type="email"
                className={`form-input${step === 'password' ? ' is-verified' : ''}`} placeholder="Email address"
                value={form.email} onChange={handleChange} autoComplete="email" disabled={step !== 'email'}
                aria-label={step === 'password' ? 'Email address (verified)' : 'Email address'} />
              {step === 'password' && (
                <span className="verified-check" title="Email verified" aria-hidden="true"><CheckCircle2 size={17} /></span>
              )}
            </div>
          </div>

          {step === 'email' && (
            <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '13px' }} disabled={loading} id="forgot-send-code">
              {loading ? 'Sending code…' : 'Send code'}
            </button>
          )}

          {step === 'code' && (
            <>
              <p className="auth-sent-note" role="status">
                Check your inbox. We've sent a code to <strong>{info || form.email.trim()}</strong>
              </p>
              <OtpInput value={form.code} onChange={code => { setForm(p => ({ ...p, code })); setError(''); }} autoFocus disabled={loading} />
              <div className="code-actions">
                <button type="button" className="auth-link" onClick={changeEmail}>Change email</button>
                <button type="button" className="auth-link" onClick={sendCode} disabled={cooldown > 0 || loading}>
                  {cooldown > 0 ? `Resend code in ${cooldown}s` : 'Resend code'}
                </button>
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '13px' }} disabled={loading} id="forgot-verify">
                {loading ? 'Verifying…' : 'Verify'}
              </button>
            </>
          )}

          {step === 'password' && (
            <>
              <div className="form-group">
                <div className="form-input-icon" style={{ position: 'relative' }}>
                  <Lock className="icon" size={15} />
                  <input id="forgot-password" name="password" type={showPass ? 'text' : 'password'} className="form-input"
                    placeholder="New password (min 8 chars)" value={form.password} onChange={handleChange}
                    autoComplete="new-password" autoFocus style={{ paddingRight: 42 }} aria-label="New password" />
                  <button type="button" onClick={() => setShowPass(v => !v)}
                    style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--gray-400)', padding: 4 }}
                    tabIndex={-1} aria-label={showPass ? 'Hide password' : 'Show password'}>
                    {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>
              <div className="form-group">
                <div className="form-input-icon">
                  <Lock className="icon" size={15} />
                  <input id="forgot-confirm" name="confirm" type="password" className="form-input" placeholder="Confirm new password"
                    value={form.confirm} onChange={handleChange} autoComplete="new-password" aria-label="Confirm new password" />
                </div>
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '13px' }} disabled={loading} id="forgot-submit">
                {loading ? 'Saving…' : 'Set new password'}
              </button>
            </>
          )}
        </form>

        <div className="auth-footer">
          Remembered it?{' '}
          <button onClick={() => navigate('/signin')} id="forgot-goto-signin">Back to sign in</button>
        </div>
      </div>
    </div>
  );
}
