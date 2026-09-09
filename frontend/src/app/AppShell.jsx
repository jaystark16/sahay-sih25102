import { useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { NAV } from '../lib/constants';
import { NAV_ICONS, IconLogout, IconMenu } from '../components/ui/Icons';
import { IconButton, cx } from '../components/ui/Primitives';
import { WorklistPage } from '../pages/WorklistPage';
import { StudentsPage } from '../pages/StudentsPage';
import { StudentDetailPage } from '../pages/StudentDetailPage';
import { AnalyticsPage } from '../pages/AnalyticsPage';
import { ModelPage } from '../pages/ModelPage';
import { AdminPage } from '../pages/AdminPage';

/**
 * The application frame.
 *
 * Navigation is local state rather than a router: the app has five
 * destinations and no deep links, so adding react-router would be a
 * dependency and a bundle cost for nothing. If shareable student URLs are
 * ever wanted, that is the point to revisit it.
 *
 * On narrow screens the sidebar becomes an off-canvas drawer rather than a
 * squeezed column.
 */
export function AppShell() {
  const { user, logout, isStaff } = useAuth();
  const [view, setView] = useState('worklist');
  const [selectedRoll, setSelectedRoll] = useState(null);
  const [navOpen, setNavOpen] = useState(false);
  // Set when another screen sends the user to the directory pre-filtered --
  // clicking "At risk 480" should land on those 480, not on everyone.
  const [studentsFilter, setStudentsFilter] = useState('all');

  const nav = NAV.filter((n) => !n.staffOnly || isStaff);

  const go = (id) => {
    setView(id);
    setSelectedRoll(null);
    setNavOpen(false);
    if (id !== 'students') setStudentsFilter('all');
  };

  /** Drill down from a metric or a chart into the matching student list. */
  const showStudents = (filter = 'all') => {
    setStudentsFilter(filter);
    setView('students');
    setSelectedRoll(null);
    setNavOpen(false);
  };

  const openStudent = (rollNo) => {
    setSelectedRoll(rollNo);
    setNavOpen(false);
  };

  // Close the drawer if the viewport grows past the breakpoint while it is open.
  useEffect(() => {
    if (!navOpen) return undefined;
    const mq = window.matchMedia('(min-width: 900px)');
    const onChange = (e) => { if (e.matches) setNavOpen(false); };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [navOpen]);

  const scope = user?.role === 'mentor'
    ? [user.dept, user.section].filter(Boolean).join(' · ') || 'Your sections'
    : 'All departments';

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to main content</a>

      <aside className={cx('sidebar', navOpen && 'is-open')}>
        <div className="sidebar__brand">
          <span className="sidebar__mark" aria-hidden="true">S</span>
          <span className="sidebar__brand-text">
            <strong>Sahay</strong>
            <small>Early warning &amp; support</small>
          </span>
        </div>

        <nav className="sidebar__nav" aria-label="Main">
          {nav.map((item) => {
            const Icon = NAV_ICONS[item.icon];
            const active = view === item.id && !selectedRoll;
            return (
              <button
                key={item.id}
                type="button"
                className={cx('nav-item', active && 'is-active')}
                aria-current={active ? 'page' : undefined}
                title={item.hint}
                onClick={() => go(item.id)}
              >
                {Icon && <Icon />}
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar__foot">
          <div className="sidebar__user">
            <span className="avatar" aria-hidden="true">{initials(user?.name)}</span>
            <span className="sidebar__user-text">
              <strong>{user?.name || user?.email}</strong>
              <small>{scope}</small>
            </span>
          </div>
          <IconButton label="Sign out" onClick={logout}
            icon={<IconLogout width={16} height={16} />} />
        </div>
      </aside>

      {navOpen && (
        <button type="button" className="scrim" aria-label="Close navigation"
          onClick={() => setNavOpen(false)} />
      )}

      <div className="shell__main">
        <header className="topbar">
          <IconButton label="Open navigation" className="topbar__menu"
            onClick={() => setNavOpen(true)} icon={<IconMenu />} />
          <div className="topbar__title">
            <h1>{selectedRoll ? 'Student record' : currentLabel(nav, view)}</h1>
            <p>{selectedRoll ? selectedRoll : (nav.find((n) => n.id === view)?.hint || '')}</p>
          </div>
        </header>

        <main id="main" className="content" tabIndex={-1}>
          {selectedRoll ? (
            <StudentDetailPage
              rollNo={selectedRoll}
              onBack={() => setSelectedRoll(null)}
            />
          ) : (
            <>
              {view === 'worklist' && (
                <WorklistPage onSelectStudent={openStudent} onDrillDown={showStudents} />
              )}
              {view === 'students' && (
                <StudentsPage
                  key={studentsFilter}
                  onSelectStudent={openStudent}
                  defaultRisk={studentsFilter}
                />
              )}
              {view === 'analytics' && <AnalyticsPage onDrillDown={showStudents} />}
              {view === 'model' && <ModelPage />}
              {view === 'admin' && isStaff && <AdminPage />}
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function currentLabel(nav, view) {
  return nav.find((n) => n.id === view)?.label || 'Sahay';
}

function initials(name) {
  if (!name) return '?';
  return name
    .replace(/^(Dr|Prof|Mr|Ms|Mrs)\.?\s+/i, '')
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0])
    .join('')
    .toUpperCase();
}
