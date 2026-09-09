/**
 * Formatting and value-state helpers.
 *
 * The central idea here is that this product must never show a number it does
 * not have. A student with 0% attendance and a student whose attendance has
 * never been recorded are completely different situations, and rendering both
 * as "0" would be a data-integrity bug, not a cosmetic one. Every formatter
 * below therefore returns an explicit absent-marker rather than coercing
 * null/undefined/NaN to zero.
 */

/** What to print when a value genuinely is not there. */
export const ABSENT = '—';

/**
 * Distinguish the states the backend can legitimately be in.
 * Phase 5 of the product brief calls these out explicitly, because
 * "not scored yet" is a different message to the mentor than "scored zero".
 */
export const ValueState = {
  OK: 'ok',
  ZERO: 'zero',
  MISSING: 'missing',        // backend returned null/undefined
  NOT_APPLICABLE: 'na',      // meaningless for this record
  INSUFFICIENT: 'insufficient', // not enough history yet
};

/** True only for a real, finite number. Guards every formatter below. */
export function isNum(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

export function valueState(v) {
  if (v === null || v === undefined) return ValueState.MISSING;
  if (typeof v === 'number' && !Number.isFinite(v)) return ValueState.MISSING;
  if (v === 0) return ValueState.ZERO;
  return ValueState.OK;
}

/** A number, or the absent marker. Never "0" for a missing value. */
export function num(v, digits = 0) {
  if (!isNum(v)) return ABSENT;
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** A percentage. `digits` defaults to 0 because most of these are whole. */
export function pct(v, digits = 0) {
  if (!isNum(v)) return ABSENT;
  return `${num(v, digits)}%`;
}

/** A signed change: +6, -4, or 0 with no sign. */
export function signed(v) {
  if (!isNum(v)) return ABSENT;
  if (v === 0) return '0';
  return `${v > 0 ? '+' : ''}${num(v)}`;
}

/** CGPA-style values, which read wrong without two decimals. */
export function decimal(v, digits = 2) {
  return isNum(v) ? num(v, digits) : ABSENT;
}

/** Large counts: 5,008. */
export function count(v) {
  return isNum(v) ? v.toLocaleString() : ABSENT;
}

/**
 * ISO timestamp -> "9 Sep 2026". The backend stores these as TEXT in ISO
 * form; anything unparseable is treated as missing rather than shown raw.
 */
export function date(v) {
  if (!v) return ABSENT;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return ABSENT;
  return d.toLocaleDateString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
  });
}

/** "3 days ago" / "just now", for activity feeds. */
export function relativeTime(v) {
  if (!v) return ABSENT;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return ABSENT;
  const secs = Math.round((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return date(v);
}

/**
 * Turn a backend enum/field name into something a mentor can read.
 *
 * The brief is explicit that raw identifiers like `primary_driver`,
 * `rules_only` or `model_pct` must not reach the screen. Anything not in the
 * map falls back to a de-snake-cased Sentence case, so a new backend value
 * degrades to readable rather than to a crash or a raw token.
 */
const LABELS = {
  // stage.scoring / stage.stage
  rules_only: 'Rules only',
  hybrid: 'Rules + model',
  onboarding: 'Collecting data',
  full: 'Full history',
  none: 'No data yet',
  // risk components (keys of ledger.components)
  attendance_level: 'Attendance level',
  attendance_decline: 'Attendance decline',
  assessment_level: 'Assessment level',
  assessment_trend: 'Assessment trend',
  backlogs: 'Backlogs',
  submission: 'Assignment submission',
  fee: 'Fee status',
  // intervention outcomes
  improved: 'Improved',
  unchanged: 'Unchanged',
  worsened: 'Worsened',
  unreachable: 'Could not reach',
  open: 'Open',
  closed: 'Closed',
  // student status
  active: 'Active',
  enrolled: 'Enrolled',
  // fairness dimensions
  first_generation: 'First-generation',
  hostel: 'Hostel resident',
  gender: 'Gender',
  category: 'Category',
};

export function label(v) {
  if (v === null || v === undefined || v === '') return ABSENT;
  const key = String(v);
  if (LABELS[key]) return LABELS[key];
  const spaced = key.replace(/_/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Percent of a whole, guarding a zero denominator. */
export function share(part, whole) {
  if (!isNum(part) || !isNum(whole) || whole === 0) return null;
  return (part / whole) * 100;
}
