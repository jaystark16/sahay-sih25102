import { useApi } from '../hooks/useApi';
import api from '../services/api';
import { Badge, Card, HelpTip, SectionHeader } from '../components/ui/Primitives';
import { AsyncBoundary, EmptyState } from '../components/ui/States';
import { DataTable } from '../components/ui/DataTable';
import { ABSENT, count, isNum, num } from '../lib/format';

/**
 * Model evaluation: the baseline, the comparison and the model in service.
 *
 * Nothing on this page is computed in the browser and nothing is hardcoded.
 * Every figure comes from `python ml.py --evaluate`, which fits Logistic
 * Regression and Random Forest on the same training rows the deployed XGBoost
 * was fitted on, scores all three on the same held-out students, and writes
 * the result into model_report.json.
 *
 * The one design decision worth stating: accuracy, precision, recall, F1 and
 * the confusion matrix are shown at TWO thresholds, side by side. A ranking
 * model has no confusion matrix until someone picks a cut, and a table that
 * quotes an F1 without saying at which threshold is not telling you much. So
 * both are here: the conventional 0.5, and the top-5% capacity the product
 * actually runs at.
 */

const ROLE_TONE = { baseline: 'info', comparison: 'neutral', primary: 'success',
                    reference: 'neutral' };
const ROLE_LABEL = { baseline: 'Baseline', comparison: 'Comparison',
                     primary: 'Primary — in service', reference: 'Non-ML reference' };

export function ModelEvaluation() {
  const { data, loading, error, reload } = useApi(
    ({ signal }) => api.model.evaluation({ signal }), [],
  );

  return (
    <AsyncBoundary loading={loading} error={error} onRetry={reload} skeletonRows={5}>
      {data && !data.available && (
        <Card className="stack" id="sec-evaluation" tabIndex={-1}>
          <SectionHeader title="Model evaluation" subtitle={data.reason} />
          <EmptyState
            title="No comparison has been run yet"
            description={`Run ${data.hint || 'python ml.py --evaluate'} to produce it.`}
          />
        </Card>
      )}
      {data && data.available && (
        <>
          <Comparison ev={data} />
          <div className="grid-2">
            <ConfusionMatrices ev={data} />
            <Methodology ev={data} />
          </div>
        </>
      )}
    </AsyncBoundary>
  );
}

