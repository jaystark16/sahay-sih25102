import { useAuth } from '../auth/AuthContext';
import { useApi } from '../hooks/useApi';
import api from '../services/api';
import { Badge, Card, Metric, SectionHeader } from '../components/ui/Primitives';
import { AsyncBoundary, EmptyState, ErrorState, LoadingState } from '../components/ui/States';
import { DeltaChip, RiskBadge, StageChip } from '../components/risk/RiskDisplay';
import { IconAlert, IconCheck } from '../components/ui/Icons';
import { ABSENT, count, isNum, label, pct } from '../lib/format';

/**
 * The mentor's worklist -- the page that answers "what needs attention now?".
 *
 * Two requests, not more: the worklist itself and the summary strip. Both are
 * scoped to the signed-in mentor when they hold that role, which is how the
 * backend expects to be asked.
 */
export function WorklistPage({ onSelectStudent }) {
  const { user } = useAuth();
  const mentor = user?.role === 'mentor' ? user.id : undefined;

  const worklist = useApi(
    ({ signal }) => api.dashboard.worklist({ mentor, signal }),
    [mentor],
  );
  const summary = useApi(
    ({ signal }) => api.dashboard.summary(mentor, { signal }),
    [mentor],
  );

  const data = worklist.data;
  const thisWeek = data?.this_week || [];

  return (
    <div className="page">
      <SummaryStrip summary={summary} worklist={data} />

      <Card className="stack">
        <SectionHeader
          title="Priority today"
          subtitle={
            isNum(data?.capacity)
              ? `The ${data.capacity} students most worth your time, ranked by how `
                + 'much their risk has moved rather than how high it is.'
              : 'Ranked by how much risk has moved, not just how high it is.'
          }
          actions={
            isNum(data?.total_flagged) && (
              <span className="muted-note">
                {count(data.total_flagged)} flagged in total
              </span>
            )
          }
        />

        <AsyncBoundary
          loading={worklist.loading}
          error={worklist.error}
          onRetry={worklist.reload}
          skeletonRows={5}
          isEmpty={thisWeek.length === 0}
          empty={(
            <EmptyState
              tone="success"
              icon={<IconCheck width={28} height={28} />}
              title="No students need attention this week"
              description={
                isNum(data?.students_in_scope)
                  ? `All ${count(data.students_in_scope)} students in your scope are `
                    + 'either steady or improving.'
                  : 'Nothing is currently flagged in your scope.'
              }
            />
          )}
        >
          <ol className="priority-list">
            {thisWeek.map((s, i) => (
              <PriorityRow
                key={s.roll_no}
                rank={i + 1}
                student={s}
                onSelect={() => onSelectStudent(s.roll_no)}
              />
            ))}
          </ol>
        </AsyncBoundary>
      </Card>

      <div className="grid-2">
        <CohortAlerts alerts={data?.cohort_alerts} loading={worklist.loading} />
        <OnboardingQueue mentor={mentor} />
      </div>

      <WatchList
        rows={data?.watch}
        page={data?.watch_page}
        loading={worklist.loading}
        onSelectStudent={onSelectStudent}
      />
    </div>
  );
}

/* ------------------------------------------------------- summary strip --- */
function SummaryStrip({ summary, worklist }) {
  if (summary.loading) return <LoadingState label="Loading summary…" />;
  if (summary.error) {
    return <ErrorState error={summary.error} onRetry={summary.reload} compact />;
  }
  const s = summary.data;
  if (!s) return null;

  const atRisk = isNum(s.bands?.Medium) && isNum(s.bands?.High)
    ? s.bands.Medium + s.bands.High
    : null;

  return (
    <div className="metric-strip">
      <Metric
        label="Students in scope"
        value={count(s.total_students)}
        sub={isNum(s.scored) ? `${count(s.scored)} scored` : undefined}
      />
      <Metric
        label="At risk"
        tone="danger"
        value={atRisk === null ? ABSENT : count(atRisk)}
        sub={isNum(s.bands?.High) ? `${count(s.bands.High)} high` : undefined}
        hint="Students currently in the Medium or High band."
      />
      <Metric
        label="Risk rising"
        tone="warning"
        value={count(s.rising)}
        hint="Students whose score has increased since the last comparison point.
              This is the signal the worklist ranks on."
      />
      <Metric
        label="Open interventions"
        value={count(s.open_interventions)}
      />
      {isNum(s.not_yet_scoreable) && s.not_yet_scoreable > 0 && (
        <Metric
          label="Not yet scoreable"
          tone="muted"
          value={count(s.not_yet_scoreable)}
          hint="Recently admitted students without enough history to score yet.
                They are shown deliberately rather than hidden."
        />
      )}
      {isNum(worklist?.routed_to_cohort_count) && worklist.routed_to_cohort_count > 0 && (
        <Metric
          label="Routed to cohort"
          tone="muted"
          value={count(worklist.routed_to_cohort_count)}
          hint="Students whose drop is explained by a section-wide pattern, so they
                are handled as one cohort issue instead of many individual cases."
        />
      )}
    </div>
  );
}

