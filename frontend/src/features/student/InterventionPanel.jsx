import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import api from '../../services/api';
import { Badge, Button, Card, SectionHeader } from '../../components/ui/Primitives';
import { EmptyState, ErrorState } from '../../components/ui/States';
import { useToast } from '../../components/ui/Toast';
import { OUTCOMES } from '../../lib/constants';
import { date, isNum, label, num, signed } from '../../lib/format';

/**
 * Interventions: what was suggested, what was done, and whether it worked.
 *
 * The suggested playbook comes from the backend's rules; the mentor approves
 * it explicitly rather than it being applied automatically, which is the
 * product's stated position that a human decides every action.
 *
 * Outcomes are recorded and then *measured* against attendance data, so both
 * numbers are shown: what the mentor reported and what the data says.
 */
export function InterventionPanel({ rollNo, interventions, suggested, onChanged }) {
  const { user, isStaff } = useAuth();
  const toast = useToast();
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const list = interventions || [];
  const open = list.filter((i) => i.status === 'open');
  const closed = list.filter((i) => i.status !== 'open');

  const approve = async () => {
    if (!suggested) return;
    setBusy(true); setError(null);
    try {
      await api.interventions.create({
        rollNo,
        mentor: user?.id,
        playbook: suggested.title || suggested.playbook,
        trigger: suggested.trigger,
        actionText: draft,
        followupDays: suggested.followup_days ?? 21,
      });
      toast.success('Intervention recorded.');
      setDraft('');
      onChanged?.();
    } catch (e) {
      setError(e);
      toast.error(e.fullMessage || e.message);
    } finally {
      setBusy(false);
    }
  };

  const close = async (id, outcome) => {
    setBusy(true); setError(null);
    try {
      const res = await api.interventions.close(id, outcome, { mentor: user?.id });
      toast.success(res?.measure_note || 'Outcome recorded.');
      onChanged?.();
    } catch (e) {
      setError(e);
      toast.error(e.fullMessage || e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="stack">
      <SectionHeader
        title="Action"
        help={'Nothing is sent automatically. The system suggests; a mentor decides '
          + 'and records what was actually done.'}
      />

      {error && <ErrorState error={error} compact />}

      {suggested && isStaff && (
        <div className="playbook">
          <div className="playbook__head">
            <strong>{suggested.title || suggested.playbook}</strong>
            {suggested.trigger && <Badge tone="neutral">{suggested.trigger}</Badge>}
          </div>
          {suggested.why && <p className="playbook__why">{suggested.why}</p>}
          {Array.isArray(suggested.steps) && suggested.steps.length > 0 && (
            <ol className="playbook__steps">
              {suggested.steps.map((st, i) => <li key={i}>{st}</li>)}
            </ol>
          )}
          <label className="field">
            <span className="field__label" htmlFor="iv-note">
              What will you actually do? (optional)
            </span>
            <textarea
              id="iv-note"
              className="field__input field__input--area"
              rows={3}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="e.g. Call the student and their guardian this week"
            />
          </label>
          <Button variant="primary" onClick={approve} busy={busy}>
            Approve and record
          </Button>
        </div>
      )}

      {!suggested && (
        <EmptyState
          title="No playbook suggested"
          description="The rules did not match a standard action for this student.
                       Record something manually if you take action."
        />
      )}

      {open.length > 0 && (
        <div className="iv-group">
          <h4 className="iv-group__title">Open</h4>
          <ul className="iv-list">
            {open.map((iv) => (
              <li key={iv.id} className="iv-item">
                <div className="iv-item__head">
                  <strong>{iv.playbook || label(iv.trigger)}</strong>
                  <span className="muted-note">
                    Started {date(iv.created_at)}
                    {iv.followup_at && <> · follow up {date(iv.followup_at)}</>}
                  </span>
                </div>
                {iv.action_text && <p className="iv-item__note">{iv.action_text}</p>}
                {isStaff && (
                  <div className="iv-item__outcomes">
                    <span className="muted-note">Record outcome:</span>
                    {OUTCOMES.map((o) => (
                      <Button key={o.value} size="sm" variant="secondary"
                        disabled={busy} onClick={() => close(iv.id, o.value)}>
                        {o.label}
                      </Button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {closed.length > 0 && (
        <div className="iv-group">
          <h4 className="iv-group__title">Completed</h4>
          <ul className="iv-list">
            {closed.map((iv) => (
              <li key={iv.id} className="iv-item iv-item--closed">
                <div className="iv-item__head">
                  <strong>{iv.playbook || label(iv.trigger)}</strong>
                  <Badge tone={toneFor(iv.outcome)}>{label(iv.outcome)}</Badge>
                </div>
                <p className="muted-note">
                  {date(iv.created_at)}
                  {/* Reported vs measured are different claims and shown as such. */}
                  {isNum(iv.measured_change) && (
                    <> · measured attendance change {signed(num(iv.measured_change, 1))} points</>
                  )}
                  {!isNum(iv.measured_change) && iv.measured_outcome && (
                    <> · measured: {label(iv.measured_outcome)}</>
                  )}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {list.length === 0 && (
        <p className="muted-note">No interventions recorded for this student yet.</p>
      )}
    </Card>
  );
}

function toneFor(outcome) {
  const found = OUTCOMES.find((o) => o.value === outcome);
  return found ? found.tone : 'neutral';
}
