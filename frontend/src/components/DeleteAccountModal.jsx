import React, { useEffect, useState } from 'react';
import { Eye, EyeOff, Lock, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../context/AppContext';
import { startAccountDeletion, confirmAccountDeletion } from '../utils/api';
import OtpInput from './OtpInput';

/**
 * Delete account, two steps in one centred window (same look as Edit Profile):
 *   1. What deletion means -> Cancel / Continue
 *   2. Confirm it's you: 6-digit emailed code (verified accounts) or the
 *      account password (older accounts without a verified email)
 *      -> Cancel / Delete my account
 * While the deletion runs the window cannot be closed.
 */
export default function DeleteAccountModal({ onClose }) {
  const navigate = useNavigate();
  const { signOut, addToast } = useApp();
  const [step, setStep] = useState('warn'); // warn | confirm
  const [method, setMethod] = useState(null); // code | password
  const [maskedEmail, setMaskedEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [busy, setBusy] = useState(false); // sending code / deleting
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const [cooldown, setCooldown] = useState(0);

  const locked = deleting; // nothing may close the window mid-deletion

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const t = setTimeout(() => setCooldown(c => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && !locked) onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [locked, onClose]);

  async function begin() {
    setBusy(true);
    setError('');
    try {
      const data = await startAccountDeletion();
      setMethod(data.method);
      setMaskedEmail(data.masked_email || '');
      setCooldown(data.method === 'code' ? (data.resend_after || 60) : 0);
      setCode('');
      setStep('confirm');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true);
    setError('');
    try {
      const data = await startAccountDeletion();
      setCooldown(data.resend_after || 60);
      setCode('');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirm(e) {
    e.preventDefault();
    if (method === 'code' && code.length !== 6) { setError('Please enter the code from your email.'); return; }
    if (method === 'password' && !password) { setError('Please enter your password.'); return; }
    setBusy(true);
    setDeleting(true);
    setError('');
    try {
      await confirmAccountDeletion(method === 'code' ? { code } : { password });
      // Everything is gone on the server; clear this browser too.
      signOut();
      navigate('/', { replace: true });
      addToast('Your account has been deleted.', 'success');
    } catch (err) {
      setError(err.message);
      setDeleting(false);
      setBusy(false);
    }
  }

  const ready = method === 'code' ? code.length === 6 : password.length > 0;

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && !locked && onClose()}>
      <div className="modal delete-account-modal" id="delete-account-modal" role="dialog" aria-modal="true" aria-labelledby="delete-account-title">
        <div className="modal-header">
          <span className="modal-title danger-title" id="delete-account-title">Delete account</span>
          <button className="modal-close" onClick={onClose} disabled={locked} aria-label="Close"><X size={14} /></button>
        </div>

        {step === 'warn' && (
          <>
            <div className="modal-body">
              <p className="delete-lead">
                Deleting your account permanently removes your NutriGen.ai profile, along with all the meal
                plans, grocery lists and chat history you&apos;ve created. This can&apos;t be undone.
              </p>
              {error && <div className="delete-error" role="alert">{error}</div>}
            </div>
            <div className="modal-footer">
              <button type="button" className="btn btn-outline btn-sm" onClick={onClose} disabled={busy}>Cancel</button>
              <button type="button" className="btn btn-danger btn-sm" onClick={begin} disabled={busy} id="delete-account-continue">
                {busy ? 'Please wait…' : 'Continue'}
              </button>
            </div>
          </>
        )}

        {step === 'confirm' && (
          <form onSubmit={confirm}>
            <div className="modal-body">
              {method === 'code' ? (
                <>
                  <p className="delete-lead">
                    To confirm it&apos;s you, enter the code we sent to <strong>{maskedEmail}</strong>.
                  </p>
                  <OtpInput value={code} onChange={v => { setCode(v); setError(''); }} autoFocus disabled={deleting} />
                  <div className="delete-resend">
                    <button type="button" className="auth-link" onClick={resend} disabled={cooldown > 0 || busy}>
                      {cooldown > 0 ? `Resend code in ${cooldown}s` : 'Resend code'}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <p className="delete-lead">To confirm it&apos;s you, enter your password.</p>
                  <div className="form-input-icon" style={{ position: 'relative' }}>
                    <Lock className="icon" size={15} />
                    <input
                      id="delete-account-password" type={showPass ? 'text' : 'password'} className="form-input"
                      placeholder="Password" value={password} autoFocus autoComplete="current-password"
                      onChange={e => { setPassword(e.target.value); setError(''); }} disabled={deleting}
                      style={{ paddingRight: 42 }} aria-label="Password"
                    />
                    <button type="button" onClick={() => setShowPass(v => !v)} tabIndex={-1}
                      style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--gray-400)', padding: 4 }}
                      aria-label={showPass ? 'Hide password' : 'Show password'}>
                      {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                </>
              )}
              {error && <div className="delete-error" role="alert">{error}</div>}
            </div>
            <div className="modal-footer">
              <button type="button" className="btn btn-outline btn-sm" onClick={onClose} disabled={locked}>Cancel</button>
              <button type="submit" className="btn btn-danger btn-sm" disabled={!ready || busy} id="delete-account-confirm">
                {deleting ? 'Deleting…' : 'Delete my account'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
