import { useState } from 'react';
import { useApi } from '../hooks/useApi';
import api from '../services/api';
import { Badge, Card, Metric, SectionHeader } from '../components/ui/Primitives';
import { AsyncBoundary, EmptyState } from '../components/ui/States';
import { DataTable } from '../components/ui/DataTable';
import { ABSENT, count, isNum, num } from '../lib/format';

/**
 * The head of department's view.
 *
 * The HOD's question is "how is my institution doing", and the answer is not a
 * 5,000-row list. It is institution → departments → cohorts → mentors, each
 * level aggregated in SQL and each row a way into the next.
 *
 * The mentor's screens are unchanged and still available to the HOD, because
 * scope is the only difference between the roles: a mentor's worklist over the
 * whole institution is exactly what an HOD wants when they drill all the way
 * down.
 */
export function InstitutionPage({ onDrillDown, onSelectMentor }) {
  const { data, loading, error, reload } = useApi(
    ({ signal }) => api.institution.overview({ signal }), [],
  );

  return (
    <div className="page">
      <AsyncBoundary loading={loading} error={error} onRetry={reload} skeletonRows={6}>
        {data && (
          <>
            <InstitutionTotals totals={data.totals} onDrillDown={onDrillDown} />
            <div className="grid-2">
              <Departments rows={data.departments} />
              <Mentors rows={data.mentors} onSelectMentor={onSelectMentor} />
            </div>
            <Cohorts rows={data.cohorts} />
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}

/* ------------------------------------------------------------ totals ------ */
function InstitutionTotals({ totals, onDrillDown }) {
  const t = totals || {};
  const atRisk = (isNum(t.high) && isNum(t.medium)) ? t.high + t.medium : null;
  return (
    <Card className="stack" id="sec-institution" tabIndex={-1}>
      <SectionHeader
        title="The institution"
        subtitle="Every student on the roll, not a mentor's slice of it."
      />
      <div className="metric-strip">
        <Metric label="Students on roll" value={count(t.students)}
          sub={isNum(t.scored) ? `${count(t.scored)} scored` : undefined}
          onClick={onDrillDown ? () => onDrillDown('all') : undefined} />
        <Metric label="At risk" tone="danger"
          value={atRisk === null ? ABSENT : count(atRisk)}
          sub={isNum(t.high) ? `${count(t.high)} high` : undefined}
          onClick={onDrillDown ? () => onDrillDown('at_risk') : undefined} />
        <Metric label="Risk rising" tone="warning" value={count(t.rising)}
          onClick={onDrillDown ? () => onDrillDown('rising') : undefined} />
        <Metric label="No mentor assigned" tone="muted" value={count(t.unassigned)}
          hint="Students nobody is currently responsible for. This is a coverage
                gap, not a data error -- it is here so it can be closed." />
      </div>
    </Card>
  );
}

/* ------------------------------------------------------- departments ------ */
function Departments({ rows }) {
  return (
    <Card className="stack" id="sec-departments" tabIndex={-1}>
      <SectionHeader title="By department"
        subtitle="Where the risk is concentrated." />
      {!rows || rows.length === 0
        ? <EmptyState title="No departments to report" />
        : (
          <DataTable
            columns={[
              { key: 'dept', header: 'Dept', render: (r) => r.dept || ABSENT },
              { key: 'students', header: 'Students', align: 'right',
                render: (r) => count(r.students) },
              { key: 'high', header: 'High', align: 'right',
                render: (r) => (r.high ? <Badge tone="danger">{r.high}</Badge> : ABSENT) },
              { key: 'rising', header: 'Rising', align: 'right',
                render: (r) => count(r.rising) },
              { key: 'avg_score', header: 'Avg score', align: 'right',
                render: (r) => (isNum(r.avg_score) ? num(r.avg_score, 1) : ABSENT) },
              { key: 'assigned', header: 'Mentored', align: 'right',
                render: (r) => `${count(r.assigned)} / ${count(r.students)}` },
            ]}
            rows={rows}
            getRowKey={(r) => r.dept}
          />
        )}
    </Card>
  );
}

/* ----------------------------------------------------------- mentors ------ */
function Mentors({ rows, onSelectMentor }) {
  return (
    <Card className="stack" id="sec-mentors" tabIndex={-1}>
      <SectionHeader
        title="Mentors"
        help="Caseload is who they are responsible for. A caseload spanning
              several departments is normal -- pastoral allocation does not
              follow the timetable."
        subtitle="Ordered by how much high risk each one is carrying."
      />
      {!rows || rows.length === 0
        ? <EmptyState title="No mentors have a caseload yet" />
        : (
          <DataTable
            columns={[
              { key: 'mentor_name', header: 'Mentor',
                render: (r) => r.mentor_name || r.mentor_id },
              { key: 'caseload', header: 'Caseload', align: 'right',
                render: (r) => count(r.caseload) },
              { key: 'high', header: 'High', align: 'right',
                render: (r) => (r.high ? <Badge tone="danger">{r.high}</Badge> : ABSENT) },
              { key: 'rising', header: 'Rising', align: 'right',
                render: (r) => count(r.rising) },
              { key: 'spread', header: 'Spread', align: 'right',
                render: (r) => `${count(r.departments)} depts` },
              { key: 'open_interventions', header: 'Open', align: 'right',
                render: (r) => count(r.open_interventions) },
            ]}
            rows={rows}
            getRowKey={(r) => r.mentor_id}
            onRowActivate={onSelectMentor
              ? (r) => onSelectMentor(r.mentor_id, r.mentor_name) : undefined}
          />
        )}
    </Card>
  );
}

/* ----------------------------------------------------------- cohorts ------ */
function Cohorts({ rows }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? rows : (rows || []).slice(0, 12);
  return (
    <Card className="stack" id="sec-cohorts" tabIndex={-1}>
      <SectionHeader
        title="Cohorts needing attention"
        help="A section-wide problem is a timetable, faculty or hostel problem.
              Reading it here as one row is the point -- it is not thirty
              individual cases."
        subtitle="Department, year and section, worst first."
      />
      {!rows || rows.length === 0
        ? <EmptyState title="No cohort data yet" />
        : (
          <>
            <DataTable
              columns={[
                { key: 'cohort', header: 'Cohort',
                  render: (r) => `${r.dept} ${r.year}-${r.section}` },
                { key: 'students', header: 'Students', align: 'right',
                  render: (r) => count(r.students) },
                { key: 'high', header: 'High', align: 'right',
                  render: (r) => (r.high ? <Badge tone="danger">{r.high}</Badge> : ABSENT) },
                { key: 'rising', header: 'Rising', align: 'right',
                  render: (r) => count(r.rising) },
                { key: 'avg_score', header: 'Avg score', align: 'right',
                  render: (r) => (isNum(r.avg_score) ? num(r.avg_score, 1) : ABSENT) },
              ]}
              rows={shown}
              getRowKey={(r) => `${r.dept}-${r.year}-${r.section}`}
            />
            {rows.length > 12 && (
              <button type="button" className="disclosure"
                onClick={() => setShowAll((v) => !v)}>
                {showAll ? 'Show the worst 12 only'
                  : `Show all ${rows.length} cohorts`}
              </button>
            )}
          </>
        )}
    </Card>
  );
}
