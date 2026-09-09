import { useEffect, useRef, useState } from 'react';
import api from '../../services/api';
import { Button, Badge } from '../../components/ui/Primitives';
import { Modal } from '../../components/ui/Modal';
import { ErrorState } from '../../components/ui/States';
import { CATEGORIES, DEPTS, GENDERS, SECTIONS, YEARS } from '../../lib/constants';

/**
 * Admitting and removing a student.
 *
 * Both routes existed on the API and in services/api.js the whole time
 * (dataOps.admit, dataOps.remove) but nothing rendered a control for them, so
 * the previous UI's "+ Add Student" and "- Remove Student" were lost in the
 * rebuild. These restore them.
 *
 * Both are staff-only. That is not a UI preference: POST /api/students depends
 * on actor_id, which itself depends on require_mentor, and DELETE
 * /api/students/{roll} depends on require_mentor directly. Rendering these
 * buttons for a non-staff account would only produce a 403.
 */

/* ------------------------------------------------------------- admit ------ */
/**
 * The admissions-day form.
 *
 * lifecycle.admit() requires name, dept and year and nothing else, so those
 * three are the only required fields here. Everything after them is genuinely
 * optional and is grouped separately rather than presented as if it were
 * needed on day one.
 *
 * Department is a picker over the departments that exist rather than a text
 * box. Free text is how you end up with "CSE", "cse " and "Cse" as three
 * departments, and dept is what generates the roll number and picks the
 * mentor -- a typo there produces a student assigned to a section nobody owns.
 */
