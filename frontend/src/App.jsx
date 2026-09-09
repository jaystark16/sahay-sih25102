import { useState, useEffect, useCallback, createContext, useContext } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import './index.css';

// ─── API helpers ──────────────────────────────────────────────────────────────
// Where the backend lives.
//
// Empty in development: vite.config.js proxies /api to 127.0.0.1:8000, so a
// relative URL is correct and there is no CORS involved. In production the
// frontend and the API are deployed separately (the API needs scikit-learn and
// xgboost, which do not fit in a serverless bundle), so set VITE_API_BASE to
// the API's origin at build time -- e.g. https://sahay-api.onrender.com --
// and add that frontend origin to CORS_ORIGINS on the API side.
const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');
const apiUrl = path => `${API_BASE}${path}`;

// One place that turns any response into either parsed data or a thrown Error
// with a message worth showing.
//
// This logic used to exist only inside login(), and every other call site did
// some weaker version of it or none at all -- five of them never checked
// res.ok, so an error body parsed cleanly, the expected key came back
// undefined, and the dashboard cheerfully rendered "0 students flagged for
// attention this week". A backend failure was being presented as good news.
async function readResponse(res) {
  // Read the body once: when the API is down the dev proxy answers with an
  // HTML error page and res.json() fails with "Unexpected token '<'", which
  // tells the user nothing.
  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { /* not JSON */ }
  }

  if (!res.ok) {
    // The API returns {"error": {code, message, hint, fields}}. `detail` is
    // accepted too so an older deployment still produces a real message.
    const err = data?.error;
    const msg = err?.message
      || (typeof data?.detail === 'string' ? data.detail : null)
      || `Request failed (HTTP ${res.status}).`;
    const e = new Error(err?.hint ? `${msg} ${err.hint}` : msg);
    e.status = res.status;
    e.code = err?.code;
    e.fields = err?.fields;
    throw e;
  }

  if (data === null) {
    throw new Error('The server sent an unreadable response. '
      + 'Check that the backend is running.');
  }
  return data;
}

// ─── Auth Context ─────────────────────────────────────────────────────────────
const AuthCtx = createContext(null);
const useAuth = () => useContext(AuthCtx);

