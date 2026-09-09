import { useEffect, useMemo, useState } from 'react';
import { useApi } from '../../hooks/useApi';
import api from '../../services/api';
import { Button, Card, SectionHeader } from '../../components/ui/Primitives';
import { AsyncBoundary, ErrorState } from '../../components/ui/States';
import { RiskBadge } from '../../components/risk/RiskDisplay';
import { ABSENT, isNum, num, pct, signed } from '../../lib/format';

/**
 * What-if.
 *
 * Two things are shown and kept visibly distinct:
 *
 *  - the rule score, which is exact arithmetic and recomputed by the same code
 *    that produced the original number;
 *  - the model estimate, which is not exact and is labelled as an estimate.
 *
 * The backend says so itself (`exact: true` plus a `caveat` string), and that
 * distinction is preserved here rather than blurred into one confident-looking
 * figure.
 *
 * The lever definitions, their ranges and the student's current values all
 * come from the API's `levers` block. Nothing about the controls is hardcoded.
 */
export function WhatIfPanel({ rollNo }) {
  const { data: base, loading, error, reload } = useApi(
    ({ signal }) => api.student.levers(rollNo, { signal }),
    [rollNo],
  );

  const [draft, setDraft] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState(null);

  // Seed the controls from the API's `current` values once they arrive.
  useEffect(() => {
    if (base?.levers?.current) setDraft({ ...base.levers.current });
    setResult(null);
  }, [base]);

  const fields = base?.levers?.fields;
  const current = base?.levers?.current;

  const dirty = useMemo(() => {
    if (!draft || !current) return false;
    return Object.keys(draft).some((k) => String(draft[k]) !== String(current[k]));
  }, [draft, current]);

  const run = async () => {
    if (!draft || !current) return;
    // Send only what actually changed; the API treats absent keys as unchanged.
    const changes = {};
    Object.keys(draft).forEach((k) => {
      if (String(draft[k]) !== String(current[k])) changes[k] = draft[k];
    });
    if (Object.keys(changes).length === 0) return;
    setBusy(true); setRunError(null);
    try {
      setResult(await api.student.whatIf(rollNo, changes));
    } catch (e) {
      setRunError(e);
    } finally {
      setBusy(false);
    }
  };

  const reset = () => { setDraft({ ...current }); setResult(null); setRunError(null); };

  return (
    <Card className="stack">
      <SectionHeader
        title="What would help?"
        help={'Move a lever to see exactly how the score changes. The rule score is '
          + 'recomputed with the same arithmetic as the real one, so the answer is '
          + 'exact rather than an approximation.'}
      />

      <AsyncBoundary loading={loading} error={error} onRetry={reload} skeletonRows={3}>
        {fields && draft && (
          <>
            <div className="levers">
              {Object.entries(fields).map(([key, f]) => (
                <Lever
                  key={key}
                  name={key}
                  field={f}
                  value={draft[key]}
                  onChange={(v) => setDraft((d) => ({ ...d, [key]: v }))}
                />
              ))}
            </div>

            <div className="levers__actions">
              <Button variant="primary" onClick={run} busy={busy} disabled={!dirty}>
                {dirty ? 'Recalculate' : 'Move a lever to simulate'}
              </Button>
              {dirty && (
                <Button variant="ghost" onClick={reset} disabled={busy}>Reset</Button>
              )}
            </div>

            {runError && <ErrorState error={runError} compact />}
            {result && <WhatIfResult result={result} />}
            {!result && base?.to_reach_low?.statement && (
              <p className="whatif__target">{base.to_reach_low.statement}</p>
            )}
          </>
        )}
      </AsyncBoundary>
    </Card>
  );
}

function Lever({ name, field, value, onChange }) {
  const id = `lever-${name}`;

  if (Array.isArray(field.options)) {
    return (
      <div className="lever">
        <label className="lever__label" htmlFor={id}>{field.label}</label>
        <select id={id} className="field__input" value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}>
          {field.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    );
  }

  const min = isNum(field.min) ? field.min : 0;
  const max = isNum(field.max) ? field.max : 100;
  const step = isNum(field.step) ? field.step : 1;

  return (
    <div className="lever">
      <label className="lever__label" htmlFor={id}>
        {field.label}
        <output className="lever__value" htmlFor={id}>
          {isNum(value) ? num(value) : ABSENT}{field.unit === '%' ? '%' : ''}
        </output>
      </label>
      <input
        id={id}
        type="range"
        className="lever__range"
        min={min}
        max={max}
        step={step}
        value={isNum(value) ? value : min}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <div className="lever__scale" aria-hidden="true">
        <span>{num(min)}</span><span>{num(max)}</span>
      </div>
    </div>
  );
}

function WhatIfResult({ result }) {
  const improved = isNum(result.score_change) && result.score_change < 0;
  return (
    <div className="whatif-result">
      <div className="whatif-result__scores">
        <div>
          <span className="whatif-result__cap">Now</span>
          <RiskBadge band={result.before?.band} score={result.before?.score} size="sm" />
        </div>
        <span className="whatif-result__arrow" aria-hidden="true">→</span>
        <div>
          <span className="whatif-result__cap">Simulated</span>
          <RiskBadge band={result.after?.band} score={result.after?.score} size="sm" />
        </div>
        <span className={`whatif-result__change ${improved ? 'is-good' : 'is-bad'}`}>
          {signed(result.score_change)} points
        </span>
      </div>

      {result.statement && <p className="whatif-result__statement">{result.statement}</p>}

      {Array.isArray(result.lines) && result.lines.length > 0 && (
        <table className="whatif-table">
          <caption className="sr-only">Which rules changed</caption>
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col" className="is-right">Was</th>
              <th scope="col" className="is-right">Now</th>
              <th scope="col" className="is-right">Change</th>
            </tr>
          </thead>
          <tbody>
            {result.lines.map((l) => (
              <tr key={l.key}>
                <th scope="row">
                  {l.label}
                  {l.reason && <span className="whatif-table__reason">{l.reason}</span>}
                </th>
                <td className="is-right">{num(l.was)}</td>
                <td className="is-right">{num(l.now)}</td>
                <td className={`is-right ${l.change < 0 ? 'is-good' : l.change > 0 ? 'is-bad' : ''}`}>
                  {signed(l.change)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="whatif-result__exactness">
        {result.exact
          ? 'This is exact arithmetic, not an estimate — the same rules recomputed on different numbers.'
          : (result.caveat || 'This is an estimate.')}
      </p>

      {result.model && (
        <div className="whatif-result__model">
          <strong>Model estimate also moves</strong>
          <p>
            {pct(result.model.before_percent)} → {pct(result.model.after_percent)}
          </p>
          <p className="whatif-result__caveat">{result.model.note}</p>
        </div>
      )}
    </div>
  );
}
