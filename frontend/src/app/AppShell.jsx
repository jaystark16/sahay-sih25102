import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { NAV } from '../lib/constants';
import { NAV_ICONS, IconLogout, IconMenu, IconPlus, IconTrash } from '../components/ui/Icons';
import { Badge, Button, IconButton, cx } from '../components/ui/Primitives';
import { useToast } from '../components/ui/Toast';
import api from '../services/api';
import { AddStudentModal, RemoveStudentModal } from '../features/students/StudentAdminModals';
import { AddToCaseloadModal, DropFromCaseloadModal } from '../features/students/CaseloadModals';
import { InstitutionPage } from '../pages/InstitutionPage';
import { WorklistPage } from '../pages/WorklistPage';
import { StudentsPage } from '../pages/StudentsPage';
import { StudentDetailPage } from '../pages/StudentDetailPage';
import { AnalyticsPage } from '../pages/AnalyticsPage';
import { ModelPage } from '../pages/ModelPage';
import { AdminPage } from '../pages/AdminPage';

const NAV_COLLAPSED_KEY = 'sahay_nav_collapsed';

/**
 * The application frame.
 *
 * Navigation is two-level and every entry goes somewhere real: directory
 * children apply an actual API filter, page children scroll to a section that
 * exists on that page.
 *
 * Three states, because one size does not fit every screen:
 *
 *   wide    - full sidebar, collapsible to an icon rail so a 1366 laptop can
 *             reclaim ~180px of horizontal space
 *   narrow  - the sidebar becomes an off-canvas drawer behind the menu button
 *
 * The menu button is only rendered when it does something. An earlier version
 * showed it at every width, but the drawer CSS only applied below 900px, so on
 * a desktop it was a visible control that did nothing when pressed.
 */
