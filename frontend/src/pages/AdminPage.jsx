import { useState } from 'react';
import { useApi } from '../hooks/useApi';
import api from '../services/api';
import { Badge, Button, Card, SectionHeader } from '../components/ui/Primitives';
import { AsyncBoundary, EmptyState, ErrorState } from '../components/ui/States';
import { ConfirmDialog } from '../components/ui/Modal';
import { useToast } from '../components/ui/Toast';
import { IconRefresh, IconUpload } from '../components/ui/Icons';
import { ABSENT, count, date, label, num, relativeTime } from '../lib/format';

/**
 * Staff operations.
 *
 * Everything here is behind the API's require_mentor dependency, and the two
 * destructive actions are additionally gated in the UI: a refresh is merely
 * slow, but a demo reset drops every table, so it asks the operator to type
 * the word before it will run.
 */
export function AdminPage() {
  return (
    <div className="page">
      <UploadPanel />
      <div className="grid-2">
        <MaintenancePanel />
        <AuditPanel />
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- upload ------ */
/**
 * Spreadsheet import, staged.
 *
 * The API previews by default and only writes when commit=true, so the flow
 * mirrors that: choose a file, see what the parser detected and what it could
 * not match, then confirm. Failures show the parser's actual message rather
 * than a generic "upload failed".
 */
function UploadPanel() {
  const toast = useToast();
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [committed, setCommitted] = useState(null);

  const choose = (f) => {
    setFile(f); setPreview(null); setError(null); setCommitted(null);
  };

  const runPreview = async () => {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      setPreview(await api.dataOps.upload(file, { commit: false }));
    } catch (e) {
      setError(e);
    } finally { setBusy(false); }
  };

  const commit = async () => {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const res = await api.dataOps.upload(file, { commit: true });
      setCommitted(res);
      setPreview(res);
      toast.success('Import committed and scores refreshed.');
    } catch (e) {
      setError(e);
      toast.error(e.fullMessage || e.message);
    } finally { setBusy(false); }
  };

  return (
    <Card className="stack" id="sec-upload" tabIndex={-1}>
      <SectionHeader
        title="Import a spreadsheet"
        subtitle="Attendance registers, internal marks, fees. Nothing is written until you confirm."
      />

      <div className="upload">
        <label className="upload__drop">
          <IconUpload width={22} height={22} />
          <span className="upload__label">
            {file ? file.name : 'Choose a .xlsx, .csv or .tsv file'}
          </span>
          <input
            type="file"
            className="upload__input"
            accept=".xlsx,.xls,.csv,.tsv"
            onChange={(e) => choose(e.target.files?.[0] || null)}
          />
        </label>
        <div className="upload__actions">
          <Button variant="secondary" onClick={runPreview} busy={busy} disabled={!file}>
            Preview
          </Button>
          <Button variant="primary" onClick={commit} busy={busy}
            disabled={!preview || Boolean(committed)}>
            {committed ? 'Committed' : 'Commit import'}
          </Button>
        </div>
      </div>

      {error && <ErrorState error={error} />}

      {preview && <UploadReport report={preview} committed={Boolean(committed)} />}
    </Card>
  );
}

