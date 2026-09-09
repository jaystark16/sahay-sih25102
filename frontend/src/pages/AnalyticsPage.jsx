import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { useAuth } from '../auth/AuthContext';
import { useApi } from '../hooks/useApi';
import api from '../services/api';
import { Badge, Card, HelpTip, Metric, SectionHeader } from '../components/ui/Primitives';
import { AsyncBoundary, EmptyState } from '../components/ui/States';
import { BAND_ORDER, bandMeta } from '../lib/constants';
import { ABSENT, count, decimal, isNum, label, num, pct, share } from '../lib/format';

/**
 * Analytics as decision support, not a wall of charts.
 *
 * Each section answers one question: how is the institution distributed, did
 * our actions help, and is the system treating groups differently. Anything
 * the backend cannot yet measure says so rather than rendering an empty axis.
 */
export function AnalyticsPage({ onDrillDown }) {
  const { user } = useAuth();
  const mentor = user?.role === 'mentor' ? user.id : undefined;

  const summary = useApi(({ signal }) => api.dashboard.summary(mentor, { signal }), [mentor]);
  const effectiveness = useApi(({ signal }) => api.analytics.effectiveness({ signal }), []);
  const fairness = useApi(({ signal }) => api.analytics.fairness({ signal }), []);

  return (
    <div className="page">
      <RiskDistribution summary={summary} onDrillDown={onDrillDown} />
      <Effectiveness state={effectiveness} />
      <Fairness state={fairness} />
    </div>
  );
}

