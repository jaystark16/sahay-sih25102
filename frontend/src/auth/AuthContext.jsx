import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import api from '../services/api';
import { TOKEN_KEY, setUnauthenticatedHandler } from '../services/apiClient';

const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

/**
 * Session handling.
 *
 * The token lives in localStorage. That is readable by any XSS, and an
 * httpOnly cookie would be stronger -- but the API is on a separate origin in
 * some deployments, which turns cookies into a SameSite=None + CSRF exercise.
 * It is a deliberate trade, recorded here rather than left implicit.
 *
 * A dead token is cleared in exactly one place: the API client calls the
 * handler registered below on any 401, so every route benefits without each
 * page remembering to check.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '');
  const [checking, setChecking] = useState(Boolean(localStorage.getItem(TOKEN_KEY)));
  // Which token we have already established a user for. Without this, a fresh
  // login was immediately followed by a redundant /auth/me and, worse, by
  // setChecking(true) -- which makes the router swap the whole app out for the
  // loading screen, unmounting every page and remounting it. Measured cost:
  // the worklist's three requests fired four times each on sign-in.
  const validatedToken = useRef(null);

  // No token plumbing here on purpose: apiClient reads it straight from
  // storage on every request. Handing it a getter backed by a ref meant the
  // first requests after a fresh sign-in raced the effect that populated it.

  const clearSession = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    validatedToken.current = null;
    setToken('');
    setUser(null);
  }, []);

  useEffect(() => {
    setUnauthenticatedHandler(() => {
      localStorage.removeItem(TOKEN_KEY);
      setToken('');
      setUser(null);
    });
  }, []);

  // Resume an existing session on load.
  useEffect(() => {
    let cancelled = false;
    if (!token) {
      validatedToken.current = null;
      setChecking(false);
      setUser(null);
      return undefined;
    }
    // login() already returned the user for this token; nothing to verify.
    if (validatedToken.current === token) { setChecking(false); return undefined; }

    setChecking(true);
    api.auth.me()
      .then((u) => {
        if (cancelled) return;
        validatedToken.current = token;
        setUser(u);
      })
      .catch(() => {
        // 401 already cleared the token via the central handler. Any other
        // failure (server down) also leaves us signed out rather than
        // pretending to be signed in with unknown permissions.
        if (!cancelled) { localStorage.removeItem(TOKEN_KEY); setUser(null); }
      })
      .finally(() => { if (!cancelled) setChecking(false); });
    return () => { cancelled = true; };
  }, [token]);

  const login = useCallback(async (email, password) => {
    const d = await api.auth.login(email, password);
    localStorage.setItem(TOKEN_KEY, d.token);
    // Mark it validated before the state updates, so the effect above does not
    // re-verify a token we were just handed the user for.
    validatedToken.current = d.token;
    setToken(d.token);
    setUser(d.user);
    return d.user;
  }, []);

  const logout = useCallback(async () => {
    try { await api.auth.logout(); } catch { /* signing out locally regardless */ }
    clearSession();
  }, [clearSession]);

  /**
   * The API revokes every session for the user on a password change, so the
   * current token is dead the moment this succeeds. Clearing the session sends
   * the user back to sign in with the new password, which is the honest
   * behaviour rather than silently keeping a token the server has dropped.
   */
  const changePassword = useCallback(async (oldPassword, newPassword) => {
    await api.auth.changePassword(oldPassword, newPassword);
    clearSession();
  }, [clearSession]);

  /**
   * Two different questions, kept apart on purpose.
   *
   * isStaff  -- "does this person work here": mentors included. Gates the
   *             staff surfaces (uploads, config, interventions).
   * isInstitution -- "may this person see the whole institution": mentors
   *             excluded. Gates the HOD views and destructive deletion.
   *
   * Mirrors the backend exactly (service.INSTITUTION_ROLES and require_staff),
   * because a UI that offers a control the API will refuse is worse than not
   * offering it. Neither of these is the security boundary -- that is enforced
   * server-side -- they only decide what is worth rendering.
   */
  const isStaff = Boolean(user
    && ['mentor', 'hod', 'principal', 'admin', 'staff'].includes(user.role));
  const isInstitution = Boolean(user
    && ['hod', 'principal', 'admin'].includes(user.role));

  const value = useMemo(() => ({
    user, token, checking, isStaff, isInstitution,
    login, logout, changePassword, clearSession,
  }), [user, token, checking, isStaff, isInstitution,
       login, logout, changePassword, clearSession]);

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}
