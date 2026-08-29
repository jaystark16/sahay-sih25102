import { useState, useEffect, useCallback, createContext, useContext } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, BarChart, Bar, Cell,
} from 'recharts';
import './index.css';

// ─── Auth Context ─────────────────────────────────────────────────────────────
const AuthCtx = createContext(null);
const useAuth = () => useContext(AuthCtx);

function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => localStorage.getItem('sahay_token') || '');
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (!token) { setChecking(false); return; }
    fetch('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(u => { setUser(u); setChecking(false); })
      .catch(() => setChecking(false));
  }, [token]);

  const login = useCallback(async (userId, password) => {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, password }),
    });
    if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Login failed'); }
    const d = await r.json();
    localStorage.setItem('sahay_token', d.token);
    setToken(d.token);
    setUser(d.user);
  }, []);

  const logout = useCallback(async () => {
    await fetch('/api/auth/logout', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    }).catch(() => {});
    localStorage.removeItem('sahay_token');
    setToken('');
    setUser(null);
  }, [token]);

  const apiFetch = useCallback((url, opts = {}) => {
    return fetch(url, {
      ...opts,
      headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` },
    });
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
            <label className="input-label" htmlFor="user-id">Username</label>
            <input id="user-id" className="input" type="text"
              placeholder="e.g. admin, hod, mcs1a"
              value={userId} onChange={e => setUserId(e.target.value)}
              autoComplete="username" required />
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
          Demo: <strong>admin</strong> / <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: 'var(--accent)' }}>Sahay@Admin2025</code>
          <br />or <strong>hod</strong> / <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: 'var(--accent)' }}>Sahay@Hod2025</code>
        </p>
      </div>
    </div>
  );
}

// ─── Add Student Modal ────────────────────────────────────────────────────────
function AddStudentModal({ onClose, onSuccess }) {
  const { apiFetch } = useAuth();
  const [formData, setFormData] = useState({ name: '', dept: '', year: 1, section: 'A' });
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
        body: JSON.stringify(formData),
      });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed to add student'); }
      const d = await r.json();
      onSuccess(d);
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
            <label className="input-label">Name <span style={{color:'var(--error)'}}>*</span></label>
            <input className="input" required value={formData.name} onChange={e => setFormData({...formData, name: e.target.value})} placeholder="e.g. Jane Doe" />
          </div>
          <div>
            <label className="input-label">Department <span style={{color:'var(--error)'}}>*</span></label>
            <input className="input" required value={formData.dept} onChange={e => setFormData({...formData, dept: e.target.value})} placeholder="e.g. CSE" />
          </div>
          <div style={{ display: 'flex', gap: '1rem' }}>
            <div style={{ flex: 1 }}>
              <label className="input-label">Year (1-4) <span style={{color:'var(--error)'}}>*</span></label>
              <input className="input" type="number" min="1" max="4" required value={formData.year} onChange={e => setFormData({...formData, year: parseInt(e.target.value)})} />
            </div>
            <div style={{ flex: 1 }}>
              <label className="input-label">Section</label>
              <input className="input" required value={formData.section} onChange={e => setFormData({...formData, section: e.target.value})} placeholder="e.g. A" />
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

// ─── App Shell ────────────────────────────────────────────────────────────────
function AppShell() {
  const { user, logout } = useAuth();
  const [view, setView] = useState('worklist');
  const [selectedRollNo, setSelectedRollNo] = useState(null);
  const [showAddStudent, setShowAddStudent] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
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
          <button className="btn btn-primary" onClick={() => setShowAddStudent(true)}>+ Add Student</button>
          <div className="user-pill" onClick={logout} title="Click to sign out" style={{ padding: '0.375rem 0.75rem', margin: 0, background: 'var(--fill-secondary)' }}>
            <div className="user-avatar" style={{ width: 28, height: 28, fontSize: '0.65rem' }}>{initials}</div>
            <div>
              <div className="user-name">{user.name}</div>
              <div className="user-role">{user.role} · Sign out</div>
            </div>
          </div>
        </header>

        {view === 'worklist' && !selectedRollNo && <WorklistPage key={refreshKey} onSelect={goToStudent} onNavigateToRoster={goToStudents} />}
        {view === 'detail'   && selectedRollNo   && <StudentDetailPage rollNo={selectedRollNo} onBack={goBack} />}
        {view === 'students' && <StudentsPage key={`students-${studentRiskFilter}`} defaultRisk={studentRiskFilter} onSelect={goToStudent} />}
        {view === 'analytics' && <AnalyticsPage />}
        {view === 'model'    && <ModelPage />}
      </main>

      {showAddStudent && (
        <AddStudentModal
          onClose={() => setShowAddStudent(false)}
          onSuccess={(res) => {
            setShowAddStudent(false);
            alert(`Success! Roll No: ${res.roll_no}. ${res.message || ''}`);
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
    
    apiFetch(url).then(r => r.json()).then(setData).catch(e => setErr(e.message));
    apiFetch(dashUrl).then(r => r.json()).then(setDash).catch(console.error);
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
          {data.this_week?.length ?? 0} students flagged for attention this week
        </p>
      </div>

      {dash && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1.25rem', marginBottom: '2.5rem' }}>
          <div className="surface" style={{ padding: '1.25rem', cursor: 'pointer', transition: 'transform 0.15s, box-shadow 0.15s' }} 
               onClick={() => onNavigateToRoster('at_risk')}
               onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 6px 16px rgba(0,0,0,0.3)'; }}
               onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}>
            <div style={{ fontSize: '0.85rem', color: 'var(--label-secondary)', marginBottom: '0.375rem' }}>Students at risk</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 600, color: 'var(--accent)' }}>{dash.at_risk}</div>
          </div>
          <div className="surface" style={{ padding: '1.25rem', cursor: 'pointer', transition: 'transform 0.15s, box-shadow 0.15s' }} 
               onClick={() => onNavigateToRoster('all')}
               onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 6px 16px rgba(0,0,0,0.3)'; }}
               onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}>
            <div style={{ fontSize: '0.85rem', color: 'var(--label-secondary)', marginBottom: '0.375rem' }}>Total students</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 600 }}>{dash.total_students}</div>
          </div>
          <div className="surface" style={{ padding: '1.25rem' }}>
            <div style={{ fontSize: '0.85rem', color: 'var(--label-secondary)', marginBottom: '0.375rem' }}>Risk rising</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 600, color: 'var(--error)' }}>{dash.rising}</div>
          </div>
          <div className="surface" style={{ padding: '1.25rem' }}>
            <div style={{ fontSize: '0.85rem', color: 'var(--label-secondary)', marginBottom: '0.375rem' }}>Average CGPA</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 600, color: 'var(--success)' }}>{dash.average_cgpa}</div>
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
                className="fade-in" style={{ animationDelay: `${i * 0.04}s` }}>
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
                <td style={{ fontSize: '0.8rem', color: 'var(--label-secondary)', maxWidth: 220 }}>
                  <span className="truncate" style={{ display: 'block' }}>{s.headline}</span>
                </td>
                <td><span style={{ fontSize: '0.78rem', color: 'var(--label-tertiary)' }}>{s.stage || '—'}</span></td>
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
    apiFetch(`/api/students?${params}`).then(r => r.json()).then(setData).catch(console.error);
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
        {data && (
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
            <button className="btn btn-outline" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Prev</button>
            <span style={{ fontSize: '0.9rem', color: 'var(--label-secondary)' }}>Page {page}</span>
            <button className="btn btn-outline" disabled={!data.students || data.students.length < 50} onClick={() => setPage(p => p + 1)}>Next</button>
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
                  className="fade-in" style={{ animationDelay: `${i * 0.02}s` }}>
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
  const [fairness, setFairness] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiFetch('/api/summary').then(r => r.json()).then(setSummary).catch(e => setErr(e.message));
    apiFetch('/api/analytics/fairness').then(r => r.json()).then(setFairness).catch(console.error);
  }, [apiFetch]);

  if (err) return <ErrorBox msg={err} />;

  // Flatten fairness dimensions into rows for display
  const fairnessRows = fairness?.dimensions
    ? Object.entries(fairness.dimensions).flatMap(([dim, items]) =>
        items.map(it => ({ dimension: dim, ...it }))
      )
    : [];

  const bands = summary?.bands || {};
  const barData = Object.entries(bands).map(([band, count]) => ({ band, count }));
  const bandColors = { High: 'var(--red)', Medium: 'var(--orange)', Low: 'var(--green)' };

  return (
    <div className="page fade-in">
      <div className="page-header">
        <h2>Analytics</h2>
        <p>Cohort-level risk distribution and fairness audit</p>
      </div>

      {summary && (
        <>
          <div className="stat-grid" style={{ marginBottom: '1.5rem' }}>
            <StatCard label="Total Students" value={summary.total_students ?? '—'} />
            <StatCard label="Scored" value={summary.scored ?? '—'} sub="with full data" />
            <StatCard label="High Risk" value={bands.High ?? 0} accent="var(--red)" />
            <StatCard label="Medium Risk" value={bands.Medium ?? 0} accent="var(--orange)" />
            <StatCard label="Low Risk" value={bands.Low ?? 0} accent="var(--green)" />
            <StatCard label="Rising Risk" value={summary.rising ?? 0} sub="worsened this week" />
          </div>

          {/* Risk Band Chart */}
          <div className="surface" style={{ marginBottom: '1.5rem', padding: '1.5rem' }}>
            <h3 style={{ marginBottom: '1.25rem' }}>Risk Band Distribution</h3>
            <div style={{ height: 180 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={barData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.06)" vertical={false} />
                  <XAxis dataKey="band" tick={{ fill: 'rgba(255,255,255,0.4)', fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: 'rgba(255,255,255,0.4)', fontSize: 12 }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#1a1a1a', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 10, fontSize: 13 }}
                    itemStyle={{ color: 'rgba(255,255,255,0.85)' }}
                    cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                    formatter={v => [v, 'Students']}
                  />
                  <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                    {barData.map((entry) => (
                      <Cell key={entry.band} fill={bandColors[entry.band] || 'var(--accent)'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </>
      )}

      {/* Fairness Audit */}
      <div className="surface" style={{ overflow: 'hidden' }}>
        <div className="surface-header">
          <h3>Fairness Audit</h3>
          <span style={{ fontSize: '0.78rem', color: 'var(--label-tertiary)' }}>
            Disparity ratio vs least-flagged group · flags &gt;1.25 are reviewed
          </span>
        </div>
        {fairnessRows.length === 0 ? (
          <div style={{ padding: '2rem', color: 'var(--label-tertiary)', fontSize: '0.875rem', textAlign: 'center' }}>
            {fairness ? 'No fairness data available yet.' : 'Loading…'}
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Dimension</th>
                <th>Group</th>
                <th>N</th>
                <th>Flagged</th>
                <th>Flag Rate</th>
                <th>Disparity Ratio</th>
                <th>Review?</th>
              </tr>
            </thead>
            <tbody>
              {fairnessRows.map((r, i) => (
                <tr key={i}>
                  <td style={{ color: 'var(--label-tertiary)', textTransform: 'capitalize' }}>{r.dimension.replace('_', ' ')}</td>
                  <td style={{ fontWeight: 500 }}>{r.group}</td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>{r.n}</td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>{r.flagged}</td>
                  <td style={{ fontFamily: 'var(--font-mono)', color: r.flag_rate > 70 ? 'var(--orange)' : 'var(--label-primary)' }}>
                    {r.flag_rate?.toFixed(1)}%
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', color: r.disparity_ratio > 1.25 ? 'var(--red)' : 'var(--label-primary)' }}>
                    {r.disparity_ratio?.toFixed(2)}×
                  </td>
                  <td>
                    {r.review
                      ? <span className="badge badge-high" style={{ fontSize: '0.7rem' }}>Review</span>
                      : <span style={{ color: 'var(--green)', fontSize: '0.78rem' }}>✓ OK</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {fairness?.note && (
          <div style={{ padding: '0.875rem 1.25rem', borderTop: '1px solid var(--separator)', fontSize: '0.78rem', color: 'var(--label-tertiary)' }}>
            {fairness.note}
          </div>
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
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
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
  const { apiFetch } = useAuth();
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiFetch(`/api/student/${rollNo}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setData)
      .catch(e => setErr(e.message));
  }, [apiFetch, rollNo]);

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
              <MiniStat label="IA Latest"
                value={student.ia3 != null ? `${((student.ia3 / (student.ia_max || 30)) * 100).toFixed(0)}%`
                  : student.ia2 != null ? `${((student.ia2 / (student.ia_max || 30)) * 100).toFixed(0)}%` : '—'} />
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
                      {/* Red zone below 75% */}
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

            {/* AI prediction */}
            {modelInfo?.available && (
              <div className="model-panel">
                <div className="model-panel-title">
                  <svg width="13" height="13" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd"/>
                  </svg>
                  AI Prediction ({modelInfo.horizon_weeks}w horizon)
                </div>
                <p style={{ fontSize: '0.875rem', color: 'var(--label-secondary)', marginBottom: '0.875rem' }}>
                  {modelInfo.statement}
                </p>
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
                {modelInfo.contributions?.length > 0 && (
                  <>
                    <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.06em',
                      color: 'var(--label-tertiary)', marginBottom: '0.5rem' }}>Key Factors</div>
                    {modelInfo.contributions.map((c, i) => (
                      <div key={i} className="contrib-row">
                        <span style={{ color: 'var(--label-secondary)' }}>
                          {c.label}{' '}
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.76rem', color: 'var(--label-tertiary)' }}>
                            ({typeof c.value === 'number' ? c.value.toFixed(1) : c.value})
                          </span>
                        </span>
                        <span className={c.log_odds > 0 ? 'contrib-dir-pos' : 'contrib-dir-neg'}>
                          {c.log_odds > 0 ? '↑ Raises risk' : '↓ Lowers risk'}
                        </span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Shared Components ────────────────────────────────────────────────────────
function RiskBadge({ score, band, large }) {
  const cls = { High: 'badge-high', Medium: 'badge-medium', Low: 'badge-low' }[band] || 'badge-low';
  return (
    <span className={`badge ${cls}`}
      style={large ? { fontSize: '0.875rem', padding: '0.375rem 0.875rem' } : {}}>
      {score}/100 · {band || '—'}
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

function StatCard({ label, value, sub, accent }) {
  return (
    <div className="stat-card">
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
