import { useApi } from '../hooks/useApi';
import api from '../services/api';
import { Badge, Card, HelpTip, Metric, SectionHeader } from '../components/ui/Primitives';
import { AsyncBoundary, EmptyState } from '../components/ui/States';
import { ABSENT, count, decimal, isNum, label, num } from '../lib/format';

/**
 * What the model is, and — just as importantly — what it is not.
 *
 * Every value here comes from the training report the backend ships. The page
 * deliberately leads with the target and the exclusions rather than the
 * accuracy, because the honest framing is the point: this is a ranking aid
 * with a stated horizon, not a dropout oracle.
 */
export function ModelPage() {
  const { data, loading, error, reload } = useApi(
    ({ signal }) => api.model.status({ signal }), [],
  );

  return (
    <div className="page">
      <AsyncBoundary
        loading={loading} error={error} onRetry={reload} skeletonRows={4}
        isEmpty={!data}
      >
        {data && (data.trained ? <TrainedModel info={data} /> : <RulesOnly info={data} />)}
      </AsyncBoundary>
    </div>
  );
}

function RulesOnly({ info }) {
  return (
    <Card className="stack">
      <SectionHeader title="Rules Mode" subtitle={info.mode} />
      <p className="prose">{info.why}</p>
      <p className="prose">
        Every score in the app is currently produced by the transparent rules
        ledger alone. That is a legitimate state, not a degraded one: the score
        remains arithmetic a mentor can verify by hand.
      </p>
    </Card>
  );
}

function TrainedModel({ info }) {
  const task = info;
  const overall = Array.isArray(info.overall) ? info.overall : [];
  const excluded = info.excluded_features;

  return (
    <>
      <Card className="stack">
        <SectionHeader
          title="What the model predicts"
          actions={<Badge tone={info.mode?.includes('stored') ? 'neutral' : 'info'}>
            {info.mode}
          </Badge>}
        />
        <div className="model-claim">
          <div className="model-claim__row">
            <span className="model-claim__key">It predicts</span>
            <span className="model-claim__val">{task.target || ABSENT}</span>
          </div>
          <div className="model-claim__row model-claim__row--negative">
            <span className="model-claim__key">It does not predict</span>
            <span className="model-claim__val">{task.target_is_not || ABSENT}</span>
          </div>
          <div className="model-claim__row">
            <span className="model-claim__key">Horizon</span>
            <span className="model-claim__val">
              {isNum(task.horizon_weeks) ? `${task.horizon_weeks} weeks ahead` : ABSENT}
            </span>
          </div>
          <div className="model-claim__row">
            <span className="model-claim__key">Algorithm</span>
            <span className="model-claim__val">{task.kind || ABSENT}</span>
          </div>
        </div>
        {info.why && <p className="caution">{info.why}</p>}
      </Card>

      <div className="grid-2">
        <Card className="stack">
          <SectionHeader
            title="When each mode is used"
            help="A student is only scored by the model once there is enough history for its features to mean anything."
          />
          <dl className="facts facts--stacked">
            <div className="facts__item">
              <dt className="facts__label">Rules only</dt>
              <dd className="facts__note">
                Used until a student has the minimum weeks of attendance history.
                The score is the transparent ledger alone.
              </dd>
            </div>
            <div className="facts__item">
              <dt className="facts__label">Rules + model</dt>
              <dd className="facts__note">
                Once there is enough history, the model’s estimate is shown
                <em> beside</em> the rules score — never merged into it. Where the
                two disagree, the app says so rather than hiding it.
              </dd>
            </div>
          </dl>
        </Card>

        <Card className="stack">
          <SectionHeader
            title="Deliberately excluded inputs"
            help="These are recorded only so the fairness audit can run. They are never scoring inputs."
          />
          <ExcludedFeatures excluded={excluded} />
        </Card>
      </div>

      <Card className="stack">
        <SectionHeader
          title="How it compares to simpler baselines"
          help={'A model is only worth its complexity if it beats the obvious '
            + 'alternatives. These are those alternatives.'}
        />
        <div className="metric-strip">
          <Metric label="Beats simple baselines"
            value={verdict(info.beats_baselines)}
            tone={info.beats_baselines ? 'success' : 'muted'} />
          <Metric label="Beats the rules ledger"
            value={verdict(info.beats_rules_ledger)}
            tone={info.beats_rules_ledger ? 'success' : 'muted'} />
          <Metric label="Beats “rank by today’s attendance”"
            value={verdict(info.beats_persistence_baseline)}
            tone={info.beats_persistence_baseline ? 'success' : 'muted'}
            hint="The hardest baseline to beat, and the most honest comparison." />
        </div>

        {overall.length > 0 && (
          <div className="table-wrap">
            <table className="table table--dense">
              <caption className="sr-only">Model and baseline scores</caption>
              <thead>
                <tr>
                  <th scope="col">Approach</th>
                  <th scope="col" className="is-right">n</th>
                  <th scope="col" className="is-right">
                    ROC-AUC<HelpTip text="Ranking quality overall. 0.5 is a coin flip." />
                  </th>
                  <th scope="col" className="is-right">
                    PR-AUC<HelpTip text="Better measure when the positive class is rare, as here." />
                  </th>
                </tr>
              </thead>
              <tbody>
                {overall.map((r) => (
                  <tr key={r.model}>
                    <th scope="row">{r.model}</th>
                    <td className="is-right">{count(r.n)}</td>
                    <td className="is-right">{decimal(r.roc_auc, 3)}</td>
                    <td className="is-right">{decimal(r.pr_auc, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="caution">
          These figures come from the shipped training report and are computed on a
          held-out split. They describe ranking quality on the data the model was
          trained against — they are not a guarantee about any individual student.
        </p>
      </Card>

      {info.data && <DataCoverage data={info.data} />}
    </>
  );
}

function verdict(v) {
  if (v === true) return 'Yes';
  if (v === false) return 'No';
  return ABSENT;
}

function ExcludedFeatures({ excluded }) {
  if (!excluded) return <EmptyState title="Not reported" />;

  // The report has carried this both as {feature: reason} and as a plain list,
  // so both are handled rather than assuming one.
  const entries = Array.isArray(excluded)
    ? excluded.map((f) => [f, null])
    : Object.entries(excluded);

  if (entries.length === 0) return <EmptyState title="Nothing excluded" />;

  return (
    <ul className="excluded">
      {entries.map(([feature, reason]) => (
        <li key={feature}>
          <strong>{label(feature)}</strong>
          {reason && <span className="excluded__reason">{reason}</span>}
        </li>
      ))}
    </ul>
  );
}

function DataCoverage({ data }) {
  const entries = Object.entries(data).filter(([, v]) =>
    typeof v === 'number' || typeof v === 'string');
  if (entries.length === 0) return null;
  return (
    <Card className="stack">
      <SectionHeader title="Training data coverage" />
      <dl className="facts">
        {entries.map(([k, v]) => (
          <div className="facts__item" key={k}>
            <dt className="facts__label">{label(k)}</dt>
            <dd className="facts__value">
              {typeof v === 'number' ? num(v, Number.isInteger(v) ? 0 : 3) : v}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