/* --------------------------------------------------------- priority row -- */
function PriorityRow({ rank, student: s, onSelect }) {
  return (
    <li className="priority-row">
      <button type="button" className="priority-row__btn" onClick={onSelect}>
        <span className="priority-row__rank" aria-hidden="true">{rank}</span>

        <span className="priority-row__identity">
          <span className="priority-row__name">{s.name}</span>
          <span className="priority-row__meta">
            {s.roll_no} · {s.dept} {s.year}
            {s.section ? `-${s.section}` : ''}
          </span>
        </span>

        <span className="priority-row__risk">
          <RiskBadge band={s.band} score={s.score} />
          <DeltaChip delta={s.delta} />
        </span>

        <span className="priority-row__why">
          {s.headline || 'No headline available'}
          {s.primary_driver && (
            <span className="priority-row__driver">
              Main driver: {label(s.primary_driver)}
            </span>
          )}
        </span>

        <span className="priority-row__flags">
          {s.anomaly && <Badge tone="info">Unusual</Badge>}
          {s.explained_by_cohort && <Badge tone="neutral">Cohort</Badge>}
          {Array.isArray(s.guardrails) && s.guardrails.length > 0 && (
            <Badge tone="neutral">Guardrail</Badge>
          )}
          {isNum(s.model_pct) && (
            <span className="priority-row__model" title="Model estimate of further decline">
              model {pct(s.model_pct)}
            </span>
          )}
        </span>
      </button>
    </li>
  );
}

/* -------------------------------------------------------- cohort alerts -- */
function CohortAlerts({ alerts, loading }) {
  return (
    <Card className="stack">
      <SectionHeader
        title="Section alerts"
        help={'When a whole section drops together, the cause is usually a timetable, '
          + 'faculty or hostel issue rather than many individual students. Those '
          + 'students are kept off the individual worklist so they do not crowd it out.'}
      />
      {loading && <LoadingState label="Loading alerts…" rows={2} />}
      {!loading && (!alerts || alerts.length === 0) && (
        <EmptyState title="No section-wide patterns detected"
          description="Every flagged student is currently an individual case." />
      )}
      {!loading && alerts && alerts.length > 0 && (
        <ul className="alert-list">
          {alerts.map((a) => (
            <li key={a.cohort} className="alert-item">
              <div className="alert-item__head">
                <IconAlert />
                <strong>{a.cohort}</strong>
                <Badge tone="warning">
                  {count(a.students_affected)} of {count(a.students_in_cohort)} affected
                </Badge>
              </div>
              <p className="alert-item__msg">{a.message}</p>
              <p className="alert-item__route">
                Attendance {pct(a.mean_prior_pct)} → {pct(a.mean_recent_pct)}
                {a.route_to && <> · Send to the {a.route_to}</>}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* ----------------------------------------------------- onboarding queue -- */
function OnboardingQueue({ mentor }) {
  const { data, loading, error, reload } = useApi(
    ({ signal }) => api.dashboard.onboarding(10, { signal }),
    [mentor],
  );
  const students = data?.students || [];

  return (
    <Card className="stack">
      <SectionHeader
        title="Collecting data"
        help={'Recently admitted students who do not yet have enough history to be '
          + 'scored. They are listed rather than hidden, so nobody is invisible '
          + 'just because the system cannot rank them yet.'}
      />
      <AsyncBoundary
        loading={loading}
        error={error}
        onRetry={reload}
        skeletonRows={2}
        isEmpty={students.length === 0}
        empty={<EmptyState title="Every student has enough history to be scored" />}
      >
        <ul className="onboard-list">
          {students.map((s) => (
            <li key={s.roll_no} className="onboard-item">
              <div>
                <strong>{s.name}</strong>
                <span className="onboard-item__meta">
                  {s.roll_no} · {s.dept} {s.year}{s.section ? `-${s.section}` : ''}
                </span>
              </div>
              <div className="onboard-item__stage">
                <StageChip stage={s.stage} />
                <span className="muted-note">
                  {isNum(s.weeks) ? `${s.weeks} week${s.weeks === 1 ? '' : 's'} of data` : ABSENT}
                  {isNum(s.stage?.weeks_until_next) && s.stage.weeks_until_next > 0 && (
                    <> · {s.stage.weeks_until_next} to go</>
                  )}
                </span>
              </div>
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </Card>
  );
}

/* ------------------------------------------------------------ watchlist -- */
function WatchList({ rows, page, loading, onSelectStudent }) {
  const items = rows || [];
  if (loading) return null;
  if (items.length === 0) return null;

  return (
    <Card className="stack">
      <SectionHeader
        title="Also flagged"
        subtitle={
          page && isNum(page.total)
            ? `${count(page.total)} further students are flagged but fall outside `
              + 'this week’s capacity.'
            : 'Flagged, but below this week’s capacity.'
        }
      />
      <ul className="watch-list">
        {items.slice(0, 12).map((s) => (
          <li key={s.roll_no}>
            <button type="button" className="watch-row"
              onClick={() => onSelectStudent(s.roll_no)}>
              <span className="watch-row__name">{s.name}</span>
              <span className="watch-row__meta">{s.roll_no}</span>
              <RiskBadge band={s.band} score={s.score} size="sm" />
              <DeltaChip delta={s.delta} />
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
