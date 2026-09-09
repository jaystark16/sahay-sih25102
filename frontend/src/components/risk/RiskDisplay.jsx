/**
 * Risk presentation.
 *
 * Sahay's differentiator is that the score is arithmetic a mentor can check by
 * hand, so these components exist to make that visible rather than to decorate
 * it. Every number here comes from the API's ledger; nothing is computed or
 * assumed in the browser beyond laying out what the backend returned.
 *
 * Colour never carries meaning alone -- each badge prints its band name, which
 * is what makes the palette usable for a colourblind mentor.
 */
import { Badge, HelpTip, ProgressBar } from '../ui/Primitives';
import { bandMeta } from '../../lib/constants';
import { ABSENT, isNum, label, num, pct, signed } from '../../lib/format';

/* ---------------------------------------------------------- RiskBadge ---- */
export function RiskBadge({ band, score, size = 'md', showScore = true }) {
  const meta = bandMeta(band);
  const scored = isNum(score);
  return (
    <span className={`risk-badge risk-badge--${meta.tone} risk-badge--${size}`}>
      {showScore && (
        <span className="risk-badge__score">{scored ? num(score) : ABSENT}</span>
      )}
      <span className="risk-badge__band">{meta.text}</span>
    </span>
  );
}

/* ---------------------------------------------------------- DeltaChip ---- */
/**
 * Change in score since the comparison point. The backend calls this `delta`;
 * the user sees "rising"/"falling", because the raw field name means nothing
 * to a mentor and the direction is the point.
 */
export function DeltaChip({ delta, weeks }) {
  if (!isNum(delta)) return <span className="delta delta--none">{ABSENT}</span>;
  const tone = delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat';
  const word = delta > 0 ? 'rising' : delta < 0 ? 'improving' : 'steady';
  const title = isNum(weeks)
    ? `${signed(delta)} points over the last ${weeks} weeks`
    : `${signed(delta)} points since the last comparison`;
  return (
    <span className={`delta delta--${tone}`} title={title}>
      <span aria-hidden="true">{delta > 0 ? '▲' : delta < 0 ? '▼' : '—'}</span>
      {signed(delta)}
      <span className="delta__word">{word}</span>
    </span>
  );
}

/* ---------------------------------------------------------- StageChip ---- */
/**
 * How much history a student has. The API's `stage.scoring` is one of
 * "rules_only" | "hybrid" | "none"; `stage.label` is already human-readable,
 * so it is preferred and the raw value is only a fallback.
 */
export function StageChip({ stage }) {
  if (!stage) return null;
  const text = stage.label || label(stage.stage || stage.scoring);
  const tone = stage.scoring === 'hybrid' ? 'info' : 'neutral';
  return <Badge tone={tone} title={stage.explain}>{text}</Badge>;
}

/* -------------------------------------------------------- ScoreSummary --- */
/**
 * The headline number, its band, and how much of the scale was actually
 * available. `max_available` matters: a student missing several signals is
 * scored out of less than 100, and hiding that would overstate confidence.
 */
export function ScoreSummary({ ledger, delta }) {
  if (!ledger) return null;
  const outOf = isNum(ledger.max_available) ? ledger.max_available : 100;
  return (
    <div className="score-summary">
      <div className="score-summary__figure">
        <span className="score-summary__value">{num(ledger.score)}</span>
        <span className="score-summary__scale">/ {num(outOf)}</span>
      </div>
      <div className="score-summary__meta">
        <RiskBadge band={ledger.band} showScore={false} size="lg" />
        {delta && <DeltaChip delta={delta.delta} weeks={delta.weeks} />}
      </div>
      {ledger.provisional && (
        <p className="score-summary__note">
          Provisional — {ledger.signals_present} of {ledger.signals_total} signals
          available so far.
          {ledger.scale_note ? ` ${ledger.scale_note}` : ''}
        </p>
      )}
      {isNum(ledger.confidence) && (
        <p className="score-summary__confidence">
          Confidence {pct(ledger.confidence * 100)}
          <HelpTip text={'How much of the scoring evidence was available for this '
            + 'student. Lower confidence means fewer signals had data, not that '
            + 'the arithmetic is uncertain.'} />
        </p>
      )}
    </div>
  );
}

/* ---------------------------------------------------------- RiskLedger --- */
/**
 * The score, broken into the lines that produced it.
 *
 * This is the answer to "why this student?". Components that could not fire
 * are shown too, marked unavailable, because a missing signal is information:
 * it tells the mentor the score is based on less than the full picture.
 */