function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => localStorage.getItem('sahay_token') || '');
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (!token) { setChecking(false); return; }
    fetch(apiUrl('/api/auth/me'), { headers: { Authorization: `Bearer ${token}` } })
      .then(r => (r.ok ? r.json() : null))
      .then(u => {
        // A dead or expired token must be cleared, or the app retries with it
        // on every render and never offers the sign-in screen.
        if (!u) localStorage.removeItem('sahay_token');
        setUser(u);
        setChecking(false);
      })
      .catch(() => setChecking(false));
  }, [token]);

  const login = useCallback(async (userId, password) => {
    let r;
    try {
      r = await fetch(apiUrl('/api/auth/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, password }),
      });
    } catch {
      throw new Error('Cannot reach the server. Is the backend running on port 8000?');
    }
    // When the API is down the dev proxy answers with an HTML error page, and
    // r.json() then fails with "Unexpected token '<'" -- which tells the user
    // nothing. Read the body once and decide what it actually is.
    const d = await readResponse(r);
    localStorage.setItem('sahay_token', d.token);
    setToken(d.token);
    setUser(d.user);
  }, []);

  const logout = useCallback(async () => {
    await fetch(apiUrl('/api/auth/logout'), {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    }).catch(() => {});
    localStorage.removeItem('sahay_token');
    setToken('');
    setUser(null);
  }, [token]);

  // Returns parsed JSON, or throws an Error carrying the server's message.
  // Callers no longer need to remember to check res.ok.
  const apiFetch = useCallback(async (url, opts = {}) => {
    let res;
    try {
      res = await fetch(apiUrl(url), {
        ...opts,
        headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` },
      });
    } catch {
      throw new Error('Cannot reach the server. Is the backend running?');
    }
    if (res.status === 401) {
      // The session is gone. Clear it so the app shows the sign-in screen
      // instead of looping on failed requests with a dead token.
      localStorage.removeItem('sahay_token');
      setToken('');
      setUser(null);
    }
    return readResponse(res);
  }, [token]);

  return (
    <AuthCtx.Provider value={{ user, token, checking, login, logout, apiFetch }}>
      {children}
    </AuthCtx.Provider>
  );
}

// ─── Root ─────────────────────────────────────────────────────────────────────
export default function App() {
  return (
    <AuthProvider>
      <AppRouter />
    </AuthProvider>
  );
}

function AppRouter() {
  const { user, checking } = useAuth();
  if (checking) return <LoadingScreen />;
  if (!user) return <LoginPage />;
  return <AppShell />;
}

function LoadingScreen() {
  return (
    <div className="loading-screen">
      <div className="spinner" />
      <span>Loading…</span>
    </div>
  );
}

// ─── Login ────────────────────────────────────────────────────────────────────
// The demo account's address only. The password is deliberately not here.
//
// This used to carry `password: 'Sahay@Mentor2025'` and print it on the sign-in
// page, which published a working credential to anyone who loaded the site. It
// is also no longer true: seeded accounts now get a random password that must
// be changed on first use (see auth.seed_users), so the printed value could
// only ever mislead. Set one with `python auth.py reset <email> <password>`.
const MENTOR_DEMO = { email: 'mentor@gmail.com' };

function LoginPage() {
  const { login } = useAuth();
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async e => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try { await login(userId.trim(), password); }
    catch (err) { setError(err.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="login-bg">
      <div className="login-card">
        <div className="login-logo">S</div>
        <h1 className="login-title">Sahay</h1>
        <p className="login-subtitle">Student Early Warning System</p>

        <form className="login-form" onSubmit={handleSubmit} autoComplete="on">
          {error && <div className="login-error">{error}</div>}

          <div className="input-group">
            <label className="input-label" htmlFor="user-id">Mentor email</label>
            <input id="user-id" className="input" type="email"
              placeholder="mentor@gmail.com"
              value={userId} onChange={e => setUserId(e.target.value)}
              autoComplete="email" required />
          </div>

          <div className="input-group">
            <label className="input-label" htmlFor="password">Password</label>
            <input id="password" className="input" type="password"
              placeholder="Your password"
              value={password} onChange={e => setPassword(e.target.value)}
              autoComplete="current-password" required />
          </div>

          <button type="submit" className="btn btn-primary btn-lg"
            disabled={loading} style={{ marginTop: '0.25rem' }}>
            {loading
              ? <><span className="spinner" style={{ width: 16, height: 16 }} /> Signing in…</>
              : 'Sign In'}
          </button>
        </form>

        <p className="login-hint">
          <span style={{ display: 'block' }}>Mentor sign-in</span>
          <span style={{ display: 'block', marginTop: '0.3rem', cursor: 'pointer' }}
            title="Click to fill the email"
            onClick={() => setUserId(MENTOR_DEMO.email)}>
            <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: 'var(--accent)' }}>
              {MENTOR_DEMO.email}
            </code>
          </span>
        </p>
      </div>
    </div>
  );
}

// ─── Toast ────────────────────────────────────────────────────────────────────
// Replaces window.alert(), which blocks the page, cannot be styled, and shows
// the "localhost:3000 says" chrome that makes a demo look unfinished.
function Toast({ toast, onDismiss }) {
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(onDismiss, 5000);
    return () => clearTimeout(t);
  }, [toast, onDismiss]);

  if (!toast) return null;
  const good = toast.type !== 'error';
  return (
    <div className={`toast ${good ? 'toast-success' : 'toast-error'}`}
      role="status" aria-live="polite">
      <span className="toast-icon" aria-hidden="true">{good ? '✓' : '!'}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="toast-title">{toast.title}</div>
        {toast.detail && <div className="toast-detail">{toast.detail}</div>}
      </div>
      <button className="toast-close" onClick={onDismiss} aria-label="Dismiss">×</button>
    </div>
  );
}

// ─── Add Student Modal ────────────────────────────────────────────────────────
function AddStudentModal({ onClose, onSuccess }) {
  const { apiFetch } = useAuth();
  // gender and cgpa are columns the roster shows, so collect them here or every
  // student added through this form has two permanently blank cells.
  const [formData, setFormData] = useState({
    name: '', dept: '', year: 1, section: 'A', gender: '', cgpa: '',
  });
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const handleSubmit = async e => {
    e.preventDefault();
    setErr('');
    setLoading(true);
    try {
      const r = await apiFetch('/api/students', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...formData,
          gender: formData.gender || null,
          cgpa: formData.cgpa === '' ? null : Number(formData.cgpa),
        }),
      });
      onSuccess(r);
    } catch (error) {
      setErr(error.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, backdropFilter: 'blur(2px)' }}>
      <div className="surface" style={{ width: 420, padding: '2rem', borderRadius: 'var(--r-md)', boxShadow: '0 8px 32px rgba(0,0,0,0.4)' }}>
        <h3 style={{ marginBottom: '1.5rem', fontSize: '1.25rem' }}>Add New Student</h3>
        {err && <div className="alert alert-danger" style={{ marginBottom: '1rem' }}>{err}</div>}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div>
            <label className="input-label">Name <span style={{color:'var(--red)'}}>*</span></label>
            <input className="input" required value={formData.name} onChange={e => setFormData({...formData, name: e.target.value})} placeholder="e.g. Jane Doe" />
          </div>
          <div>
            <label className="input-label">Department <span style={{color:'var(--red)'}}>*</span></label>
            <input className="input" required value={formData.dept} onChange={e => setFormData({...formData, dept: e.target.value})} placeholder="e.g. CSE" />
          </div>
          <div style={{ display: 'flex', gap: '1rem' }}>
            <div style={{ flex: 1 }}>
              <label className="input-label">Year (1-4) <span style={{color:'var(--red)'}}>*</span></label>
              <input className="input" type="number" min="1" max="4" required value={formData.year} onChange={e => setFormData({...formData, year: parseInt(e.target.value)})} />
            </div>
            <div style={{ flex: 1 }}>
              <label className="input-label">Section</label>
              <input className="input" required value={formData.section} onChange={e => setFormData({...formData, section: e.target.value})} placeholder="e.g. A" />
            </div>
          </div>
          <div style={{ display: 'flex', gap: '1rem' }}>
            <div style={{ flex: 1 }}>
              <label className="input-label" htmlFor="add-gender">Gender</label>
              <select id="add-gender" className="input" value={formData.gender}
                onChange={e => setFormData({ ...formData, gender: e.target.value })}>
                <option value="">Not stated</option>
                <option value="F">Female</option>
                <option value="M">Male</option>
                <option value="O">Other</option>
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label className="input-label" htmlFor="add-cgpa">CGPA</label>
              <input id="add-cgpa" className="input" type="number" step="0.01" min="0" max="10"
                placeholder="if known"
                value={formData.cgpa}
                onChange={e => setFormData({ ...formData, cgpa: e.target.value })} />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '1rem', marginTop: '1rem' }}>
            <button type="button" className="btn btn-outline" onClick={onClose} style={{ flex: 1 }}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={loading} style={{ flex: 1 }}>
              {loading ? 'Saving...' : 'Save Student'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Remove Student Modal ─────────────────────────────────────────────────────
// Deleting a student also deletes their attendance history and interventions,
// which cannot be undone -- so the mentor picks a student, then confirms
// against the name and roll number, and the button says what it does.
function RemoveStudentModal({ onClose, onRemoved }) {
  const { apiFetch } = useAuth();
  const [students, setStudents] = useState(null);
  const [rollNo, setRollNo] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiFetch('/api/analytics/roster')
      .then(d => setStudents(d.students || []))
      .catch(e => setErr(e.message));
  }, [apiFetch]);

  const selected = (students || []).find(s => s.roll_no === rollNo);

  const remove = async () => {
    setBusy(true); setErr('');
    try {
      onRemoved(await apiFetch(`/api/students/${rollNo}`, { method: 'DELETE' }));
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, backdropFilter: 'blur(2px)' }}>
      <div className="surface" style={{ width: 440, padding: '2rem', borderRadius: 'var(--r-md)', boxShadow: '0 8px 32px rgba(0,0,0,0.4)' }}>
        <h3 style={{ marginBottom: '1.25rem', fontSize: '1.25rem' }}>Remove Student</h3>
        {err && <div className="alert alert-danger" style={{ marginBottom: '1rem' }}>{err}</div>}

        {!students ? (
          <p style={{ color: 'var(--label-tertiary)', fontSize: '0.875rem' }}>Loading students…</p>
        ) : !confirming ? (
          <>
            <label className="input-label" htmlFor="rm-student">Student</label>
            <select id="rm-student" className="input" value={rollNo}
              onChange={e => setRollNo(e.target.value)} style={{ width: '100%' }}>
              <option value="">Select a student…</option>
              {students.map(s => (
                <option key={s.roll_no} value={s.roll_no}>{s.name} · {s.roll_no}</option>
              ))}
            </select>
            <div style={{ display: 'flex', gap: '1rem', marginTop: '1.75rem' }}>
              <button type="button" className="btn btn-outline" onClick={onClose} style={{ flex: 1 }}>Cancel</button>
              <button type="button" className="btn btn-danger" disabled={!rollNo}
                onClick={() => setConfirming(true)} style={{ flex: 1 }}>Continue</button>
            </div>
          </>
        ) : (
          <>
            <p style={{ fontSize: '0.9rem', lineHeight: 1.55 }}>
              Permanently remove <strong>{selected?.name}</strong>{' '}
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--label-secondary)' }}>
                ({selected?.roll_no})
              </span>?
            </p>
            <p style={{ fontSize: '0.82rem', color: 'var(--label-secondary)', marginTop: '0.6rem', lineHeight: 1.5 }}>
              Their attendance history, interventions and risk scores are deleted
              with them. This cannot be undone.
            </p>
            <div style={{ display: 'flex', gap: '1rem', marginTop: '1.75rem' }}>
              <button type="button" className="btn btn-outline" disabled={busy}
                onClick={() => setConfirming(false)} style={{ flex: 1 }}>Back</button>
              <button type="button" className="btn btn-danger" disabled={busy}
                onClick={remove} style={{ flex: 1 }}>
                {busy ? 'Removing…' : 'Remove permanently'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ─── App Shell ────────────────────────────────────────────────────────────────
function AppShell() {
  const { user, logout } = useAuth();
  const [view, setView] = useState('worklist');
  const [selectedRollNo, setSelectedRollNo] = useState(null);
  const [showAddStudent, setShowAddStudent] = useState(false);
  const [showRemoveStudent, setShowRemoveStudent] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [toast, setToast] = useState(null);
  const dismissToast = useCallback(() => setToast(null), []);
  const [studentRiskFilter, setStudentRiskFilter] = useState('all');

  const initials = user.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();

  const goToStudent = useCallback(rollNo => {
    setSelectedRollNo(rollNo);
    setView('detail');
  }, []);

  const goBack = useCallback(() => {
    setSelectedRollNo(null);
    setView('worklist');
  }, []);

  const goToStudents = useCallback((filter = 'all') => {
    setSelectedRollNo(null);
    setStudentRiskFilter(filter);
    setView('students');
  }, []);

  const navItems = [
    { key: 'worklist',  label: 'Worklist',   icon: <IconClipboard /> },
    { key: 'students',  label: 'Students',   icon: <IconUsers /> },
    { key: 'analytics', label: 'Analytics',  icon: <IconChart /> },
    { key: 'model',     label: 'Model Info', icon: <IconBolt /> },
  ];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <div className="sidebar-logo-mark">S</div>
          <div className="sidebar-app-name">Sahay</div>
          <div className="sidebar-subtitle">Early Warning System</div>
        </div>

        <span className="sidebar-section-label">Main</span>
        <nav className="sidebar-nav">
          {navItems.map(({ key, label, icon }) => (
            <button key={key}
              className={`nav-item ${view === key ? 'active' : ''}`}
              onClick={() => { setView(key); setSelectedRollNo(null); }}>
              {icon}{label}
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          {/* User pill moved to header */}
        </div>
      </aside>

      <main className="main-content">
        <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '1.25rem', padding: '1rem 2rem', borderBottom: '1px solid var(--separator)', background: 'rgba(18,18,18,0.85)', backdropFilter: 'blur(12px)', position: 'sticky', top: 0, zIndex: 100 }}>
          <button className="btn btn-danger" onClick={() => setShowRemoveStudent(true)}>− Remove Student</button>
          <button className="btn btn-primary" onClick={() => setShowAddStudent(true)}>+ Add Student</button>
          <div className="user-pill" onClick={logout} title="Click to sign out" style={{ padding: '0.375rem 0.75rem', margin: 0, background: 'var(--fill-secondary)' }}>
            <div className="user-avatar" style={{ width: 28, height: 28, fontSize: '0.65rem' }}>{initials}</div>
            <div>
              <div className="user-name">{user.name}</div>
              <div className="user-role">{user.email || user.role} · Sign out</div>
            </div>
          </div>
        </header>

        {view === 'worklist' && !selectedRollNo && <WorklistPage key={`worklist-${refreshKey}`} onSelect={goToStudent} onNavigateToRoster={goToStudents} />}
        {view === 'detail'   && selectedRollNo   && <StudentDetailPage rollNo={selectedRollNo} onBack={goBack} />}
        {view === 'students' && <StudentsPage key={`students-${studentRiskFilter}-${refreshKey}`} defaultRisk={studentRiskFilter} onSelect={goToStudent} />}
        {view === 'analytics' && <AnalyticsPage key={`analytics-${refreshKey}`} />}
        {view === 'model'    && <ModelPage />}
      </main>

      <Toast toast={toast} onDismiss={dismissToast} />

      {showRemoveStudent && (
        <RemoveStudentModal
          onClose={() => setShowRemoveStudent(false)}
          onRemoved={(res) => {
            setShowRemoveStudent(false);
            setToast({
              type: 'success',
              title: `${res.name} (${res.roll_no}) removed`,
              detail: `Also deleted ${res.removed.attendance} attendance weeks and `
                + `${res.removed.interventions} intervention${res.removed.interventions === 1 ? '' : 's'}.`,
            });
            setRefreshKey(k => k + 1);
          }}
        />
      )}

      {showAddStudent && (
        <AddStudentModal
          onClose={() => setShowAddStudent(false)}
          onSuccess={(res) => {
            setShowAddStudent(false);
            setToast({
              type: 'success',
              title: `${res.name || 'Student'} saved as ${res.roll_no}`,
              detail: res.message || 'Risk scoring begins once four weeks of data exist.',
            });
            // Bumping the key remounts every list, so the new student is on
            // screen immediately rather than after a manual reload.
            setRefreshKey(k => k + 1);
          }}
        />
      )}
    </div>
  );
}

// ─── Worklist Page ────────────────────────────────────────────────────────────
function WorklistPage({ onSelect, onNavigateToRoster }) {
  const { apiFetch, user } = useAuth();
  const [data, setData] = useState(null);
  const [dash, setDash] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    const mentor = user.role === 'mentor' ? user.id : undefined;
    const url = mentor ? `/api/worklist?mentor=${mentor}` : '/api/worklist';
    const dashUrl = mentor ? `/api/dashboard?mentor=${mentor}` : '/api/dashboard';
    
    apiFetch(url).then(setData).catch(e => setErr(e.message));
    apiFetch(dashUrl).then(setDash).catch(e => setErr(e.message));
  }, [apiFetch, user]);

  if (err) return <ErrorBox msg={err} />;
  if (!data) return <LoadingScreen />;

  const greeting = (() => {
    const h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
  })();

  const mentorName = data.mentor?.split(' ').pop() || user.name;

  return (
    <div className="page fade-in">
      <div className="page-header">
        <h2>{greeting}, {mentorName}</h2>
        <p style={{ marginTop: '0.25rem' }}>
          {data.this_week?.length ?? 0} student{(data.this_week?.length ?? 0) === 1 ? '' : 's'} flagged for attention this week
          {data.students_in_scope != null && (
            <span style={{ color: 'var(--label-tertiary)' }}>
              {' · '}{`your caseload, ${data.students_in_scope.toLocaleString()} students`}
            </span>
          )}
        </p>
      </div>

      {dash && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1.5rem', marginBottom: '2.5rem' }}>
          <div className="stat-card fade-in delay-1" style={{ cursor: 'pointer', position: 'relative', overflow: 'hidden' }} onClick={() => onNavigateToRoster('at_risk')}>
            <div style={{ position: 'absolute', top: '-50%', left: '-50%', width: '200%', height: '200%', background: 'radial-gradient(circle at top right, rgba(10, 132, 255, 0.15), transparent 60%)', zIndex: 0, pointerEvents: 'none' }} />
            <div style={{ position: 'relative', zIndex: 1 }}>
              <div className="stat-label">Students at risk</div>
              <div className="stat-value" style={{ color: 'var(--accent)', marginTop: '0.5rem' }}>{dash.at_risk !== undefined ? dash.at_risk : '—'}</div>
            </div>
          </div>
          <div className="stat-card fade-in delay-2" style={{ cursor: 'pointer', position: 'relative', overflow: 'hidden' }} onClick={() => onNavigateToRoster('all')}>
            <div style={{ position: 'absolute', top: '-50%', left: '-50%', width: '200%', height: '200%', background: 'radial-gradient(circle at top right, rgba(255, 255, 255, 0.05), transparent 60%)', zIndex: 0, pointerEvents: 'none' }} />
            <div style={{ position: 'relative', zIndex: 1 }}>
              <div className="stat-label">Total students</div>
              <div className="stat-value" style={{ marginTop: '0.5rem' }}>{dash.total_students !== undefined ? dash.total_students : '—'}</div>
            </div>
          </div>
          <div className="stat-card fade-in delay-3" style={{ position: 'relative', overflow: 'hidden' }}>
            <div style={{ position: 'absolute', top: '-50%', left: '-50%', width: '200%', height: '200%', background: 'radial-gradient(circle at top right, rgba(255, 69, 58, 0.15), transparent 60%)', zIndex: 0, pointerEvents: 'none' }} />
            <div style={{ position: 'relative', zIndex: 1 }}>
              <div className="stat-label">Risk rising</div>
              <div className="stat-value" style={{ color: 'var(--red)', marginTop: '0.5rem' }}>{dash.rising !== undefined ? dash.rising : '—'}</div>
            </div>
          </div>
          <div className="stat-card fade-in delay-4" style={{ position: 'relative', overflow: 'hidden' }}>
            <div style={{ position: 'absolute', top: '-50%', left: '-50%', width: '200%', height: '200%', background: 'radial-gradient(circle at top right, rgba(50, 215, 75, 0.15), transparent 60%)', zIndex: 0, pointerEvents: 'none' }} />
            <div style={{ position: 'relative', zIndex: 1 }}>
              <div className="stat-label" title="Mean CGPA across students in scope">Average CGPA</div>
              <div className="stat-value" style={{ color: 'var(--green)', marginTop: '0.5rem' }}>{dash.average_cgpa !== undefined ? dash.average_cgpa : '—'}</div>
            </div>
          </div>
        </div>
      )}

      {data.cohort_alerts?.length > 0 && (
        <div style={{ marginBottom: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
          {data.cohort_alerts.map((a, i) => (
            <div key={i} className="alert alert-danger fade-in" style={{ animationDelay: `${i * 0.05}s` }}>
              <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" style={{ flexShrink: 0, marginTop: 1 }}>
                <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd"/>
              </svg>
              <div><strong>{a.cohort}</strong> — {a.message}</div>
            </div>
          ))}
        </div>
      )}

      <div className="surface" style={{ overflow: 'hidden' }}>
        <div className="surface-header">
          <h3>Priority List</h3>
          <span style={{ fontSize: '0.8rem', color: 'var(--label-tertiary)' }}>Sorted by urgency</span>
        </div>
        <table className="table">
          <thead>
            <tr><th>Student</th><th>Risk</th><th>Δ Week</th><th>Driver</th><th>Stage</th></tr>
          </thead>
          <tbody>
            {data.this_week?.map((s, i) => (
              <tr key={s.roll_no} onClick={() => onSelect(s.roll_no)}
                className="fade-in" style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}>
                <td>
                  <div style={{ fontWeight: 500 }}>{s.name}</div>
                  <div style={{ fontSize: '0.78rem', color: 'var(--label-tertiary)' }}>{s.roll_no} · {s.section}</div>
                </td>
                <td>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                    <RiskBadge score={s.score} band={s.band} />
                    <div className="risk-bar-track" style={{ width: 80 }}>
                      <div className={`risk-bar-fill risk-bar-${(s.band || '').toLowerCase()}`}
                        style={{ width: `${Math.min(s.score, 100)}%` }} />
                    </div>
                  </div>
                </td>
                <td><DeltaChip delta={s.delta} /></td>
                <td style={{ fontSize: '0.8rem', color: 'var(--label-secondary)', maxWidth: 320 }}>
                  <span className="driver-text" title={s.headline}>{s.headline}</span>
                </td>
                <td><StageChip stage={s.stage} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Students Page ────────────────────────────────────────────────────────────
function StudentsPage({ onSelect, defaultRisk = 'all' }) {
  const { apiFetch, user } = useAuth();
  const [data, setData] = useState(null);
  const [q, setQ] = useState('');
  const [risk, setRisk] = useState(defaultRisk);
  const [page, setPage] = useState(1);

  const load = useCallback(() => {
    const mentor = user.role === 'mentor' ? user.id : undefined;
    const params = new URLSearchParams({ q, risk, page, page_size: 50 });
    if (mentor) params.set('mentor', mentor);
    apiFetch(`/api/students?${params}`).then(setData).catch(e => setErr(e.message));
  }, [apiFetch, user, q, risk, page]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="page fade-in">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Students</h2>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className={`btn ${risk === 'all' ? 'btn-primary' : 'btn-outline'}`} onClick={() => { setRisk('all'); setPage(1); }}>All</button>
          <button className={`btn ${risk === 'at_risk' ? 'btn-primary' : 'btn-outline'}`} onClick={() => { setRisk('at_risk'); setPage(1); }}>At Risk</button>
        </div>
      </div>
      <div style={{ marginBottom: '1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <input className="input" style={{ maxWidth: 320 }}
          placeholder="Search by name or roll number…"
          value={q} onChange={e => { setQ(e.target.value); setPage(1); }} />
        {/* Only worth showing when there is somewhere to page to. The backend
            still paginates; this is just the control disappearing when the whole
            roster already fits on one page. */}
        {data && data.pages > 1 && (
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
            <button className="btn btn-outline" disabled={page <= 1}
              onClick={() => setPage(p => p - 1)}>Prev</button>
            <span style={{ fontSize: '0.9rem', color: 'var(--label-secondary)' }}>
              Page {page} of {data.pages}
            </span>
            <button className="btn btn-outline" disabled={page >= data.pages}
              onClick={() => setPage(p => p + 1)}>Next</button>
          </div>
        )}
      </div>
      {!data ? <LoadingScreen /> : (
        <div className="surface" style={{ overflow: 'hidden' }}>
          <div className="surface-header">
            <h3>{risk === 'at_risk' ? 'At-Risk Students' : 'All Students'}</h3>
            <span style={{ fontSize: '0.8rem', color: 'var(--label-tertiary)' }}>{data.total} total</span>
          </div>
          <table className="table">
            <thead><tr><th>Name</th><th>Roll</th><th>Dept</th><th>Year</th><th>Section</th><th>Risk</th></tr></thead>
            <tbody>
              {data.students?.map((s, i) => (
                <tr key={s.roll_no} onClick={() => onSelect(s.roll_no)}
                  className="fade-in" style={{ animationDelay: `${Math.min(i, 12) * 0.02}s` }}>
                  <td style={{ fontWeight: 500 }}>{s.name}</td>
                  <td><code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.82rem', color: 'var(--label-secondary)' }}>{s.roll_no}</code></td>
                  <td style={{ color: 'var(--label-secondary)' }}>{s.dept}</td>
                  <td style={{ color: 'var(--label-secondary)' }}>{s.year}</td>
                  <td style={{ color: 'var(--label-secondary)' }}>{s.section}</td>
                  <td><RiskBadge score={s.score} band={s.band} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Analytics Page ───────────────────────────────────────────────────────────
function AnalyticsPage() {
  const { apiFetch } = useAuth();
  const [summary, setSummary] = useState(null);
  const [roster, setRoster] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiFetch('/api/summary').then(setSummary).catch(e => setErr(e.message));
    apiFetch('/api/analytics/roster').then(setRoster).catch(e => setErr(e.message));
  }, [apiFetch]);

  if (err) return <ErrorBox msg={err} />;

  const bands = summary?.bands || {};
  const barData = Object.entries(bands).map(([band, count]) => ({ band, count }));
  const bandColors = { High: 'var(--red)', Medium: 'var(--orange)', Low: 'var(--green)' };

  return (
    <div className="page fade-in">
      <div className="page-header">
        <h2>Analytics</h2>
        <p>Cohort-level risk distribution and the full student roster</p>
      </div>

      {summary && (
        <>
          <div className="stat-grid" style={{ marginBottom: '1.5rem' }}>
            <StatCard label="Total Students" value={summary.total_students ?? '—'} />
            <StatCard label="Scored" value={summary.scored ?? '—'}
              sub={summary.not_yet_scoreable
                ? `${summary.not_yet_scoreable} held back — too little history`
                : 'every student scored'}
              title={'A student is only scored once there is enough attendance history '
                + 'to be confident. Below that the system declines to label them '
                + 'rather than guess.'} />
            <StatCard label="High Risk" value={bands.High ?? 0} accent="var(--red)" />
            <StatCard label="Medium Risk" value={bands.Medium ?? 0} accent="var(--orange)" />
            <StatCard label="Low Risk" value={bands.Low ?? 0} accent="var(--green)" />
            <StatCard label="Rising Risk" value={summary.rising ?? 0} sub="worsened this week" />
          </div>

          {/* Risk band composition.
              A linear y-axis cannot show this data: Low is ~100x High, so the
              High band renders under one pixel tall and the one number that
              matters disappears. Part-to-whole belongs in a horizontal stacked
              bar, with every segment given a floor width and a direct label so
              identity never rests on colour alone. */}
          <div className="surface" style={{ marginBottom: '1.5rem', padding: '1.5rem' }}>
            <h3 style={{ marginBottom: '0.35rem' }}>Risk Band Distribution</h3>
            <p style={{ fontSize: '0.82rem', color: 'var(--label-secondary)', marginBottom: '1.25rem' }}>
              {(() => {
                const scored = (bands.Low || 0) + (bands.Medium || 0) + (bands.High || 0);
                const need = (bands.Medium || 0) + (bands.High || 0);
                if (!scored) return 'No scored students yet.';
                return `${need.toLocaleString()} of ${scored.toLocaleString()} scored students need attention (${(need / scored * 100).toFixed(1)}%)`;
              })()}
            </p>
            <CompositionBar bands={bands} />
          </div>
        </>
      )}

      {/* Full roster. At this cohort size every student fits on one screen,
          so the mentor can read the whole cohort rather than a sample. */}
      <div className="surface" style={{ overflow: 'hidden' }}>
        <div className="surface-header">
          <h3>Student Roster</h3>
          <span style={{ fontSize: '0.78rem', color: 'var(--label-secondary)' }}>
            {roster ? `${roster.total} students · lowest attendance first` : ''}
          </span>
        </div>
        {!roster ? (
          <div style={{ padding: '2rem', color: 'var(--label-tertiary)', fontSize: '0.875rem', textAlign: 'center' }}>
            Loading…
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Gender</th>
                <th>Attendance</th>
                <th>CGPA</th>
              </tr>
            </thead>
            <tbody>
              {roster.students.map(r => (
                <tr key={r.roll_no}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{r.name}</div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--label-tertiary)' }}>{r.roll_no}</div>
                  </td>
                  <td style={{ color: 'var(--label-secondary)' }}>{r.gender || '—'}</td>
                  <td style={{ fontFamily: 'var(--font-mono)',
                    color: r.attendance_pct == null ? 'var(--label-tertiary)'
                      : r.attendance_pct < 75 ? 'var(--red)' : 'var(--label-primary)' }}>
                    {r.attendance_pct == null ? 'No data yet' : `${r.attendance_pct}%`}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>
                    {r.cgpa != null ? r.cgpa.toFixed(2) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ─── Model Page ───────────────────────────────────────────────────────────────
function ModelPage() {
  const { apiFetch } = useAuth();
  const [info, setInfo] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiFetch('/api/model')
      .then(setInfo)
      .catch(e => setErr(e.message));
  }, [apiFetch]);

  if (err) return <ErrorBox msg={err} />;
  if (!info) return <LoadingScreen />;

  // excluded_features is an object like { gender: "reason", ... }
  const excludedFeatures = typeof info.excluded_features === 'object' && !Array.isArray(info.excluded_features)
    ? Object.entries(info.excluded_features)
    : (info.excluded_features || []).map(f => [f, '']);

  return (
    <div className="page fade-in">
      <div className="page-header">
        <h2>Model Info</h2>
        <p>Transparency report — what the model predicts and what it refuses to look at</p>
      </div>

      {/* Status card */}
      <div className="surface" style={{ marginBottom: '1.5rem' }}>
        <div className="surface-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <h3>Status</h3>
            <span className={`badge ${info.trained ? 'badge-blue' : 'badge-medium'}`}>{info.mode}</span>
          </div>
          {info.kind && <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.82rem', color: 'var(--label-tertiary)' }}>{info.kind}</code>}
        </div>
        <div style={{ padding: '1.25rem 1.5rem', display: 'grid', gap: '0' }}>
          {!info.trained
            ? <p style={{ color: 'var(--label-secondary)', fontSize: '0.9rem' }}>{info.why}</p>
            : <>
                <InfoRow label="Target" value={info.target} />
                <InfoRow label="Target is not" value={info.target_is_not} />
                <InfoRow label="Horizon" value={`${info.horizon_weeks} weeks`} />
                <InfoRow label="Beats baselines"
                  value={info.beats_baselines == null ? 'Not yet evaluated' : info.beats_baselines ? '✓ Yes' : '✗ No'} />
                <InfoRow label="Beats rules ledger"
                  value={info.beats_rules_ledger == null ? 'Not yet evaluated' : info.beats_rules_ledger ? '✓ Yes' : '✗ No'} />
              </>}
        </div>
      </div>

      {/* Excluded features */}
      {excludedFeatures.length > 0 && (
        <div className="surface" style={{ marginBottom: '1.5rem', overflow: 'hidden' }}>
          <div className="surface-header">
            <h3>Excluded Features</h3>
            <span style={{ fontSize: '0.78rem', color: 'var(--label-tertiary)' }}>
              Protected attributes the model is forbidden from seeing
            </span>
          </div>
          <table className="table">
            <thead><tr><th>Feature</th><th>Reason</th></tr></thead>
            <tbody>
              {excludedFeatures.map(([feat, reason]) => (
                <tr key={feat}>
                  <td><code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.82rem' }}>{feat}</code></td>
                  <td style={{ color: 'var(--label-secondary)', fontSize: '0.85rem' }}>{reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Performance table */}
      {info.overall?.length > 0 && (
        <div className="surface" style={{ overflow: 'hidden' }}>
          <div className="surface-header">
            <h3>Performance vs Baselines</h3>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Model</th>
                <th>N</th>
                <th>ROC-AUC</th>
                <th>PR-AUC</th>
                <th>P@5% Precision</th>
                <th>P@5% Recall</th>
              </tr>
            </thead>
            <tbody>
              {info.overall.map((row, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: row.model?.includes('baseline') ? 400 : 600, fontSize: '0.85rem' }}>
                    {row.model}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>{row.n?.toLocaleString()}</td>
                  <td style={{ fontFamily: 'var(--font-mono)', color: row.roc_auc > 0.75 ? 'var(--green)' : 'var(--label-primary)' }}>
                    {row.roc_auc?.toFixed(4)}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>{row.pr_auc?.toFixed(4)}</td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>
                    {row['p@5%_precision'] != null ? `${(row['p@5%_precision'] * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>
                    {row['p@5%_recall'] != null ? `${(row['p@5%_recall'] * 100).toFixed(1)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Student Detail Page ──────────────────────────────────────────────────────
function StudentDetailPage({ rollNo, onBack }) {
  const { apiFetch, user } = useAuth();
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');

  const reload = useCallback(() => {
    apiFetch(`/api/student/${rollNo}`)
      .then(setData)
      .catch(e => setErr(e.message));
  }, [apiFetch, rollNo]);

  useEffect(() => { reload(); }, [reload]);

  if (err) return (
    <div className="page fade-in">
      <button className="btn btn-secondary btn-sm" onClick={onBack} style={{ marginBottom: '1.5rem' }}>← Back</button>
      <ErrorBox msg={err} />
    </div>
  );
  if (!data) return <LoadingScreen />;

  // Real field names from the API:
  // data.student, data.stage, data.ledger, data.hybrid, data.attendance (array)
  const { student, ledger, hybrid, attendance } = data;
  const modelInfo = hybrid?.model || null;
  const review = data.review || null;

  // Attendance chart data
  const chartData = (attendance || []).map((h, i) => ({
    week: `W${i + 1}`,
    pct: parseFloat(h.pct?.toFixed(1) ?? 0),
  }));

  // Score and band come from ledger
  const score = ledger?.score ?? 0;
  const band = ledger?.band ?? 'Low';
  const components = ledger?.components?.filter(c => c.points > 0) ?? [];

  return (
    <div className="page fade-in">
      <button className="btn btn-secondary btn-sm" onClick={onBack} style={{ marginBottom: '1.5rem' }}>← Back</button>

      {/* Header */}
      <div className="surface" style={{ marginBottom: '1.5rem' }}>
        <div className="surface-header">
          <div>
            <h2>{student.name}</h2>
            <p style={{ marginTop: '0.25rem', fontSize: '0.85rem', color: 'var(--label-tertiary)' }}>
              {student.roll_no} · {student.dept} · Year {student.year} · Section {student.section}
            </p>
          </div>
          <RiskBadge score={score} band={band} large />
        </div>

        <div className="detail-grid" style={{ padding: '1.5rem' }}>
          {/* Left: Risk factors */}
          <div>
            <div style={{ fontSize: '0.72rem', fontWeight: 600, textTransform: 'uppercase',
              letterSpacing: '0.06em', color: 'var(--label-tertiary)', marginBottom: '0.875rem' }}>
              Risk Factors
            </div>
            {components.length === 0 ? (
              <p style={{ color: 'var(--label-tertiary)', fontSize: '0.875rem' }}>No active risk factors.</p>
            ) : (
              <div className="factor-list">
                {components.map((c, i) => (
                  <div key={i} className="factor-item">
                    <div>
                      <div className="factor-name">{c.label}</div>
                      <div className="factor-reason">{c.reason}</div>
                    </div>
                    <div className="factor-pts">+{c.points}</div>
                  </div>
                ))}
              </div>
            )}

            {/* Key stats below factors */}
            <div style={{ marginTop: '1.25rem', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
              <MiniStat label="Backlogs" value={student.backlogs ?? '—'} />
              <MiniStat label="Submissions" value={student.submission_pct != null ? `${student.submission_pct}%` : '—'} />
              <MiniStat label="Fee Status" value={student.fee_status ?? '—'} />
              <MiniStat label="CGPA"
                value={student.cgpa != null ? student.cgpa.toFixed(2) : '—'} />
            </div>
          </div>

          {/* Right: chart + AI */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            <div>
              <div style={{ fontSize: '0.72rem', fontWeight: 600, textTransform: 'uppercase',
                letterSpacing: '0.06em', color: 'var(--label-tertiary)', marginBottom: '1rem' }}>
                Attendance Trend
              </div>
              {chartData.length === 0 ? (
                <p style={{ color: 'var(--label-tertiary)', fontSize: '0.875rem' }}>No attendance data yet.</p>
              ) : (
                <div style={{ height: 200 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.06)" />
                      <XAxis dataKey="week"
                        tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 11 }}
                        axisLine={false} tickLine={false}
                        interval={Math.floor(chartData.length / 6)} />
                      <YAxis tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 11 }}
                        axisLine={false} tickLine={false} domain={[0, 100]}
                        tickFormatter={v => `${v}%`} />
                      <Tooltip
                        contentStyle={{ backgroundColor: '#1a1a1a', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 10, fontSize: 13 }}
                        itemStyle={{ color: 'rgba(255,255,255,0.85)' }}
                        labelStyle={{ color: 'rgba(255,255,255,0.4)', fontSize: 11 }}
                        formatter={v => [`${v}%`, 'Attendance']}
                      />
                      {/* 75% is the attendance requirement the whole ledger is
                          scored against. Drawing it turns the line from "some
                          numbers" into "how far under the bar this student is". */}
                      <ReferenceLine y={75} stroke="var(--orange)" strokeDasharray="4 4"
                        strokeOpacity={0.7}
                        label={{ value: '75% required', position: 'insideTopRight',
                                 fill: 'var(--orange)', fontSize: 10 }} />
                      <Line type="monotone" dataKey="pct" stroke="var(--accent)"
                        strokeWidth={2}
                        dot={(props) => {
                          const { cx, cy, payload } = props;
                          const color = payload.pct < 75 ? 'var(--red)' : 'var(--accent)';
                          return <circle key={cx} cx={cx} cy={cy} r={3} fill={color} strokeWidth={0} />;
                        }}
                        activeDot={{ r: 5, fill: 'var(--accent)' }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            {/* AI review. The prediction bar needs a trained model and enough
                history; the plain-language read of what is wrong does not, so
                the panel still says something useful for a new admission. */}
            {(modelInfo?.available || review) && (
              <div className="model-panel">
                <div className="model-panel-title">
                  <svg width="13" height="13" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd"/>
                  </svg>
                  {modelInfo?.available
                    ? `AI Review (${modelInfo.horizon_weeks}w prediction)`
                    : 'AI Review'}
                </div>
                {modelInfo?.available && (
                <p style={{ fontSize: '0.875rem', color: 'var(--label-secondary)', marginBottom: '0.875rem' }}>
                  {modelInfo.statement}
                </p>
                )}
                {modelInfo?.available && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.875rem' }}>
                  <div style={{ flex: 1 }}>
                    <div className="risk-bar-track">
                      <div className="risk-bar-fill"
                        style={{
                          width: `${modelInfo.percent}%`,
                          background: modelInfo.percent > 60 ? 'var(--red)' : modelInfo.percent > 35 ? 'var(--orange)' : 'var(--green)',
                        }} />
                    </div>
                  </div>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.9rem', fontWeight: 700,
                    color: modelInfo.percent > 60 ? 'var(--red)' : modelInfo.percent > 35 ? 'var(--orange)' : 'var(--green)' }}>
                    {modelInfo.percent}%
                  </span>
                </div>
                )}
                {/* What is actually wrong, in the words a mentor would use.
                    The SHAP contributions this replaced answered "which input
                    moved the model", which is not a question anyone about to
                    phone a student needs answered. */}
                {review && (
                  <>
                    <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.06em',
                      color: 'var(--label-tertiary)', marginBottom: '0.5rem' }}>What this means</div>
                    <p style={{ fontSize: '0.9rem', lineHeight: 1.5, color: 'var(--label-primary)',
                      marginBottom: review.concerns?.length ? '0.75rem' : 0 }}>
                      {review.summary}
                    </p>
                    {review.concerns?.map((c, i) => (
                      <div key={`c-${i}`} className="review-line">
                        <span className="review-dot review-dot-bad" aria-hidden="true" />
                        <span>{c.text}</span>
                      </div>
                    ))}
                    {review.positives?.map((c, i) => (
                      <div key={`p-${i}`} className="review-line">
                        <span className="review-dot review-dot-good" aria-hidden="true" />
                        <span style={{ color: 'var(--label-secondary)' }}>{c.text}</span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      <ActionPanel
        rollNo={rollNo}
        playbook={data.suggested_playbook}
        interventions={data.interventions || []}
        mentorId={user?.id}
        onChanged={reload}
      />
    </div>
  );
}

// ─── Action loop ──────────────────────────────────────────────────────────────
// The backend suggests a playbook, records the mentor's approval, and later
// measures whether it worked -- but none of that was reachable from the UI, so
// detect -> act -> measure had no "act" step. This is it.

const OUTCOMES = [
  ['improved', 'Improved'],
  ['unchanged', 'Unchanged'],
  ['worsened', 'Worsened'],
  ['unreachable', 'Unreachable'],
];

function ActionPanel({ rollNo, playbook, interventions, mentorId, onChanged }) {
  const { apiFetch } = useAuth();
  const [draft, setDraft] = useState(playbook?.draft || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { setDraft(playbook?.draft || ''); }, [playbook]);

  const open = interventions.filter(i => i.status === 'open');
  const closed = interventions.filter(i => i.status !== 'open');

  const approve = async () => {
    if (!playbook) return;
    setBusy(true); setError('');
    try {
      await apiFetch('/api/interventions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          roll_no: rollNo, mentor: mentorId, playbook: playbook.title,
          trigger: playbook.trigger, action_text: draft,
          followup_days: playbook.followup_days ?? 21,
        }),
      });
      onChanged();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const close = async (id, outcome) => {
    setBusy(true); setError('');
    try {
      await apiFetch(`/api/interventions/${id}/outcome`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ outcome, mentor: mentorId }),
      });
      onChanged();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ marginTop: '1.5rem', display: 'grid', gap: '1.5rem',
      gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))' }}>

      {/* Recommended action */}
      <div className="surface" style={{ padding: '1.5rem' }}>
        <h3 style={{ marginBottom: '0.75rem' }}>Recommended action</h3>
        {!playbook ? (
          <p style={{ fontSize: '0.875rem', color: 'var(--label-tertiary)' }}>
            No action suggested. This student is not currently flagged.
          </p>
        ) : (
          <>
            <div style={{ fontWeight: 600, marginBottom: '0.35rem' }}>{playbook.title}</div>
            <p style={{ fontSize: '0.8rem', color: 'var(--label-tertiary)', marginBottom: '0.875rem' }}>
              Because: {playbook.because}
            </p>
            <textarea className="input" rows={5} value={draft}
              onChange={e => setDraft(e.target.value)}
              style={{ width: '100%', resize: 'vertical', fontSize: '0.85rem', lineHeight: 1.5 }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.875rem', marginTop: '0.875rem', flexWrap: 'wrap' }}>
              <button className="btn btn-primary" onClick={approve} disabled={busy || !draft.trim()}>
                {busy ? 'Saving…' : 'Approve & log action'}
              </button>
              <span style={{ fontSize: '0.78rem', color: 'var(--label-tertiary)' }}>
                Follow up in {playbook.followup_days ?? 21} days
              </span>
            </div>
            {playbook.requires_mentor_approval && (
              <p style={{ fontSize: '0.75rem', color: 'var(--label-tertiary)', marginTop: '0.75rem' }}>
                Nothing is sent automatically. The system drafts; a mentor decides.
              </p>
            )}
          </>
        )}
        {error && <div className="login-error" style={{ marginTop: '0.875rem' }}>{error}</div>}
      </div>

      {/* History */}
      <div className="surface" style={{ padding: '1.5rem' }}>
        <h3 style={{ marginBottom: '0.75rem' }}>
          Interventions <span style={{ color: 'var(--label-tertiary)', fontWeight: 400 }}>({interventions.length})</span>
        </h3>
        {interventions.length === 0 ? (
          <p style={{ fontSize: '0.875rem', color: 'var(--label-tertiary)' }}>
            Nothing logged for this student yet.
          </p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>
            {[...open, ...closed].map(iv => (
              <div key={iv.id} style={{ borderTop: '1px solid var(--separator)', paddingTop: '0.75rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', alignItems: 'baseline' }}>
                  <span style={{ fontWeight: 500, fontSize: '0.875rem' }}>{iv.playbook}</span>
                  <span className={`badge ${iv.status === 'open' ? 'badge-blue'
                    : iv.outcome === 'improved' ? 'badge-low'
                    : iv.outcome === 'worsened' ? 'badge-high' : 'badge-none'}`}>
                    {iv.status === 'open' ? 'Open' : (iv.outcome || 'closed')}
                  </span>
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--label-tertiary)', marginTop: '0.2rem' }}>
                  {iv.mentor} · opened {iv.created_at}
                  {iv.measured_change != null && (
                    <> · measured {iv.measured_change > 0 ? '+' : ''}{iv.measured_change} pts attendance</>
                  )}
                </div>
                {iv.status === 'open' && (
                  <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.6rem', flexWrap: 'wrap' }}>
                    {OUTCOMES.map(([val, label]) => (
                      <button key={val} className="btn btn-secondary btn-sm"
                        disabled={busy} onClick={() => close(iv.id, val)}>{label}</button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Shared Components ────────────────────────────────────────────────────────

// Part-to-whole across three status bands whose counts differ by ~100x.
// Segments get a minimum width so a small-but-critical band stays visible and
// clickable, a 2px surface gap so adjacent fills read as separate marks, and a
// direct label each so the chart is not colour-alone (the risk palette sits in
// the CVD floor band, which is only legal with secondary encoding).
const BAND_ORDER = ['Low', 'Medium', 'High'];
const BAND_FILL = { Low: 'var(--green)', Medium: 'var(--orange)', High: 'var(--red)' };

function CompositionBar({ bands }) {
  const total = BAND_ORDER.reduce((a, b) => a + (bands[b] || 0), 0);
  if (!total) {
    return <div style={{ color: 'var(--label-tertiary)', fontSize: '0.875rem' }}>No scored students yet.</div>;
  }
  return (
    <div>
      <div style={{ display: 'flex', gap: 2, height: 30, marginBottom: '1rem' }}
        role="img"
        aria-label={BAND_ORDER.map(b => `${b} ${bands[b] || 0}`).join(', ')}>
        {BAND_ORDER.map((b, i) => {
          const v = bands[b] || 0;
          if (!v) return null;
          const pct = v / total * 100;
          return (
            <div key={b}
              title={`${b}: ${v.toLocaleString()} students (${pct.toFixed(1)}%)`}
              style={{
                flex: `${pct} 1 0`,
                minWidth: 8,
                background: BAND_FILL[b],
                borderRadius: `${i === 0 ? '4px' : '0'} ${i === BAND_ORDER.length - 1 ? '4px' : '0'} ${i === BAND_ORDER.length - 1 ? '4px' : '0'} ${i === 0 ? '4px' : '0'}`,
              }} />
          );
        })}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem' }}>
        {BAND_ORDER.map(b => {
          const v = bands[b] || 0;
          return (
            <div key={b} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: BAND_FILL[b], flexShrink: 0 }} />
              <span style={{ fontSize: '0.82rem', color: 'var(--label-secondary)' }}>{b}</span>
              <span style={{ fontSize: '0.82rem', fontFamily: 'var(--font-mono)', color: 'var(--label-primary)' }}>
                {v.toLocaleString()}
              </span>
              <span style={{ fontSize: '0.78rem', color: 'var(--label-tertiary)' }}>
                {(v / total * 100).toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// lifecycle.py stores the stage key; these are its own labels. Showing the raw
// key ("full") in the table tells a reader nothing.
const STAGE_LABEL = {
  onboarding: 'Collecting data',
  rules_only: 'Rules only',
  full: 'Rules + model',
};
const STAGE_HELP = {
  onboarding: 'Profile created. Attendance and assessment data are still accumulating, so no risk assessment is shown yet.',
  rules_only: 'Enough attendance history for the threshold rules, but under the 10 weeks the model needs, so no prediction is shown.',
  full: 'Full history available. Both the transparent rules ledger and the model prediction are shown.',
};

function StageChip({ stage }) {
  if (!stage) return <span style={{ color: 'var(--label-tertiary)' }}>—</span>;
  return (
    <span title={STAGE_HELP[stage] || ''}
      style={{ fontSize: '0.72rem', color: 'var(--label-secondary)',
        border: '1px solid var(--separator)', borderRadius: 'var(--r-xs)',
        padding: '0.15rem 0.45rem', whiteSpace: 'nowrap' }}>
      {STAGE_LABEL[stage] || stage}
    </span>
  );
}

function RiskBadge({ score, band, large }) {
  // A student with too little history is NOT low risk -- they are unscoreable,
  // and saying "/100 - -" reads as a rendering bug rather than a real state.
  if (score == null || !band) {
    return (
      <span className="badge badge-none" title="Not enough attendance history yet to score this student"
        style={large ? { fontSize: '0.875rem', padding: '0.375rem 0.875rem' } : {}}>
        Not scored yet
      </span>
    );
  }
  const cls = { High: 'badge-high', Medium: 'badge-medium', Low: 'badge-low' }[band] || 'badge-low';
  return (
    <span className={`badge ${cls}`}
      style={large ? { fontSize: '0.875rem', padding: '0.375rem 0.875rem' } : {}}>
      {score}/100 · {band}
    </span>
  );
}

function DeltaChip({ delta }) {
  if (delta == null || delta === 0)
    return <span style={{ color: 'var(--label-tertiary)', fontSize: '0.82rem' }}>—</span>;
  const up = delta > 0;
  return (
    <span style={{ color: up ? 'var(--red)' : 'var(--green)', fontSize: '0.82rem',
      fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
      {up ? '+' : ''}{delta}
    </span>
  );
}

function StatCard({ label, value, sub, accent, title }) {
  return (
    <div className="stat-card" title={title || undefined}>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={accent ? { color: accent } : {}}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div style={{ background: 'var(--fill-primary)', borderRadius: 8, padding: '0.625rem 0.875rem' }}>
      <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.06em',
        color: 'var(--label-tertiary)', marginBottom: '0.2rem' }}>{label}</div>
      <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>{value}</div>
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.625rem 0',
      borderBottom: '1px solid var(--separator)', fontSize: '0.875rem' }}>
      <span style={{ color: 'var(--label-tertiary)' }}>{label}</span>
      <span style={{ fontWeight: 500, textAlign: 'right', maxWidth: '60%' }}>{value}</span>
    </div>
  );
}

function ErrorBox({ msg }) {
  return (
    <div className="page">
      <div className="alert alert-danger">
        <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" style={{ flexShrink: 0 }}>
          <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd"/>
        </svg>
        <div><strong>Error loading data</strong><br />{msg}</div>
      </div>
    </div>
  );
}

// ─── Nav Icons ────────────────────────────────────────────────────────────────
function IconClipboard() {
  return (
    <svg className="nav-icon" viewBox="0 0 20 20" fill="currentColor">
      <path d="M9 2a1 1 0 000 2h2a1 1 0 100-2H9z"/>
      <path fillRule="evenodd" d="M4 5a2 2 0 012-2 3 3 0 003 3h2a3 3 0 003-3 2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V5zm3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3zm-3 4a1 1 0 100 2h.01a1 1 0 100-2H7zm3 0a1 1 0 100 2h3a1 1 0 100-2h-3z" clipRule="evenodd"/>
    </svg>
  );
}
function IconUsers() {
  return (
    <svg className="nav-icon" viewBox="0 0 20 20" fill="currentColor">
      <path d="M9 6a3 3 0 11-6 0 3 3 0 016 0zM17 6a3 3 0 11-6 0 3 3 0 016 0zM12.93 17c.046-.327.07-.66.07-1a6.97 6.97 0 00-1.5-4.33A5 5 0 0119 16v1h-6.07zM6 11a5 5 0 015 5v1H1v-1a5 5 0 015-5z"/>
    </svg>
  );
}
function IconChart() {
  return (
    <svg className="nav-icon" viewBox="0 0 20 20" fill="currentColor">
      <path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z"/>
    </svg>
  );
}
function IconBolt() {
  return (
    <svg className="nav-icon" viewBox="0 0 20 20" fill="currentColor">
      <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd"/>
    </svg>
  );
}