export function AppShell() {
  const { user, logout, isStaff, isInstitution } = useAuth();
  const toast = useToast();
  // An HOD lands on the institution; a mentor lands on their worklist. The
  // roles ask different questions and the first screen should answer the one
  // they actually have.
  const [view, setView] = useState(() => (isInstitution ? 'institution' : 'worklist'));
  const [selectedRoll, setSelectedRoll] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [studentsFilter, setStudentsFilter] = useState('all');
  const [expanded, setExpanded] = useState(() => new Set(['students']));
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(NAV_COLLAPSED_KEY) === '1',
  );
  // 'add' | 'remove'                    institution: create / delete a student
  // 'caseload-add' | 'caseload-drop'     mentor: assign / unassign
  const [dialog, setDialog] = useState(null);
  // The mentor's own caseload size, shown in the sidebar. Institution roles do
  // not have one, so it stays null and nothing is rendered.
  const [caseload, setCaseload] = useState(null);
  // HOD drilling into one mentor from the institution view. Null for a mentor,
  // whose scope the server pins regardless of what this component asks for.
  const [mentorScope, setMentorScope] = useState(null);
  // Bumped whenever the roster changes. It is part of every page's key, so the
  // open page remounts and refetches instead of showing a student count that
  // no longer matches the database.
  const [dataVersion, setDataVersion] = useState(0);

  const nav = useMemo(() => NAV
    .filter((n) => (!n.staffOnly || isStaff) && (!n.institutionOnly || isInstitution))
    .map((n) => (n.id === 'students'
      ? { ...n, hint: isInstitution ? 'Every student on the roll' : 'My students' }
      : n)),
  [isStaff, isInstitution]);

  useEffect(() => {
    localStorage.setItem(NAV_COLLAPSED_KEY, collapsed ? '1' : '0');
  }, [collapsed]);

  // Refetched on dataVersion so an add or a drop updates the count without a
  // reload. Institution roles are skipped rather than shown a count of zero.
  useEffect(() => {
    if (isInstitution) { setCaseload(null); return undefined; }
    const ac = new AbortController();
    api.caseload.mine({ signal: ac.signal })
      .then(setCaseload)
      .catch(() => { /* the sidebar badge is not worth an error state */ });
    return () => ac.abort();
  }, [isInstitution, dataVersion]);

  /** Scroll to a section on the page that is already open. */
  const scrollToSection = useCallback((sectionId) => {
    // Two frames: one for the view swap to commit, one for layout.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const el = document.getElementById(sectionId);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        el.focus?.({ preventScroll: true });
      }
    }));
  }, []);

  const goto = useCallback((item, parent) => {
    setDrawerOpen(false);
    setSelectedRoll(null);
    setMentorScope(null);

    if (parent?.id === 'students' || item.id === 'students') {
      setStudentsFilter(item.filter || 'all');
      setView('students');
      return;
    }
    const targetView = parent ? parent.id : item.id;
    setView(targetView);
    if (item.section) scrollToSection(item.section);
  }, [scrollToSection]);

  /** Drill down from a metric or chart into the matching student list. */
  const showStudents = useCallback((filter = 'all') => {
    setStudentsFilter(filter);
    setMentorScope(null);
    setView('students');
    setSelectedRoll(null);
    setDrawerOpen(false);
    setExpanded((e) => new Set(e).add('students'));
  }, []);

  /** HOD: from the mentor table into that mentor's caseload. */
  const showMentorCaseload = useCallback((mentorId, mentorName) => {
    setMentorScope({ id: mentorId, name: mentorName });
    setStudentsFilter('all');
    setView('students');
    setSelectedRoll(null);
    setDrawerOpen(false);
  }, []);

  const openStudent = useCallback((rollNo) => {
    setSelectedRoll(rollNo);
    setDrawerOpen(false);
  }, []);

  const toggleExpanded = (id) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  // Close the drawer if the viewport grows past the breakpoint while it is open.
  useEffect(() => {
    if (!drawerOpen) return undefined;
    const mq = window.matchMedia('(min-width: 1000px)');
    const onChange = (e) => { if (e.matches) setDrawerOpen(false); };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [drawerOpen]);

  // Escape closes the drawer, which is what every other overlay here does.
  useEffect(() => {
    if (!drawerOpen) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setDrawerOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawerOpen]);

  const current = nav.find((n) => n.id === view);
  // What this person is responsible for, in words.
  //
  // "Your sections" was wrong for a mentor and always had been: a caseload is
  // a pastoral allocation, not a timetable slice, and these 27 students span
  // eleven departments. Saying how many departments is the honest summary.
  const scope = isInstitution
    ? 'Whole institution'
    : caseload && caseload.count
      ? `${Object.keys(caseload.departments || {}).length} departments`
      : 'Your caseload';

  return (
    <div className={cx('shell', collapsed && 'shell--rail')}>
      <a className="skip-link" href="#main">Skip to main content</a>

      <aside className={cx('sidebar', drawerOpen && 'is-open')}
        aria-label="Sections">
        <div className="sidebar__brand">
          <span className="sidebar__mark" aria-hidden="true">S</span>
          <span className="sidebar__brand-text">
            <strong>Sahay</strong>
            <small>Early warning &amp; support</small>
          </span>
          <IconButton
            label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
            className="sidebar__collapse"
            onClick={() => setCollapsed((c) => !c)}
            icon={<IconMenu width={16} height={16} />}
          />
        </div>

        {/* The mentor's scope, stated as a number. "My students: 27" is the
            answer to "what am I responsible for", and it is the caseload the
            server returns rather than a count of what happens to be on
            screen. */}
        {caseload && caseload.count !== null && (
          <div className="sidebar__scope">
            <span className="sidebar__scope-label">My students</span>
            <Badge tone="info">{caseload.count}</Badge>
          </div>
        )}

        <nav className="sidebar__nav" aria-label="Main">
          {nav.map((item) => {
            const Icon = NAV_ICONS[item.icon];
            const isCurrent = view === item.id && !selectedRoll;
            const isOpen = expanded.has(item.id) && !collapsed;
            const hasChildren = Array.isArray(item.children) && item.children.length > 0;

            return (
              <div className="nav-group" key={item.id}>
                <div className={cx('nav-item', isCurrent && 'is-active')}>
                  <button
                    type="button"
                    className="nav-item__main"
                    aria-current={isCurrent ? 'page' : undefined}
                    title={collapsed ? `${item.label} — ${item.hint}` : item.hint}
                    onClick={() => goto(item)}
                  >
                    {Icon && <Icon />}
                    <span className="nav-item__label">{item.label}</span>
                  </button>
                  {hasChildren && !collapsed && (
                    <button
                      type="button"
                      className={cx('nav-item__caret', isOpen && 'is-open')}
                      aria-expanded={isOpen}
                      aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${item.label}`}
                      onClick={() => toggleExpanded(item.id)}
                    >
                      <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
                        <path d="M4 3l4 3-4 3" fill="none" stroke="currentColor"
                          strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                  )}
                </div>

                {hasChildren && isOpen && (
                  <ul className="nav-sub">
                    {item.children.map((child) => {
                      const active = item.id === 'students'
                        ? view === 'students' && studentsFilter === child.filter && !selectedRoll
                        : false;
                      return (
                        <li key={child.id}>
                          <button
                            type="button"
                            className={cx('nav-sub__item', active && 'is-active')}
                            aria-current={active ? 'page' : undefined}
                            onClick={() => goto(child, item)}
                          >
                            {child.label}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
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

      {drawerOpen && (
        <button type="button" className="scrim" aria-label="Close navigation"
          onClick={() => setDrawerOpen(false)} />
      )}

      <div className="shell__main">
        <header className="topbar">
          <IconButton label="Open navigation" className="topbar__menu"
            onClick={() => setDrawerOpen(true)} icon={<IconMenu />} />
          <div className="topbar__title">
            <h1>{selectedRoll ? 'Student record' : (current?.label || 'Sahay')}</h1>
            <p>
              {selectedRoll ? selectedRoll
                : mentorScope ? `${mentorScope.name}’s caseload`
                  : (current?.hint || '')}
            </p>
          </div>

          {/* Staff only, because both routes sit behind require_mentor -- for
              anyone else these would be buttons that return 403. */}
          {/* Different verbs for different authority, and the wording is the
              safeguard. A mentor adds a student TO THEIR CASELOAD and removes
              them FROM IT; the institution admits a new student and can erase
              one. Offering a mentor a button labelled "Remove student" that
              called DELETE is how "off my list" becomes "gone from the
              institution", so the two pairs are kept apart. The backend
              enforces this regardless -- DELETE is require_institution. */}
          {isStaff && (
            <div className="topbar__actions">
              {isInstitution ? (
                <>
                  <Button variant="secondary" onClick={() => setDialog('add')}
                    aria-label="Admit a new student" title="Admit a new student"
                    icon={<IconPlus width={15} height={15} />}>
                    <span className="btn__label">Admit student</span>
                  </Button>
                  <Button variant="ghost" onClick={() => setDialog('remove')}
                    aria-label="Delete a student from the institution"
                    title="Delete a student from the institution"
                    icon={<IconTrash width={15} height={15} />}>
                    <span className="btn__label">Delete student</span>
                  </Button>
                </>
              ) : (
                <>
                  <Button variant="secondary" onClick={() => setDialog('caseload-add')}
                    aria-label="Add a student to my caseload"
                    title="Add a student to my caseload"
                    icon={<IconPlus width={15} height={15} />}>
                    <span className="btn__label">Add student</span>
                  </Button>
                  <Button variant="ghost" onClick={() => setDialog('caseload-drop')}
                    aria-label="Remove a student from my caseload"
                    title="Remove a student from my caseload — this does not delete them"
                    icon={<IconTrash width={15} height={15} />}>
                    <span className="btn__label">Drop student</span>
                  </Button>
                </>
              )}
            </div>
          )}
        </header>

        <main id="main" className="content" tabIndex={-1}>
          {selectedRoll ? (
            <StudentDetailPage
              key={`detail-${selectedRoll}-${dataVersion}`}
              rollNo={selectedRoll}
              onBack={() => setSelectedRoll(null)}
            />
          ) : (
            <>
              {view === 'institution' && isInstitution && (
                <InstitutionPage key={`institution-${dataVersion}`}
                  onDrillDown={showStudents}
                  onSelectMentor={showMentorCaseload} />
              )}
              {view === 'worklist' && (
                <WorklistPage key={`worklist-${dataVersion}`}
                  onSelectStudent={openStudent} onDrillDown={showStudents} />
              )}
              {view === 'students' && (
                <StudentsPage
                  key={`students-${studentsFilter}-${mentorScope?.id || 'self'}-${dataVersion}`}
                  onSelectStudent={openStudent}
                  defaultRisk={studentsFilter}
                  mentorScope={mentorScope?.id}
                  scopeLabel={mentorScope ? `${mentorScope.name}’s caseload`
                    : isInstitution ? 'the institution' : 'your caseload'}
                />
              )}
              {view === 'analytics' && (
                <AnalyticsPage key={`analytics-${dataVersion}`} onDrillDown={showStudents} />
              )}
              {view === 'model' && <ModelPage key={`model-${dataVersion}`} />}
              {view === 'admin' && isStaff && <AdminPage key={`admin-${dataVersion}`} />}
            </>
          )}
        </main>
      </div>

      {dialog === 'add' && (
        <AddStudentModal
          onClose={() => setDialog(null)}
          onAdmitted={(res) => {
            setDialog(null);
            setDataVersion((v) => v + 1);
            // The API's own message explains that no risk score will be shown
            // until enough weeks of data exist, which is the thing a mentor
            // would otherwise report as a bug.
            toast.success(res.message || `${res.name} admitted as ${res.roll_no}.`);
          }}
        />
      )}

      {dialog === 'caseload-add' && (
        <AddToCaseloadModal
          onClose={() => setDialog(null)}
          onAdded={(res) => {
            setDialog(null);
            setDataVersion((v) => v + 1);
            toast.success(`${res.name} added to your caseload.`);
          }}
        />
      )}

      {dialog === 'caseload-drop' && (
        <DropFromCaseloadModal
          onClose={() => setDialog(null)}
          onDropped={(res) => {
            setDialog(null);
            // If their record is open it must close: the mentor is no longer
            // authorised for it, and refetching would give them a 403.
            if (selectedRoll && res.roll_no
                && selectedRoll.toUpperCase() === res.roll_no.toUpperCase()) {
              setSelectedRoll(null);
            }
            setDataVersion((v) => v + 1);
            toast.success(`${res.name} removed from your caseload. `
                          + 'Their record and history are unchanged.');
          }}
        />
      )}

      {dialog === 'remove' && (
        <RemoveStudentModal
          onClose={() => setDialog(null)}
          onRemoved={(res) => {
            setDialog(null);
            // If the removed student's record is open, close it -- otherwise
            // the detail page refetches a roll number that no longer exists
            // and shows "That record could not be found".
            if (selectedRoll && res.roll_no
                && selectedRoll.toUpperCase() === res.roll_no.toUpperCase()) {
              setSelectedRoll(null);
            }
            setDataVersion((v) => v + 1);
            const weeks = res.removed?.attendance ?? 0;
            toast.success(
              `${res.name} removed, with ${weeks} week${weeks === 1 ? '' : 's'} `
              + 'of attendance history. Scores have been recomputed.',
            );
          }}
        />
      )}
    </div>
  );
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