function UploadReport({ report, committed }) {
  const mapping = report.columns || report.mapping || report.detected;
  return (
    <div className="upload-report">
      <div className="metric-strip metric-strip--compact">
        <div className="metric">
          <span className="metric__label">Rows matched</span>
          <span className="metric__value">{count(report.rows_keyed)}</span>
        </div>
        <div className="metric">
          <span className="metric__label">Rows unmatched</span>
          <span className="metric__value">{count(report.rows_unmatched)}</span>
        </div>
        {report.applied !== undefined && (
          <div className="metric">
            <span className="metric__label">Applied</span>
            <span className="metric__value">{count(report.applied)}</span>
          </div>
        )}
      </div>

      {committed && (
        <Badge tone="success">Committed — scores were refreshed afterwards</Badge>
      )}
      {!committed && (
        <p className="muted-note">
          This is a preview. Nothing has been written to the database yet.
        </p>
      )}

      {mapping && typeof mapping === 'object' && (
        <details className="upload-report__details" open>
          <summary>Detected column mapping</summary>
          <dl className="facts">
            {Object.entries(mapping).map(([k, v]) => (
              <div className="facts__item" key={k}>
                <dt className="facts__label">{label(k)}</dt>
                <dd className="facts__value">
                  {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      {Array.isArray(report.unmatched) && report.unmatched.length > 0 && (
        <details className="upload-report__details">
          <summary>{report.unmatched.length} unmatched row(s)</summary>
          <ul className="upload-report__list">
            {report.unmatched.slice(0, 25).map((u, i) => (
              <li key={i}>{typeof u === 'object' ? JSON.stringify(u) : String(u)}</li>
            ))}
          </ul>
        </details>
      )}

      {report.refresh && (
        <p className="muted-note">
          Rescored {count(report.refresh.scored)} students
          {report.refresh.seconds !== undefined && ` in ${num(report.refresh.seconds, 1)}s`}.
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------ maintenance ------ */
function MaintenancePanel() {
  const toast = useToast();
  const [confirming, setConfirming] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const run = async (kind) => {
    setBusy(true); setError(null);
    try {
      if (kind === 'refresh') {
        const r = await api.dataOps.refresh();
        toast.success(`Rescored ${count(r.scored)} students in ${num(r.seconds, 1)}s.`);
      } else if (kind === 'measure') {
        const r = await api.dataOps.measure();
        toast.success(`Measured ${count(r.measured)} interventions.`);
      } else if (kind === 'reset') {
        const r = await api.dataOps.demoReset();
        toast.success(`Database rebuilt with ${count(r.students)} students.`);
      }
      setConfirming(null);
    } catch (e) {
      setError(e);
      toast.error(e.fullMessage || e.message);
    } finally { setBusy(false); }
  };

  return (
    <Card className="stack" id="sec-maintenance" tabIndex={-1}>
      <SectionHeader title="Maintenance" />

      <ul className="ops-list">
        <li className="ops-item">
          <div>
            <strong>Recompute all scores</strong>
            <p className="muted-note">
              Re-runs the risk engine over every student. Safe, but slow at
              institution size.
            </p>
          </div>
          <Button variant="secondary" icon={<IconRefresh width={16} height={16} />}
            busy={busy && confirming === 'refresh'}
            onClick={() => { setConfirming('refresh'); run('refresh'); }}>
            Refresh
          </Button>
        </li>

        <li className="ops-item">
          <div>
            <strong>Re-measure intervention outcomes</strong>
            <p className="muted-note">
              Recalculates whether past actions helped, from attendance data.
            </p>
          </div>
          <Button variant="secondary" busy={busy && confirming === 'measure'}
            onClick={() => { setConfirming('measure'); run('measure'); }}>
            Measure
          </Button>
        </li>

        <li className="ops-item ops-item--danger">
          <div>
            <strong>Reset the demo database</strong>
            <p className="muted-note">
              <strong>Destructive.</strong> Drops every table and rebuilds from the
              bundled demo data. All students, interventions and history are lost.
            </p>
          </div>
          <Button variant="danger" onClick={() => setConfirming('reset-confirm')}>
            Reset…
          </Button>
        </li>
      </ul>

      {error && <ErrorState error={error} compact />}

      {confirming === 'reset-confirm' && (
        <ConfirmDialog
          title="Reset the entire database?"
          description="Every student, intervention, outcome and audit entry will be
                       deleted and rebuilt from the bundled demo data. This cannot
                       be undone."
          confirmLabel="Drop and rebuild"
          tone="danger"
          requireTyping="RESET"
          busy={busy}
          error={error}
          onConfirm={() => run('reset')}
          onClose={() => { setConfirming(null); setError(null); }}
        />
      )}
    </Card>
  );
}

/* ------------------------------------------------------------ audit ------ */
function AuditPanel() {
  const { data, loading, error, reload } = useApi(
    ({ signal }) => api.analytics.audit(50, { signal }), [],
  );
  const entries = data?.entries || [];

  return (
    <Card className="stack" id="sec-activity" tabIndex={-1}>
      <SectionHeader title="Recent activity"
        subtitle="Every write is recorded against the account that made it." />
      <AsyncBoundary
        loading={loading} error={error} onRetry={reload} skeletonRows={5}
        isEmpty={entries.length === 0}
        empty={<EmptyState title="No activity recorded yet" />}
      >
        <ul className="audit-list">
          {entries.map((e) => (
            <li key={e.id} className="audit-item">
              <span className="audit-item__action">{label(e.action)}</span>
              <span className="audit-item__subject">{e.subject || ABSENT}</span>
              <span className="audit-item__actor">{e.actor}</span>
              <time className="audit-item__time" dateTime={e.at} title={date(e.at)}>
                {relativeTime(e.at)}
              </time>
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </Card>
  );
}
