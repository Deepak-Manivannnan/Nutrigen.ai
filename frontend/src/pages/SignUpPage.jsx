import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Mail, Lock, Eye, EyeOff, User, CheckCircle2 } from 'lucide-react';
import { useApp } from '../context/AppContext';
import { API_BASE, setAuthToken } from '../utils/api';
import nutrigenLogo from '../assets/nutrigen_logo.png';
import GoogleButton, { googleSignInEnabled } from '../components/GoogleButton';
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
 * Sign up in three steps on one page:
 *   1. name + email -> "Send code"
 *   2. 6-digit code -> "Verify"
 *   3. password + confirm -> "Create account"
 * The account is only created after the email is proven.
 */
export default function SignUpPage() {
  const navigate = useNavigate();
  const { signIn, addToast } = useApp();
  const [step, setStep] = useState('details'); // details | code | password
  const [form, setForm] = useState({ name: '', email: '', code: '', password: '', confirm: '' });
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

  async function finishSignIn(data, message) {
    setAuthToken(data.token);
    await signIn(data);
    addToast(message || (data.has_profile ? `Welcome back, ${data.name}!` : 'Account created! Complete your profile.'), 'success');
    navigate(data.has_profile ? '/app/home' : '/onboarding', { replace: true });
  }

  async function sendCode(e) {
    e?.preventDefault();
    if (!form.name.trim() || !form.email.trim()) {
      setError('Please enter your name and email.');
      return;
    }
    setLoading(true);
    try {
      const data = await postJson('/auth/signup/send-code', { name: form.name.trim(), email: form.email.trim() });
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
    if (form.code.length !== 6) {
      setError('Please enter the code from your email.');
      return;
    }
    setLoading(true);
    try {
      const data = await postJson('/auth/signup/verify-code', { email: form.email.trim(), code: form.code });
      setTicket(data.verification_token);
      setInfo('');
      setStep('password');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function createAccount(e) {
    e.preventDefault();
    if (form.password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (form.password !== form.confirm) {
      setError('Passwords do not match.');
      return;
    }
    setLoading(true);
    try {
      const data = await postJson('/auth/register', {
        name: form.name.trim(), email: form.email.trim(), password: form.password, verification_token: ticket,
      });
      await finishSignIn(data, 'Account created! Complete your profile.');
    } catch (err) {
      setError(err.message);
      // The verification expired: start again from the code step.
      if (/verif/i.test(err.message)) { setTicket(''); setStep('details'); }
    } finally {
      setLoading(false);
    }
  }

  function changeEmail() {
    setStep('details');
    setTicket('');
    setInfo('');
    setError('');
    setForm(p => ({ ...p, code: '', password: '', confirm: '' }));
  }

  const onSubmit = step === 'details' ? sendCode : step === 'code' ? verifyCode : createAccount;
  const lockDetails = step !== 'details';

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
          <div role="alert" style={{ background: 'var(--color-error-bg)', border: '1px solid #fca5a5', borderRadius: 'var(--radius-md)', padding: '10px 14px', fontSize: 'var(--text-sm)', color: 'var(--color-error)', marginBottom: 'var(--space-4)' }}>
            {error}
          </div>
        )}

        <form className="auth-form" onSubmit={onSubmit} noValidate>
          <div className="form-group">
            <div className="form-input-icon">
              <User className="icon" size={15} />
              <input id="signup-name" name="name" type="text" className="form-input" placeholder="Full name" value={form.name} onChange={handleChange} autoComplete="name" disabled={lockDetails} aria-label="Full name" />
            </div>
          </div>
          <div className="form-group">
            <div className="form-input-icon" style={{ position: 'relative' }}>
              <Mail className="icon" size={15} />
              <input id="signup-email" name="email" type="email"
                className={`form-input${step === 'password' ? ' is-verified' : ''}`}
                placeholder="Email address" value={form.email} onChange={handleChange} autoComplete="email"
                disabled={lockDetails} aria-label={step === 'password' ? 'Email address (verified)' : 'Email address'} />
              {step === 'password' && (
                <span className="verified-check" title="Email verified" aria-hidden="true"><CheckCircle2 size={17} /></span>
              )}
            </div>
          </div>

          {step === 'details' && (
            <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '13px' }} disabled={loading} id="signup-send-code">
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
              <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '13px' }} disabled={loading} id="signup-verify">
                {loading ? 'Verifying…' : 'Verify'}
              </button>
            </>
          )}

          {step === 'password' && (
            <>
              <div className="auth-row-end">
                <button type="button" className="auth-link" onClick={changeEmail}>Change email</button>
              </div>
              <div className="form-group">
                <div className="form-input-icon" style={{ position: 'relative' }}>
                  <Lock className="icon" size={15} />
                  <input
                    id="signup-password" name="password" type={showPass ? 'text' : 'password'}
                    className="form-input" placeholder="Create password (min 8 chars)"
                    value={form.password} onChange={handleChange} autoComplete="new-password" autoFocus
                    style={{ paddingRight: 42 }} aria-label="Create password"
                  />
                  <button type="button" onClick={() => setShowPass(v => !v)}
                    style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--gray-400)', padding: 4 }}
                    tabIndex={-1} aria-label={showPass ? 'Hide password' : 'Show password'}
                  >
                    {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>
              <div className="form-group">
                <div className="form-input-icon">
                  <Lock className="icon" size={15} />
                  <input id="signup-confirm" name="confirm" type="password" className="form-input" placeholder="Confirm password" value={form.confirm} onChange={handleChange} autoComplete="new-password" aria-label="Confirm password" />
                </div>
              </div>
              <button type="submit" className="btn btn-primary" style={{ width: '100%', padding: '13px' }} disabled={loading} id="signup-submit">
                {loading ? 'Creating account…' : 'Create account'}
              </button>
            </>
          )}
        </form>

        {googleSignInEnabled && (
          <>
            <div className="auth-divider auth-divider-spaced"><span>or continue with</span></div>
            <GoogleButton onSuccess={data => finishSignIn(data)} onError={setError} disabled={loading} />
          </>
        )}

        <div className="auth-footer">
          Already have an account?{' '}
          <button onClick={() => navigate('/signin')} id="goto-signin">Sign in</button>
        </div>
      </div>
    </div>
  );
}