/* -------------------------------------------------------- comparison ------ */
function Comparison({ ev }) {
  const d = ev.dataset || {};
  const metric = (v, dp = 3) => (isNum(v) ? num(v, dp) : ABSENT);

  return (
    <Card className="stack" id="sec-evaluation" tabIndex={-1}>
      <SectionHeader
        title="Model comparison"
        help="Same rows, same 70/30 split grouped by student, same 17 features,
              same target, same metrics. Anything that could differ between the
              models is held fixed, or the comparison would not mean anything."
        subtitle={`${count(d.test_rows)} held-out rows from ${count(d.test_students)} students `
          + `· ${count(d.test_positives)} positive (${isNum(d.test_base_rate)
            ? `${num(d.test_base_rate * 100, 1)}%` : ABSENT} base rate)`}
      />

      <div className="eval-note">
        <p className="muted-note">
          <strong>Threshold-free</strong> — ROC-AUC and PR-AUC measure how well
          a model <em>ranks</em>; no decision boundary is involved. The Brier
          score measures whether the probabilities are calibrated, and is not
          applicable to the two non-ML references, which emit scores rather
          than probabilities.
        </p>
        <p className="muted-note">
          <strong>Thresholded</strong> — accuracy, precision, recall and F1 need
          a hard yes/no. The columns below use the conventional 0.5 probability
          cut. The final column repeats F1 at the top-5% operating point this
          system actually deploys at, which is the unchanged p@5% methodology.
        </p>
      </div>

      <DataTable
        caption="Model comparison on the held-out test set"
        columns={[
          { key: 'model', header: 'Model',
            render: (r) => (
              <div className="cell-identity">
                <span className="cell-identity__name">{r.model}</span>
                <span className="cell-identity__id">
                  {ROLE_LABEL[r.role] || r.role}
                </span>
              </div>
            ) },
          { key: 'accuracy', header: 'Accuracy', align: 'right',
            render: (r) => metric(r.accuracy) },
          { key: 'precision', header: 'Precision', align: 'right',
            render: (r) => metric(r.precision) },
          { key: 'recall', header: 'Recall', align: 'right',
            render: (r) => metric(r.recall) },
          { key: 'f1', header: 'F1', align: 'right',
            render: (r) => (isNum(r.f1)
              ? <strong className="eval-f1">{num(r.f1, 3)}</strong> : ABSENT) },
          { key: 'roc_auc', header: 'ROC-AUC', align: 'right',
            render: (r) => metric(r.roc_auc) },
          { key: 'pr_auc', header: 'PR-AUC', align: 'right',
            render: (r) => metric(r.pr_auc) },
          { key: 'brier_score', header: 'Brier', align: 'right',
            hideOnMobile: true,
            render: (r) => (isNum(r.brier_score)
              ? num(r.brier_score, 4)
              : <span className="cell-absent" title="Not applicable: this baseline
                  emits a score, not a probability">n/a</span>) },
          { key: 'f1_at_alert_threshold', header: 'F1 @ top-5%', align: 'right',
            hideOnMobile: true,
            render: (r) => metric(r.f1_at_alert_threshold) },
        ]}
        rows={ev.table || []}
        getRowKey={(r) => r.key}
      />

      <div className="eval-facts">
        <span>
          <Badge tone="info">Baseline</Badge> Logistic Regression
        </span>
        <span>
          <Badge tone="neutral">Comparison</Badge> Random Forest
        </span>
        <span>
          <Badge tone="success">Primary</Badge> XGBoost — serves every prediction
        </span>
      </div>

      {/* Stated plainly. The baseline currently ranks best on this target, and
          a comparison table that quietly buried that would be worthless. */}
      <p className="caution">
        On this target the Logistic Regression baseline ranks best
        (PR-AUC {metric(ev.models?.logistic_regression?.pr_auc)} against
        XGBoost&rsquo;s {metric(ev.models?.xgboost?.pr_auc)}), and Random Forest
        is best calibrated. XGBoost remains the model in service and the one
        SHAP explains. The label it is fitted to is close to linear in these
        features — see the target note on this page — which is exactly the
        situation where a linear model is hard to beat, and is the strongest
        argument for changing the target rather than the algorithm.
      </p>

      <dl className="facts">
        <div className="facts__item">
          <dt className="facts__label">Split</dt>
          <dd className="facts__value">{d.split || ABSENT}</dd>
        </div>
        <div className="facts__item">
          <dt className="facts__label">Leakage check</dt>
          <dd className="facts__value">{d.leakage_check || ABSENT}</dd>
        </div>
        <div className="facts__item">
          <dt className="facts__label">XGBoost retrained for this</dt>
          <dd className="facts__value">
            {ev.xgboost_retrained ? 'Yes' : `No — ${ev.xgboost_source}`}
          </dd>
        </div>
        <div className="facts__item">
          <dt className="facts__label">Features</dt>
          <dd className="facts__value">{count(d.n_features)}, identical for every model</dd>
        </div>
      </dl>
    </Card>
  );
}

/* --------------------------------------------------- confusion matrices --- */
function ConfusionMatrices({ ev }) {
  const keys = ['logistic_regression', 'random_forest', 'xgboost'];
  return (
    <Card className="stack" id="sec-confusion" tabIndex={-1}>
      <SectionHeader
        title="Confusion matrices"
        help="Counts from sklearn.metrics.confusion_matrix on the held-out fold.
              Shown at both thresholds because the numbers are very different
              and each answers a different question."
        subtitle="Held-out test set, at both thresholds."
      />
      {keys.map((k) => {
        const m = ev.models?.[k];
        if (!m) return null;
        return (
          <div className="conf-block" key={k}>
            <div className="conf-block__head">
              <strong>{m.model}</strong>
              <Badge tone={ROLE_TONE[m.role] || 'neutral'}>
                {ROLE_LABEL[m.role] || m.role}
              </Badge>
            </div>
            <div className="conf-pair">
              <Matrix title="At 0.5" blk={m.at_default_threshold} />
              <Matrix title="At top-5%" blk={m.at_alert_threshold} />
            </div>
          </div>
        );
      })}
    </Card>
  );
}

function Matrix({ title, blk }) {
  if (!blk) return null;
  const c = blk.confusion_matrix || {};
  return (
    <div className="conf">
      <div className="conf__title">
        {title}
        <HelpTip text={blk.basis || ''} />
      </div>
      <table className="conf__grid">
        <caption className="sr-only">{`Confusion matrix ${title}`}</caption>
        <thead>
          <tr>
            <td />
            <th scope="col">Pred. no</th>
            <th scope="col">Pred. yes</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Actual no</th>
            <td className="conf__ok">{count(c.true_negative)}</td>
            <td className="conf__bad">{count(c.false_positive)}</td>
          </tr>
          <tr>
            <th scope="row">Actual yes</th>
            <td className="conf__bad">{count(c.false_negative)}</td>
            <td className="conf__ok">{count(c.true_positive)}</td>
          </tr>
        </tbody>
      </table>
      <p className="conf__foot">
        F1 {isNum(blk.f1) ? num(blk.f1, 3) : ABSENT}
        {' · '}precision {isNum(blk.precision) ? num(blk.precision, 3) : ABSENT}
        {' · '}recall {isNum(blk.recall) ? num(blk.recall, 3) : ABSENT}
      </p>
    </div>
  );
}

/* ------------------------------------------------------- methodology ------ */
function Methodology({ ev }) {
  const rows = ev.methodology || [];
  return (
    <Card className="stack" id="sec-methodology" tabIndex={-1}>
      <SectionHeader
        title="Methodology"
        subtitle="How these numbers were produced, and what they do not say."
      />
      {rows.length === 0
        ? <EmptyState title="No methodology recorded" />
        : (
          <div className="method">
            {rows.map((m) => (
              <div className="method__item" key={m.heading}>
                <h4 className="method__heading">{m.heading}</h4>
                <p className="method__text">{m.text}</p>
              </div>
            ))}
          </div>
        )}
      {ev.generated_at && (
        <p className="muted-note">Evaluation run {ev.generated_at}.</p>
      )}
    </Card>
  );
}
