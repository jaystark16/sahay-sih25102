/**
 * The one HTTP client.
 *
 * Every request in the app goes through `request()`. Nothing else calls fetch.
 *
 * The rules it enforces, and why each one exists in this codebase:
 *
 *  - A failed request throws. It never resolves to empty data. Five call sites
 *    used to do `.then(r => r.json()).then(setData)` with no status check, so
 *    an error body parsed cleanly, the expected key came back undefined, and
 *    the dashboard rendered "0 students flagged for attention this week". A
 *    backend outage was being shown to the mentor as good news.
 *  - The body is read once as text and then parsed, because when the API is
 *    down a proxy answers with an HTML error page and `res.json()` fails with
 *    "Unexpected token '<'", which tells the user nothing.
 *  - Network failure is a different error from a backend failure, and both are
 *    different from a validation failure. The UI needs to say different things.
 *  - The backend's error envelope is {"error": {code, message, hint, fields}}.
 *    That contract is preserved and surfaced, not flattened into a string.
 */

/**
 * Where the API lives.
 *
 * Empty in development: vite.config.js proxies /api to 127.0.0.1:8000, so a
 * relative URL is correct and no CORS is involved. It is also empty in the
 * current production setup, where the Vercel deployment serves the API from
 * /api on the same origin as the frontend. VITE_API_BASE exists so the two can
 * be split onto different hosts without a code change.
 */
const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');

/** Paths are written as "/dashboard"; the /api prefix is added here, once. */
export function apiUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE}/api${p}`;
}

/** Error kinds the UI branches on. */
export const ErrorKind = {
  NETWORK: 'network',        // never reached the server
  UNAUTHENTICATED: 'unauthenticated',
  FORBIDDEN: 'forbidden',
  NOT_FOUND: 'not_found',
  VALIDATION: 'validation',
  RATE_LIMITED: 'rate_limited',
  SERVER: 'server',
  UNREADABLE: 'unreadable',  // 2xx but not JSON
};

export class ApiError extends Error {
  constructor(kind, message, { status, code, hint, fields } = {}) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.code = code;
    this.hint = hint;
    this.fields = fields;
  }

  /** Message plus the backend's hint, when it adds something. */
  get fullMessage() {
    return this.hint ? `${this.message} ${this.hint}` : this.message;
  }
}

function kindForStatus(status) {
  if (status === 401) return ErrorKind.UNAUTHENTICATED;
  if (status === 403) return ErrorKind.FORBIDDEN;
  if (status === 404) return ErrorKind.NOT_FOUND;
  // 400 as well as 422. Every 400 this API raises comes from `except
  // ValueError` around a domain rule -- a duplicate roll number, attendance
  // above the classes held, marks above the maximum -- so it is the caller's
  // input and the caller can fix it. Falling through to SERVER told the user
  // "The server ran into a problem. This is not your input." above a message
  // that said "22CSE0032 already exists", which is exactly their input.
  if (status === 400 || status === 422) return ErrorKind.VALIDATION;
  if (status === 429) return ErrorKind.RATE_LIMITED;
  return ErrorKind.SERVER;
}

/**
 * Fallback messages. Used only when the backend did not supply one -- for a
 * proxy-generated 502, say, where there is no JSON envelope at all.
 */
const FALLBACK = {
  [ErrorKind.UNAUTHENTICATED]: 'Your session has ended. Please sign in again.',
  [ErrorKind.FORBIDDEN]: 'This action is restricted to staff accounts.',
  [ErrorKind.NOT_FOUND]: 'That record could not be found.',
  [ErrorKind.VALIDATION]: 'Some of the values sent were not valid.',
  [ErrorKind.RATE_LIMITED]: 'Too many attempts. Please wait and try again.',
  [ErrorKind.SERVER]: 'The server ran into a problem. This is not your input.',
};

/** Called on any 401 so the session can be cleared exactly once, centrally. */
let onUnauthenticated = null;
export function setUnauthenticatedHandler(fn) {
  onUnauthenticated = fn;
}

/**
 * Where the bearer token lives. Shared with the auth layer so the two cannot
 * drift apart.
 */
export const TOKEN_KEY = 'sahay_token';

/**
 * Read the token from storage on every request.
 *
 * This used to be a getter the auth provider registered, backed by a ref it
 * updated inside an effect -- and that was a real bug. React runs effects
 * child-first, so on the render where the token first becomes non-empty the
 * dashboard's own effects fire their requests *before* the provider's effect
 * updates the ref. The first batch after a fresh sign-in therefore went out
 * with no Authorization header, got 401, and the central handler below
 * cleared the session: sign in, see the dashboard for an instant, get thrown
 * back to the login screen.
 *
 * localStorage is synchronous and login() writes to it before touching state,
 * so reading it here is always current and has no ordering hazard at all.
 */
function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || null;
  } catch {
    // Storage can throw in private-mode / sandboxed contexts.
    return null;
  }
}

export async function request(path, { method = 'GET', body, signal,
                                      isForm = false, auth = true } = {}) {
  const headers = {};
  if (auth) {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json';

  let res;
  try {
    res = await fetch(apiUrl(path), {
      method,
      headers,
      signal,
      body: body === undefined ? undefined : (isForm ? body : JSON.stringify(body)),
    });
  } catch (e) {
    // AbortError is a deliberate cancellation, not a failure to report.
    if (e && e.name === 'AbortError') throw e;
    throw new ApiError(ErrorKind.NETWORK,
      'Cannot reach the server. Check your connection and try again.');
  }

  // 204 and friends carry no body.
  const text = res.status === 204 ? '' : await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { /* not JSON; handled below */ }
  }

  if (!res.ok) {
    const kind = kindForStatus(res.status);
    if (kind === ErrorKind.UNAUTHENTICATED && onUnauthenticated) onUnauthenticated();
    const env = data && data.error;
    throw new ApiError(
      kind,
      (env && env.message)
        // Tolerate an older deployment that still returns FastAPI's default.
        || (typeof data?.detail === 'string' ? data.detail : null)
        || FALLBACK[kind]
        || `Request failed (HTTP ${res.status}).`,
      { status: res.status, code: env?.code, hint: env?.hint, fields: env?.fields },
    );
  }

  if (text && data === null) {
    throw new ApiError(ErrorKind.UNREADABLE,
      'The server sent a response this app could not read.',
      { status: res.status });
  }
  return data;
}

export const get = (path, opts) => request(path, { ...opts, method: 'GET' });
export const post = (path, body, opts) => request(path, { ...opts, method: 'POST', body });
export const put = (path, body, opts) => request(path, { ...opts, method: 'PUT', body });
export const patch = (path, body, opts) => request(path, { ...opts, method: 'PATCH', body });
export const del = (path, opts) => request(path, { ...opts, method: 'DELETE' });

/** Build a query string, dropping empty values so we never send `?q=`. */
export function query(params) {
  const usable = Object.entries(params || {})
    .filter(([, v]) => v !== undefined && v !== null && v !== '');
  if (!usable.length) return '';
  return `?${new URLSearchParams(usable).toString()}`;
}
