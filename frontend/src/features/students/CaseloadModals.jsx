import { useEffect, useRef, useState } from 'react';
import api from '../../services/api';
import { Badge, Button } from '../../components/ui/Primitives';
import { Modal } from '../../components/ui/Modal';
import { ErrorState } from '../../components/ui/States';

/**
 * A mentor's caseload: taking a student on, and handing them back.
 *
 * These are deliberately NOT the admin create/delete pair.
 *
 *   Add  -- the student already exists in the institution. This creates an
 *           ASSIGNMENT. No student record is created and none is duplicated,
 *           and everything already known about them comes with them.
 *   Drop -- ends that assignment. The student, their attendance, their
 *           interventions, their outcomes, their risk history and the audit
 *           trail all stay exactly where they are. The HOD still sees them and
 *           another mentor can pick them up.
 *
 * Deleting a student from the institution is a separate, institution-only
 * operation, and a mentor is never shown it. "Remove from my list" must not be
 * one mis-click away from erasing a person's history.
 */

/* --------------------------------------------------------------- add ------ */
export function AddToCaseloadModal({ onClose, onAdded }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const [picked, setPicked] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  // Debounced: typing a name should not be one request per keystroke.
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setResults(null); setSearching(false); setSearchError(null);
      return undefined;
    }
    setSearching(true);
    const t = setTimeout(() => {
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      api.caseload.lookup(term, { limit: 10, signal: ac.signal })
        .then((d) => { setResults(d.students || []); setSearchError(null); })
        .catch((e) => { if (e.name !== 'AbortError') setSearchError(e); })
        .finally(() => { if (!ac.signal.aborted) setSearching(false); });
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const confirm = async () => {
    setBusy(true); setError(null);
    try {
      onAdded(await api.caseload.add(picked.roll_no));
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={picked ? 'Add to your caseload?' : 'Add a student'}
      description={picked ? undefined
        : 'Search the institution for a student, then take them onto your caseload.'}
      onClose={onClose}
      size="sm"
      footer={picked ? (
        <>
          <Button variant="ghost" disabled={busy}
            onClick={() => { setPicked(null); setError(null); }}>Back</Button>
          <Button variant="primary" onClick={confirm} busy={busy}>
            Add to my caseload
          </Button>
        </>
      ) : (
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
      )}
    >
      {!picked ? (
        <div className="stack">
          <label className="field">
            <span className="field__label">Find the student</span>
            <input
              className="field__input"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Name or roll number"
              autoComplete="off"
              spellCheck="false"
            />
            <span className="field__help">
              Searches every student in the institution, not just yours — that
              is the point of this screen. Only the details needed to identify
              the right person are shown.
            </span>
          </label>

          {q.trim().length === 1 && (
            <p className="field__help">Type at least two characters.</p>
          )}
          {searching && <p className="field__help">Searching…</p>}
          {searchError && <ErrorState error={searchError} compact />}
          {results && results.length === 0 && !searching && (
            <p className="field__help">No student matches “{q.trim()}”.</p>
          )}

          {results && results.length > 0 && (
            <ul className="pick-list">
              {results.map((s) => (
                <li key={s.roll_no}>
                  <button type="button" className="pick-list__item"
                    onClick={() => setPicked(s)}>
                    <span className="pick-list__name">{s.name}</span>
                    <span className="pick-list__meta">
                      {s.roll_no} · {s.dept} {s.year}-{s.section}
                    </span>
                    {s.current_mentor_name && (
                      <span className="pick-list__note">
                        Currently with {s.current_mentor_name}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="stack">
          <div className="confirm-subject confirm-subject--neutral">
            <strong>{picked.name}</strong>
            <span className="pick-list__meta">
              {picked.roll_no} · {picked.dept} {picked.year}-{picked.section}
            </span>
            {picked.current_mentor_name
              ? <Badge tone="warning">Currently with {picked.current_mentor_name}</Badge>
              : <Badge tone="neutral">No mentor assigned</Badge>}
          </div>

          <p className="muted-note">
            This adds an assignment. {picked.name.split(' ')[0]} is already a
            student here — no new record is created, and their attendance,
            marks and history come with them.
          </p>
          {picked.current_mentor_name && (
            <p className="muted-note">
              They will be moved off {picked.current_mentor_name}’s caseload.
              The previous assignment is kept as history, not erased.
            </p>
          )}

          {error && <ErrorState error={error} compact />}
        </div>
      )}
    </Modal>
  );
}

/* -------------------------------------------------------------- drop ------ */
export function DropFromCaseloadModal({ onClose, onDropped }) {
  const [q, setQ] = useState('');
  const [mine, setMine] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [picked, setPicked] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  // The whole caseload is 20-40 rows, so it loads once and filters locally.
  // No debounce and no per-keystroke request needed at this size.
  useEffect(() => {
    const ac = new AbortController();
    api.caseload.mine({ signal: ac.signal })
      .then(setMine)
      .catch((e) => { if (e.name !== 'AbortError') setLoadError(e); });
    return () => ac.abort();
  }, []);

  const term = q.trim().toLowerCase();
  const shown = (mine?.students || []).filter(
    (s) => !term || s.name.toLowerCase().includes(term)
      || s.roll_no.toLowerCase().includes(term),
  );

  const confirm = async () => {
    setBusy(true); setError(null);
    try {
      onDropped(await api.caseload.drop(picked.roll_no));
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={picked ? 'Remove from your caseload?' : 'Remove a student from your caseload'}
      description={picked ? undefined
        : 'This hands the student back. It does not delete them.'}
      onClose={onClose}
      size="sm"
      footer={picked ? (
        <>
          <Button variant="ghost" disabled={busy}
            onClick={() => { setPicked(null); setError(null); }}>Back</Button>
          <Button variant="danger" onClick={confirm} busy={busy}>
            {busy ? 'Removing…' : 'Remove from my caseload'}
          </Button>
        </>
      ) : (
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
      )}
    >
      {!picked ? (
        <div className="stack">
          {loadError && <ErrorState error={loadError} compact />}
          {!mine && !loadError && <p className="field__help">Loading your caseload…</p>}
          {mine && (
            <>
              <label className="field">
                <span className="field__label">
                  Your students ({mine.count})
                </span>
                <input
                  className="field__input"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Filter by name or roll number"
                  autoComplete="off"
                />
              </label>
              {shown.length === 0
                ? <p className="field__help">Nobody on your caseload matches that.</p>
                : (
                  <ul className="pick-list">
                    {shown.map((s) => (
                      <li key={s.roll_no}>
                        <button type="button" className="pick-list__item"
                          onClick={() => setPicked(s)}>
                          <span className="pick-list__name">{s.name}</span>
                          <span className="pick-list__meta">
                            {s.roll_no} · {s.dept} {s.year}-{s.section}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
            </>
          )}
        </div>
      ) : (
        <div className="stack">
          <div className="confirm-subject confirm-subject--neutral">
            <strong>{picked.name}</strong>
            <span className="pick-list__meta">
              {picked.roll_no} · {picked.dept} {picked.year}-{picked.section}
            </span>
          </div>

          {/* Stated plainly, because "remove" is the word people fear. */}
          <p className="muted-note">
            <strong>This is not a deletion.</strong> {picked.name.split(' ')[0]}
            {' '}stays a student at this institution. Their attendance record,
            interventions, outcomes and risk history are all kept, the head of
            department can still see them, and another mentor can be assigned.
          </p>
          <p className="muted-note">
            They will simply no longer appear in your worklist or your student
            list.
          </p>

          {error && <ErrorState error={error} compact />}
        </div>
      )}
    </Modal>
  );
}
