import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import api from '../services/api';
import { setTokenGetter, setUnauthenticatedHandler } from '../services/apiClient';

const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

const TOKEN_KEY = 'sahay_token';

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
  const tokenRef = useRef(token);

  // The client reads the token through a getter so it always sees the current
  // one, without every request closing over a stale value. Writing the ref in
  // an effect rather than during render keeps render pure.
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  useEffect(() => { setTokenGetter(() => tokenRef.current); }, []);

  const clearSession = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
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
    if (!token) { setChecking(false); setUser(null); return undefined; }
    setChecking(true);
    api.auth.me()
      .then((u) => { if (!cancelled) setUser(u); })
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

  const isStaff = Boolean(user && ['mentor', 'admin', 'staff'].includes(user.role));

  const value = useMemo(() => ({
    user, token, checking, isStaff, login, logout, changePassword, clearSession,
  }), [user, token, checking, isStaff, login, logout, changePassword, clearSession]);

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}