export function AddStudentModal({ onClose, onAdmitted }) {
  const [form, setForm] = useState({
    name: '', dept: '', year: 1, section: 'A',
    roll_no: '', admission_date: '', gender: '', category: '',
    cgpa: '', first_gen: '', hostel: '',
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [more, setMore] = useState(false);

  const set = (k) => (e) => {
    setForm((f) => ({ ...f, [k]: e.target.value }));
    setError(null);
  };

  const nameOk = form.name.trim().length > 0;
  const deptOk = Boolean(form.dept);
  // The backend rejects a non-finite cgpa outright, because a NaN average
  // propagates through every cohort statistic. Catch it before the round trip.
  const cgpaOk = form.cgpa === ''
    || (Number.isFinite(Number(form.cgpa)) && Number(form.cgpa) >= 0 && Number(form.cgpa) <= 10);
  const canSubmit = nameOk && deptOk && cgpaOk && !busy;

  const submit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true); setError(null);

    // Empty strings are not "no value" to the API -- they are values. Optional
    // fields are omitted entirely so the columns stay NULL.
    const payload = {
      name: form.name.trim(),
      dept: form.dept,
      year: Number(form.year),
      section: form.section,
    };
    if (form.roll_no.trim()) payload.roll_no = form.roll_no.trim().toUpperCase();
    if (form.admission_date) payload.admission_date = form.admission_date;
    if (form.gender) payload.gender = form.gender;
    if (form.category) payload.category = form.category;
    if (form.cgpa !== '') payload.cgpa = Number(form.cgpa);
    if (form.first_gen !== '') payload.first_gen = form.first_gen === 'yes';
    if (form.hostel !== '') payload.hostel = form.hostel === 'yes';

    try {
      onAdmitted(await api.dataOps.admit(payload));
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Add a student"
      description="Only what an admissions office has on day one. No attendance or marks required."
      onClose={onClose}
      size="md"
      footer={(
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={submit} busy={busy} disabled={!canSubmit}>
            Add student
          </Button>
        </>
      )}
    >
      <form className="stack" onSubmit={submit}>
        <label className="field">
          <span className="field__label">Full name</span>
          <input
            className="field__input"
            value={form.name}
            onChange={set('name')}
            autoComplete="off"
            placeholder="e.g. Meera Nair"
            required
          />
        </label>

        <div className="field-row">
          <label className="field">
            <span className="field__label">Department</span>
            <select className="field__input" value={form.dept} onChange={set('dept')} required>
              <option value="">Select…</option>
              {DEPTS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Year</span>
            <select className="field__input" value={form.year} onChange={set('year')}>
              {YEARS.map((y) => <option key={y} value={y}>{`Year ${y}`}</option>)}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Section</span>
            <select className="field__input" value={form.section} onChange={set('section')}>
              {SECTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        </div>

        <p className="field__help">
          The roll number and the mentor are assigned from the department, year
          and section unless you set them yourself below.
        </p>

        <button
          type="button"
          className="disclosure"
          aria-expanded={more}
          onClick={() => setMore((m) => !m)}
        >
          {more ? 'Hide optional details' : 'Add optional details'}
        </button>

        {more && (
          <div className="stack">
            <div className="field-row">
              <label className="field">
                <span className="field__label">Roll number</span>
                <input
                  className="field__input"
                  value={form.roll_no}
                  onChange={set('roll_no')}
                  autoComplete="off"
                  placeholder="auto"
                  spellCheck="false"
                />
              </label>
              <label className="field">
                <span className="field__label">Admission date</span>
                <input
                  className="field__input"
                  type="date"
                  value={form.admission_date}
                  onChange={set('admission_date')}
                />
              </label>
              <label className="field">
                <span className="field__label">CGPA on entry</span>
                <input
                  className="field__input"
                  value={form.cgpa}
                  onChange={set('cgpa')}
                  inputMode="decimal"
                  placeholder="0 – 10"
                />
              </label>
            </div>
            {!cgpaOk && (
              <p className="field__error" role="alert">
                CGPA has to be a number between 0 and 10.
              </p>
            )}

            <div className="field-row">
              <label className="field">
                <span className="field__label">Gender</span>
                <select className="field__input" value={form.gender} onChange={set('gender')}>
                  <option value="">Not recorded</option>
                  {GENDERS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
                </select>
              </label>
              <label className="field">
                <span className="field__label">Category</span>
                <select className="field__input" value={form.category} onChange={set('category')}>
                  <option value="">Not recorded</option>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
            </div>

            <div className="field-row">
              <label className="field">
                <span className="field__label">First-generation learner</span>
                <select className="field__input" value={form.first_gen} onChange={set('first_gen')}>
                  <option value="">Not recorded</option>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </label>
              <label className="field">
                <span className="field__label">Hostel resident</span>
                <select className="field__input" value={form.hostel} onChange={set('hostel')}>
                  <option value="">Not recorded</option>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </label>
            </div>

            <p className="field__help">
              Gender and category are stored only so the fairness audit can run.
              They are never scoring inputs and never appear on a mentor&apos;s screen.
            </p>
          </div>
        )}

        {error && <ErrorState error={error} compact />}
      </form>
    </Modal>
  );
}

/* ------------------------------------------------------------ remove ------ */
/**
 * Removing a student, in two steps.
 *
 * The old version of this loaded the entire roster into a <select>, which is
 * 5,000 options and every one of them a scroll away from the wrong student.
 * This searches the paginated directory instead -- the same /students?q=
 * endpoint the directory page uses -- so you find a person by name or roll
 * number and see their department and section before you act.
 *
 * Then it confirms against the roll number typed out in full. A delete takes
 * the attendance history, interventions and score history with it and there is
 * no undo, so a single mis-click must not be able to do it.
 */
export function RemoveStudentModal({ onClose, onRemoved }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const [picked, setPicked] = useState(null);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  // Debounced so typing a name is not one request per keystroke.
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
      api.dashboard.students({ q: term, pageSize: 8, signal: ac.signal })
        .then((d) => { setResults(d.students || []); setSearchError(null); })
        .catch((e) => { if (e.name !== 'AbortError') setSearchError(e); })
        .finally(() => { if (!ac.signal.aborted) setSearching(false); });
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const unlocked = picked && typed.trim().toUpperCase() === picked.roll_no.toUpperCase();

  const remove = async () => {
    if (!unlocked) return;
    setBusy(true); setError(null);
    try {
      onRemoved(await api.dataOps.remove(picked.roll_no));
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={picked ? 'Remove this student?' : 'Remove a student'}
      description={picked
        ? undefined
        : 'Search by name or roll number. Nothing is deleted until you confirm.'}
      onClose={onClose}
      size="sm"
      footer={picked ? (
        <>
          <Button variant="ghost" disabled={busy}
            onClick={() => { setPicked(null); setTyped(''); setError(null); }}>
            Back
          </Button>
          <Button variant="danger" onClick={remove} busy={busy} disabled={!unlocked}>
            {busy ? 'Removing…' : 'Remove permanently'}
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
          </label>

          {q.trim().length > 0 && q.trim().length < 2 && (
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
                    onClick={() => { setPicked(s); setTyped(''); }}>
                    <span className="pick-list__name">{s.name}</span>
                    <span className="pick-list__meta">
                      {s.roll_no} · {s.dept} {s.year}-{s.section}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="stack">
          <div className="confirm-subject">
            <strong>{picked.name}</strong>
            <span className="pick-list__meta">
              {picked.roll_no} · {picked.dept} {picked.year}-{picked.section}
            </span>
            {picked.band && <Badge tone="neutral">{picked.band} risk</Badge>}
          </div>

          <p className="muted-note">
            Their attendance history, interventions, feedback and every past risk
            score are deleted along with the record. This cannot be undone.
          </p>
          <p className="muted-note">
            Cohort scores are recomputed afterwards, so this takes roughly ten
            seconds to finish.
          </p>

          <label className="field">
            <span className="field__label">
              Type <code>{picked.roll_no}</code> to confirm
            </span>
            <input
              className="field__input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck="false"
            />
          </label>

          {error && <ErrorState error={error} compact />}
        </div>
      )}
    </Modal>
  );
}