/* ------------------------------------------------- risk distribution ----- */
function RiskDistribution({ summary, onDrillDown }) {
  const s = summary.data;
  const bands = s?.bands;
  const total = isNum(s?.scored) ? s.scored : null;

  const data = bands
    ? BAND_ORDER.map((b) => ({
      band: b,
      label: bandMeta(b).text,
      n: isNum(bands[b]) ? bands[b] : 0,
      tone: bandMeta(b).tone,
    }))
    : [];

  return (
    <Card className="stack" id="sec-distribution" tabIndex={-1}>
      <SectionHeader
        title="Institution overview"
        subtitle={s?.data_source ? `Source: ${s.data_source}` : undefined}
      />
      <AsyncBoundary
        loading={summary.loading} error={summary.error} onRetry={summary.reload}
        skeletonRows={2} isEmpty={!s}
      >
        <>
          <div className="metric-strip">
            <Metric label="Total students" value={count(s?.total_students)}
              onClick={onDrillDown ? () => onDrillDown('all') : undefined} />
            <Metric label="Scored" value={count(s?.scored)}
              sub={isNum(s?.total_students) && isNum(s?.scored)
                ? `${pct(share(s.scored, s.total_students), 0)} coverage` : undefined}
              hint="Students with enough history for the rules to produce a score." />
            <Metric label="Not yet scoreable" tone="muted"
              value={count(s?.not_yet_scoreable)}
              hint="Recently admitted. Listed rather than hidden."
              onClick={onDrillDown ? () => onDrillDown('unscored') : undefined} />
            <Metric label="Risk rising" tone="warning" value={count(s?.rising)}
              onClick={onDrillDown ? () => onDrillDown('rising') : undefined} />
            <Metric label="Scoring mode" value={s?.mode || ABSENT}
              hint="Rules only, or rules combined with the trained model." />
          </div>

          <div className="chart" style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: -16 }}>
                <CartesianGrid stroke="var(--separator)" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 12, fill: 'var(--label-secondary)' }}
                  axisLine={false} tickLine={false} />
                <YAxis width={54} allowDecimals={false}
                  tick={{ fontSize: 11, fill: 'var(--label-tertiary)' }}
                  axisLine={false} tickLine={false}
                  label={{ value: 'students', angle: -90, position: 'insideLeft',
                    fill: 'var(--label-tertiary)', fontSize: 11 }} />
                <Tooltip
                  cursor={{ fill: 'var(--fill-primary)' }}
                  contentStyle={{
                    background: 'var(--bg-elevated)', border: '1px solid var(--separator)',
                    borderRadius: 8, fontSize: 12,
                  }}
                  formatter={(v) => [
                    `${count(v)} students${total ? ` (${pct(share(v, total))})` : ''}`,
                    'Count',
                  ]}
                />
                <Bar
                  dataKey="n"
                  radius={[6, 6, 0, 0]}
                  cursor={onDrillDown ? 'pointer' : undefined}
                  onClick={onDrillDown
                    ? (bar) => bar?.payload?.band
                      && onDrillDown(bar.payload.band.toLowerCase())
                    : undefined}
                >
                  {data.map((d) => (
                    <Cell key={d.band} fill={`var(--${toneVar(d.tone)})`} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {onDrillDown && bands && (
            <div className="drill-row">
              <span className="muted-note">Open a band in the directory:</span>
              {BAND_ORDER.map((b) => (
                <button
                  key={b}
                  type="button"
                  className="drill-chip"
                  onClick={() => onDrillDown(b.toLowerCase())}
                >
                  {bandMeta(b).text}
                  <span className="drill-chip__n">{count(bands[b])}</span>
                </button>
              ))}
            </div>
          )}
        </>
      </AsyncBoundary>
    </Card>
  );
}

function toneVar(tone) {
  return tone === 'danger' ? 'red' : tone === 'warning' ? 'orange' : 'green';
}

/* ---------------------------------------------------- effectiveness ------ */
function Effectiveness({ state }) {
  const d = state.data;
  const rows = d?.measured?.rows || [];
  const measuredTotal = d?.measured_total;

  return (
    <Card className="stack" id="sec-effectiveness" tabIndex={-1}>
      <SectionHeader
        title="Did our interventions help?"
        subtitle={d?.measured?.method}
        help={'Measured from attendance records against a matched comparison group, '
          + 'not from what mentors reported. The naive rate is almost always higher '
          + 'than the real effect, which is exactly why the control column exists.'}
      />
      <AsyncBoundary
        loading={state.loading} error={state.error} onRetry={state.reload}
        skeletonRows={4}
        isEmpty={rows.length === 0}
        empty={(
          <EmptyState
            title="Nothing measured yet"
            description="Interventions are measured once enough attendance has been
                         recorded after they were started."
          />
        )}
      >
        <>
          <p className="muted-note">
            {count(measuredTotal)} action{measuredTotal === 1 ? '' : 's'} measured from
            attendance data.
          </p>
          <div className="table-wrap">
            <table className="table table--dense">
              <caption className="sr-only">Intervention effectiveness by playbook</caption>
              <thead>
                <tr>
                  <th scope="col">Playbook</th>
                  <th scope="col" className="is-right">n</th>
                  <th scope="col" className="is-right">
                    Improved
                    <HelpTip text="Share that improved. Includes students who would have improved anyway." />
                  </th>
                  <th scope="col" className="is-right">
                    Vs control
                    <HelpTip text="Change relative to a matched comparison group. This is the honest number." />
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${r.playbook}-${r.trigger}`}>
                    <th scope="row">
                      {r.playbook}
                      {r.trigger && <span className="cell-sub">{label(r.trigger)}</span>}
                    </th>
                    <td className="is-right">{count(r.n)}</td>
                    <td className="is-right">{pct(r.improved_rate, 0)}</td>
                    <td className="is-right">
                      {isNum(r.estimated_effect)
                        ? <strong className={r.estimated_effect > 0 ? 'is-good' : 'is-bad'}>
                            {r.estimated_effect > 0 ? '+' : ''}{num(r.estimated_effect, 1)} pts
                          </strong>
                        : <span className="cell-absent">not enough data</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d?.measured?.caution && (
            <p className="caution">{d.measured.caution}</p>
          )}
        </>
      </AsyncBoundary>
    </Card>
  );
}

/* --------------------------------------------------------- fairness ------ */
function Fairness({ state }) {
  const d = state.data;
  const dims = d?.dimensions || {};
  const names = Object.keys(dims);

  return (
    <Card className="stack" id="sec-fairness" tabIndex={-1}>
      <SectionHeader
        title="Fairness monitoring"
        subtitle={d?.method}
        help={'None of these attributes are scoring inputs. They are recorded only so '
          + 'that this audit can run, and a flagged row means the flag rate for that '
          + 'group differs enough to be worth a human look.'}
      />
      <AsyncBoundary
        loading={state.loading} error={state.error} onRetry={state.reload}
        skeletonRows={3} isEmpty={names.length === 0}
        empty={<EmptyState title="No fairness data available" />}
      >
        <div className="fairness">
          {names.map((dim) => (
            <div className="fairness__dim" key={dim}>
              <h4 className="fairness__title">{label(dim)}</h4>
              <table className="table table--dense">
                <caption className="sr-only">Flag rate by {label(dim)}</caption>
                <thead>
                  <tr>
                    <th scope="col">Group</th>
                    <th scope="col" className="is-right">Students</th>
                    <th scope="col" className="is-right">Flagged</th>
                    <th scope="col" className="is-right">Rate</th>
                    <th scope="col" className="is-right">
                      Ratio
                      <HelpTip text="Flag rate relative to the lowest group. 1.0 is parity." />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {dims[dim].map((g) => (
                    <tr key={g.group} className={g.review ? 'is-review' : undefined}>
                      <th scope="row">
                        {label(g.group)}
                        {g.review && <Badge tone="warning">Review</Badge>}
                      </th>
                      <td className="is-right">{count(g.n)}</td>
                      <td className="is-right">{count(g.flagged)}</td>
                      <td className="is-right">{pct(g.flag_rate, 1)}</td>
                      <td className="is-right">{decimal(g.disparity_ratio, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          {d?.note && <p className="caution">{d.note}</p>}
        </div>
      </AsyncBoundary>
    </Card>
  );
}
