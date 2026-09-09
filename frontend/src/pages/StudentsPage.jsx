import { useMemo, useState } from 'react';
import { useApi, useDebounced } from '../hooks/useApi';
import api from '../services/api';
import { Card, FilterBar, SearchInput, SectionHeader, Select } from '../components/ui/Primitives';
import { AsyncBoundary, EmptyState } from '../components/ui/States';
import { DataTable, Pagination } from '../components/ui/DataTable';
import { RiskBadge } from '../components/risk/RiskDisplay';
import { PAGE_SIZE, RISK_FILTERS } from '../lib/constants';
import { count, date, label } from '../lib/format';

/**
 * The student directory, scoped by the server.
 *
 * Server-paginated. The API caps page_size at 200 and returns
 * {total, page, page_size, pages}, so this asks for one page at a time rather
 * than pulling the whole institution and slicing it in the browser -- at
 * 5,000+ students that would be several megabytes per view.
 *
 * `mentorScope` is NOT how a mentor gets scoped. The API pins that to the
 * session, so this component could ask for anything and still receive only the
 * caller's caseload. It exists for the HOD drilling into one mentor from the
 * institution view, which the backend permits for institution roles and
 * refuses with 403 for everyone else. It used to send `mentor = user.id` for
 * mentors, which read as though the browser were choosing the scope; it never
 * should have been, and now it is not.
 *
 * `scopeLabel` is passed in rather than derived here, because the shell already
 * knows the role and this component has no other reason to read the session.
 *
 * A note on columns: /api/students deliberately returns only identity, status
 * and the current score/band. Attendance and CGPA are NOT in this payload
 * (they live on /analytics/roster), so they are not shown here rather than
 * being faked or fetched per row.
 */
export function StudentsPage({ onSelectStudent, defaultRisk = 'all',
                               mentorScope, scopeLabel = 'this list' }) {
  const mentor = mentorScope || undefined;

  const [search, setSearch] = useState('');
  const [risk, setRisk] = useState(defaultRisk);
  const [page, setPage] = useState(1);

  // Debounced so typing does not fire a request per keystroke.
  const q = useDebounced(search, 300);

  const { data, loading, error, reload } = useApi(
    ({ signal }) => api.dashboard.students({
      mentor, q, risk, page, pageSize: PAGE_SIZE, signal,
    }),
    [mentor, q, risk, page],
  );

  const students = data?.students || [];

  const columns = useMemo(() => [
    {
      key: 'name',
      header: 'Student',
      render: (s) => (
        <div className="cell-identity">
          <span className="cell-identity__name">{s.name}</span>
          <span className="cell-identity__id">{s.roll_no}</span>
        </div>
      ),
    },
    {
      key: 'cohort',
      header: 'Cohort',
      hideOnMobile: true,
      render: (s) => (
        <span className="cell-muted">
          {s.dept} {s.year}{s.section ? `-${s.section}` : ''}
        </span>
      ),
    },
    {
      key: 'risk',
      header: 'Risk',
      render: (s) => (
        s.band
          ? <RiskBadge band={s.band} score={s.score} size="sm" />
          : <span className="cell-absent" title="Not enough history to score yet">
              Not scored
            </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      hideOnMobile: true,
      render: (s) => <span className="cell-muted">{label(s.status)}</span>,
    },
    {
      key: 'admitted',
      header: 'Admitted',
      align: 'right',
      hideOnMobile: true,
      render: (s) => <span className="cell-muted">{date(s.admitted_on)}</span>,
    },
  ], []);

  const onFilterChange = (next) => { setRisk(next); setPage(1); };
  const onSearchChange = (next) => { setSearch(next); setPage(1); };

  return (
    <div className="page">
      <Card className="stack">
        <SectionHeader
          title="Students"
          subtitle={data ? `${count(data.total)} in ${scopeLabel}` : 'Directory'}
        />

        <FilterBar>
          <SearchInput
            value={search}
            onChange={onSearchChange}
            placeholder="Search by name or roll number…"
            label="Search students"
          />
          <Select
            id="risk-filter"
            label="Show"
            value={risk}
            onChange={onFilterChange}
            options={RISK_FILTERS}
          />
        </FilterBar>

        <AsyncBoundary
          loading={loading}
          error={error}
          onRetry={reload}
          skeletonRows={8}
          isEmpty={students.length === 0}
          empty={(
            <EmptyState
              title={q ? `No students match “${q}”` : 'No students to show'}
              description={
                q ? 'Try a different name or roll number.'
                  : risk === 'at_risk'
                    ? 'Nobody in scope is currently in the Medium or High band.'
                    : 'The directory is empty for this scope.'
              }
            />
          )}
        >
          <DataTable
            caption="Student directory"
            columns={columns}
            rows={students}
            getRowKey={(s) => s.roll_no}
            onRowActivate={(s) => onSelectStudent(s.roll_no)}
            footer={(
              <Pagination
                page={data?.page || 1}
                pages={data?.pages || 1}
                total={data?.total || 0}
                pageSize={data?.page_size || PAGE_SIZE}
                onPage={setPage}
                label="students"
              />
            )}
          />
        </AsyncBoundary>
      </Card>
    </div>
  );
}