export function RiskLedger({ ledger }) {
  if (!ledger || !Array.isArray(ledger.components)) return null;

  const firing = ledger.components.filter((c) => c.available && isNum(c.points) && c.points > 0);
  const clear = ledger.components.filter((c) => c.available && (c.points === 0));
  const missing = ledger.components.filter((c) => !c.available);
  const total = firing.reduce((s, c) => s + (isNum(c.points) ? c.points : 0), 0);

  return (
    <div className="ledger">
      {firing.length > 0 && (
        <ul className="ledger__list">
          {firing.map((c) => (
            <LedgerRow key={c.key} component={c} />
          ))}
        </ul>
      )}

      {firing.length === 0 && (
        <p className="ledger__none">
          No risk signals are currently firing for this student.
        </p>
      )}

      <div className="ledger__total">
        <span>Total risk points</span>
        <strong>{num(total)}</strong>
      </div>

      {clear.length > 0 && (
        <details className="ledger__more">
          <summary>{clear.length} signal{clear.length === 1 ? '' : 's'} checked and clear</summary>
          <ul className="ledger__list ledger__list--muted">
            {clear.map((c) => <LedgerRow key={c.key} component={c} />)}
          </ul>
        </details>
      )}

      {missing.length > 0 && (
        <details className="ledger__more">
          <summary>{missing.length} signal{missing.length === 1 ? '' : 's'} without data</summary>
          <ul className="ledger__list ledger__list--muted">
            {missing.map((c) => (
              <li className="ledger__row ledger__row--missing" key={c.key}>
                <span className="ledger__label">{c.label || label(c.key)}</span>
                <span className="ledger__reason">{c.reason || 'No data available'}</span>
                <span className="ledger__points ledger__points--absent">{ABSENT}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {Array.isArray(ledger.guardrails) && ledger.guardrails.length > 0 && (
        <div className="ledger__guardrails">
          <h4>Guardrails applied</h4>
          <ul>
            {ledger.guardrails.map((g, i) => (
              <li key={i}>{typeof g === 'string' ? g : (g.message || g.reason)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function LedgerRow({ component: c }) {
  const points = isNum(c.points) ? c.points : null;
  const max = isNum(c.max_points) ? c.max_points : null;
  return (
    <li className="ledger__row">
      <div className="ledger__head">
        <span className="ledger__label">{c.label || label(c.key)}</span>
        <span className={`ledger__points ${points ? 'is-firing' : 'is-clear'}`}>
          {points === null ? ABSENT : `+${num(points)}`}
          {max !== null && <span className="ledger__max"> / {num(max)}</span>}
        </span>
      </div>
      {max !== null && points !== null && (
        <ProgressBar value={points} max={max}
          tone={points > 0 ? 'danger' : 'success'}
          label={`${c.label || c.key}: ${points} of ${max} points`} />
      )}
      {c.reason && <p className="ledger__reason">{c.reason}</p>}
    </li>
  );
}

/* --------------------------------------------------------- ModelOpinion -- */
/**
 * The model's view, shown beside the rules rather than merged into them.
 *
 * The distinction is deliberate and load-bearing: the ledger is exact
 * arithmetic, the model output is an estimate. Presenting them as one number
 * would misrepresent both.
 */
export function ModelOpinion({ hybrid }) {
  if (!hybrid) return null;
  const m = hybrid.model;

  if (!m || !m.available) {
    return (
      <div className="model-opinion model-opinion--unavailable">
        <h4 className="model-opinion__title">Model estimate</h4>
        <p className="model-opinion__note">
          {hybrid.model_note || m?.reason
            || 'Not available for this student yet.'}
        </p>
      </div>
    );
  }

  return (
    <div className="model-opinion">
      <h4 className="model-opinion__title">
        Model estimate
        <HelpTip text={'An estimate from a trained model, not arithmetic. It '
          + 'looks at the shape of the attendance trend, which the threshold '
          + 'rules cannot see.'} />
      </h4>
      <p className="model-opinion__figure">
        {pct(m.percent, 0)}
        {isNum(m.horizon_weeks) && (
          <span className="model-opinion__horizon">
            {' '}risk within {m.horizon_weeks} weeks
          </span>
        )}
      </p>
      {m.statement && <p className="model-opinion__statement">{m.statement}</p>}
      {m.computed === 'stored' && m.note && (
        <p className="model-opinion__note">{m.note}</p>
      )}
      {/* The badge carries the label and the note is a sentence beneath it.
          Concatenated into the badge, the note inherited .badge's
          `white-space: nowrap` and pushed 72px out of a 425px panel -- the
          text was cut off mid-word and the whole page gained a horizontal
          scrollbar. Only visible once predictions were restored, since a
          cohort with no stored anomaly flag never rendered this at all. */}
      {m.anomaly?.unusual && (
        <div className="model-opinion__anomaly">
          <Badge tone="info">Unusual pattern</Badge>
          {m.anomaly.note && (
            <p className="model-opinion__note">{m.anomaly.note}</p>
          )}
        </div>
      )}
      {hybrid.disagreement && (
        <div className="model-opinion__disagreement">
          <strong>Rules and model disagree.</strong>
          <p>{hybrid.disagreement.message}</p>
        </div>
      )}
    </div>
  );
}
