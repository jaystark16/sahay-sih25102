import {
  Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { useApi } from '../hooks/useApi';
import api from '../services/api';
import { Badge, Card, SectionHeader } from '../components/ui/Primitives';
import { AsyncBoundary, EmptyState, ErrorState } from '../components/ui/States';
import {
  ModelOpinion, RiskLedger, ScoreSummary, StageChip,
} from '../components/risk/RiskDisplay';
import { IconArrowLeft } from '../components/ui/Icons';
import { WhatIfPanel } from '../features/student/WhatIfPanel';
import { InterventionPanel } from '../features/student/InterventionPanel';
import { ABSENT, date, decimal, isNum, label, num, pct } from '../lib/format';

/**
 * The student record -- the centre of the product.
 *
 * Ordered to answer the mentor's questions in the order they actually ask
 * them: who is this, how bad is it, why, what does the evidence look like,
 * what should I do, what have we already tried, and what would help.
 */
export function StudentDetailPage({ rollNo, onBack }) {
  const { data, loading, error, reload } = useApi(
    ({ signal }) => api.student.detail(rollNo, { signal }),
    [rollNo],
  );

  // Only the *first* load replaces the page. Once there is data, a reload --
  // which happens after every intervention is recorded -- keeps the existing
  // content on screen. Swapping the whole tree for a skeleton unmounted the
  // what-if panel and made it refetch its levers on every mutation, and it
  // flashed the page for no reason.
  if (loading && !data) {
    return (
      <div className="page">
        <BackLink onBack={onBack} />
        <Card><AsyncBoundary loading skeletonRows={6} /></Card>
      </div>
    );
  }
  if (error && !data) {
    return (
      <div className="page">
        <BackLink onBack={onBack} />
        <Card><ErrorState error={error} onRetry={reload} /></Card>
      </div>
    );
  }
  if (!data) return null;

  const { student: s, ledger, hybrid, stage, delta, attendance, review, forecast } = data;

  return (
    <div className="page">
      <BackLink onBack={onBack} />

      {error && (
        // A refresh failed but we still have the previous data. Say so rather
        // than showing stale numbers as though they were current.
        <ErrorState error={error} onRetry={reload} compact />
      )}
      {loading && (
        <p className="muted-note" role="status" aria-live="polite">Refreshing…</p>
      )}

      <Card className="student-head">
        <div className="student-head__identity">
          <h1 className="student-head__name">{s.name}</h1>
          <p className="student-head__meta">
            {s.roll_no} · {s.dept} Year {s.year}{s.section ? ` · Section ${s.section}` : ''}
            {' · '}{label(s.status)}
          </p>
          <div className="student-head__chips">
            <StageChip stage={stage} />
            {ledger?.scholarship_referral && (
              <Badge tone="info">Scholarship referral suggested</Badge>
            )}
          </div>
        </div>
        <ScoreSummary ledger={ledger} delta={delta} />
      </Card>

      {ledger?.headline && (
        <Card className="headline-card">
          <p className="headline-card__text">{ledger.headline}</p>
        </Card>
      )}

      <div className="grid-2">
        <Card className="stack">
          <SectionHeader
            title="Why this student is flagged"
            help={'Every point in this score comes from a rule you can check by hand. '
              + 'The lines below add up to the total exactly.'}
          />
          <RiskLedger ledger={ledger} />
        </Card>

        <div className="stack">
          <Card className="stack">
            <SectionHeader title="The model’s separate opinion" />
            <ModelOpinion hybrid={hybrid} />
          </Card>
          <Card className="stack">
            <SectionHeader title="Plain-language review" />
            <ReviewBlock review={review} />
          </Card>
        </div>
      </div>

      <Card className="stack">
        <SectionHeader
          title="Attendance history"
          subtitle={forecast?.statement || undefined}
        />
        <AttendanceChart attendance={attendance} forecast={forecast} />
      </Card>

      <Card className="stack">
        <SectionHeader title="Academic context" />
        <AcademicFacts student={s} />
      </Card>

      <div className="grid-2">
        <WhatIfPanel rollNo={rollNo} />
        <InterventionPanel
          rollNo={rollNo}
          interventions={data.interventions}
          suggested={data.suggested_playbook}
          onChanged={reload}
        />
      </div>
    </div>
  );
}

function BackLink({ onBack }) {
  return (
    <button type="button" className="back-link" onClick={onBack}>
      <IconArrowLeft width={16} height={16} />
      Back to worklist
    </button>
  );
}

/* ------------------------------------------------------------- review ---- */
function ReviewBlock({ review }) {
  if (!review) return <EmptyState title="No review available" />;
  return (
    <div className="review">
      {review.summary && <p className="review__summary">{review.summary}</p>}
      {Array.isArray(review.concerns) && review.concerns.length > 0 && (
        <div className="review__group">
          <h4>Concerns</h4>
          <ul>
            {review.concerns.map((c, i) => (
              <li key={i}><strong>{c.area}</strong> {c.text}</li>
            ))}
          </ul>
        </div>
      )}
      {Array.isArray(review.positives) && review.positives.length > 0 && (
        <div className="review__group review__group--positive">
          <h4>Going well</h4>
          <ul>
            {review.positives.map((p, i) => (
              <li key={i}><strong>{p.area}</strong> {p.text}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------- attendance ---- */
function AttendanceChart({ attendance, forecast }) {
  const rows = (attendance || []).filter((a) => isNum(a.pct));
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No attendance recorded yet"
        description="This student has no weekly attendance history, so the trend
                     cannot be shown and attendance rules cannot fire."
      />
    );
  }

  const data = rows.map((a) => ({
    week: `W${a.week_index + 1}`,
    pct: a.pct,
    weekStart: a.week_start,
  }));

  return (
    <div className="chart" style={{ height: 240 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: -12 }}>
          <defs>
            <linearGradient id="attFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.28} />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--separator)" vertical={false} />
          <XAxis dataKey="week" tick={{ fontSize: 11, fill: 'var(--label-tertiary)' }}
            axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis domain={[0, 100]} unit="%" width={46}
            tick={{ fontSize: 11, fill: 'var(--label-tertiary)' }}
            axisLine={false} tickLine={false} />
          <Tooltip
            contentStyle={{
              background: 'var(--bg-elevated)', border: '1px solid var(--separator)',
              borderRadius: 8, fontSize: 12,
            }}
            labelStyle={{ color: 'var(--label-secondary)' }}
            formatter={(v) => [`${num(v, 1)}%`, 'Attendance']}
          />
          {isNum(forecast?.projected_pct) && (
            <ReferenceLine y={forecast.projected_pct}
              stroke="var(--orange)" strokeDasharray="4 4"
              label={{
                value: `projected ${pct(forecast.projected_pct)}`,
                fill: 'var(--orange)', fontSize: 11, position: 'insideTopRight',
              }} />
          )}
          <Area type="monotone" dataKey="pct" stroke="var(--accent)"
            strokeWidth={2} fill="url(#attFill)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ----------------------------------------------------------- academics --- */
/**
 * Facts, each with an explicit absent state. A student with no recorded IA is
 * not a student who scored zero, and the two must not look the same.
 */
function AcademicFacts({ student: s }) {
  const iaMax = isNum(s.ia_max) ? s.ia_max : 30;
  const ia = (v, name) => ({
    label: name,
    value: isNum(v) ? `${num(v)} / ${num(iaMax)}` : ABSENT,
    note: isNum(v) ? pct((v / iaMax) * 100) : 'Not recorded',
  });

  const facts = [
    ia(s.ia1, 'Internal 1'),
    ia(s.ia2, 'Internal 2'),
    ia(s.ia3, 'Internal 3'),
    {
      label: 'CGPA',
      value: decimal(s.cgpa),
      note: isNum(s.cgpa) ? undefined : 'Not recorded',
    },
    {
      label: 'Backlogs',
      value: isNum(s.backlogs) ? num(s.backlogs) : ABSENT,
      note: s.backlogs === 0 ? 'None carried' : undefined,
    },
    {
      label: 'Assignment submission',
      value: pct(s.submission_pct),
      note: isNum(s.submission_pct) ? undefined : 'Not recorded',
    },
    {
      label: 'Fee status',
      value: s.fee_status ? label(s.fee_status) : ABSENT,
      note: 'Never raises risk above Medium on its own',
    },
    {
      label: 'Last contact',
      value: s.last_contact_at ? date(s.last_contact_at) : ABSENT,
      note: s.last_contact_at ? undefined : 'No recorded contact',
    },
  ];

  return (
    <dl className="facts">
      {facts.map((f) => (
        <div className="facts__item" key={f.label}>
          <dt className="facts__label">{f.label}</dt>
          <dd className="facts__value">{f.value}</dd>
          {f.note && <dd className="facts__note">{f.note}</dd>}
        </div>
      ))}
    </dl>
  );
}
