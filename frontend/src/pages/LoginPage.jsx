import { useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/ui/Primitives';
import { ErrorKind } from '../services/apiClient';

/**
 * Sign in.
 *
 * Deliberately carries no demo credentials. An earlier version printed a
 * working password on this screen as a click-to-fill hint, which published a
 * credential to anyone who opened the deployed site. Accounts are seeded with
 * a random password that must be changed on first use, so there is no shared
 * password to advertise even if it were wise to.
 */
export function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  // Each failure mode needs a different response from the user, so they get
  // different words: a wrong password is not a dead network is not a lockout.
  const message = (() => {
    if (!error) return null;
    if (error.kind === ErrorKind.RATE_LIMITED) return error.message;
    if (error.kind === ErrorKind.NETWORK) {
      return 'Cannot reach the server. Check your connection and try again.';
    }
    if (error.kind === ErrorKind.SERVER) {
      return `${error.message}${error.hint ? ` ${error.hint}` : ''}`;
    }
    return error.message || 'Sign-in failed.';
  })();

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="auth-brand__mark" aria-hidden="true">S</span>
          <div>
            <h1 className="auth-brand__name">Sahay</h1>
            <p className="auth-brand__tag">Student early warning &amp; support</p>
          </div>
        </div>

        {message && (
          <div className="auth-error" role="alert" aria-live="assertive">
            {message}
          </div>
        )}

        <form onSubmit={submit} className="auth-form">
          <label className="field">
            <span className="field__label" htmlFor="login-email">Email</span>
            <input
              id="login-email"
              className="field__input"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
          </label>

          <label className="field">
            <span className="field__label" htmlFor="login-password">Password</span>
            <input
              id="login-password"
              className="field__input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>

          <Button type="submit" variant="primary" size="lg" busy={busy}
            className="auth-submit">
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>

        <p className="auth-foot">
          Accounts are issued by your institution&rsquo;s administrator.
        </p>
      </div>
    </main>
  );
}

/**
 * Forced password change.
 *
 * Seeded accounts carry must_change_password, and the API refuses every other
 * route until it is cleared. Without this screen a user signed in successfully
 * and then met "Set a new password before using the app" on every page with no
 * way to comply -- locked out by the check meant to protect them.
 */
export function ChangePasswordPage() {
  const { user, changePassword, logout } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (next !== confirm) {
      setError({ message: 'The two new passwords do not match.' });
      return;
    }
    if (next.length < 10) {
      setError({ message: 'New password must be at least 10 characters.' });
      return;
    }
    setBusy(true);
    try {
      // Succeeding here revokes every session for this user, so the app
      // returns to the sign-in screen by design.
      await changePassword(current, next);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="auth-brand__mark" aria-hidden="true">S</span>
          <div>
            <h1 className="auth-brand__name">Set a new password</h1>
            <p className="auth-brand__tag">
              This account still uses a temporary password.
            </p>
          </div>
        </div>

        {error && (
          <div className="auth-error" role="alert" aria-live="assertive">
            {error.fullMessage || error.message}
          </div>
        )}

        <form onSubmit={submit} className="auth-form">
          <label className="field">
            <span className="field__label" htmlFor="cp-current">Current password</span>
            <input id="cp-current" className="field__input" type="password"
              autoComplete="current-password" value={current} required autoFocus
              onChange={(e) => setCurrent(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label" htmlFor="cp-next">New password</span>
            <input id="cp-next" className="field__input" type="password"
              autoComplete="new-password" value={next} required minLength={10}
              onChange={(e) => setNext(e.target.value)} />
            <span className="field__help">At least 10 characters.</span>
          </label>
          <label className="field">
            <span className="field__label" htmlFor="cp-confirm">Confirm new password</span>
            <input id="cp-confirm" className="field__input" type="password"
              autoComplete="new-password" value={confirm} required
              onChange={(e) => setConfirm(e.target.value)} />
          </label>
          <Button type="submit" variant="primary" size="lg" busy={busy}
            className="auth-submit">
            {busy ? 'Saving…' : 'Set password'}
          </Button>
        </form>

        <p className="auth-foot">
          Signed in as {user?.email}. <button type="button" className="link-btn"
            onClick={logout}>Sign out instead</button>
        </p>
      </div>
    </main>
  );
}
