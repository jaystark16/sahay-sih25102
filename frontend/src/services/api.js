/**
 * Every backend endpoint, in one place.
 *
 * Each function documents the shape it returns, taken from the live API rather
 * than guessed, so that a component can be written against a known contract.
 * Where the backend does NOT return something, that is noted -- the directory
 * carries no attendance, for example, and the UI must not invent it.
 *
 * Grouped to match the API's own permission model: `staff` operations map to
 * routes behind require_mentor, and are surfaced only in admin views.
 */

import { get, post, put, patch, del, query } from './apiClient';

/* ---------------------------------------------------------------- auth ---- */
export const auth = {
  /** -> {token, user:{id,email,name,role,dept,section,must_change_password}, expires_at} */
  login: (userId, password) =>
    post('/auth/login', { user_id: userId, password }, { auth: false }),

  logout: () => post('/auth/logout'),

  /** -> the user object above. 401 when the token is dead. */
  me: (opts) => get('/auth/me', opts),

  changePassword: (oldPassword, newPassword) =>
    post('/auth/change-password', { old_password: oldPassword, new_password: newPassword }),

  /** staff. -> {users:[{id,name,role,dept,section,must_change_password}]} */
  users: (opts) => get('/auth/users', opts),
};

/* ----------------------------------------------------------- dashboard ---- */
export const dashboard = {
  /** -> {at_risk, total_students, rising, average_cgpa} */
  summaryCards: (mentor, opts) => get(`/dashboard${query({ mentor })}`, opts),

  /** -> {total_students, scored, not_yet_scoreable, bands:{Low,Medium,High},
   *      rising, open_interventions, mode, data_source} */
  summary: (mentor, opts) => get(`/summary${query({ mentor })}`, opts),

  /**
   * -> {this_week:[item], watch:[item], watch_page:{page,page_size,total,pages},
   *     routed_to_cohort_count, capacity, total_flagged, total_routed,
   *     cohort_alerts:[...], mentor, students_in_scope, computed_at, onboarding}
   * item: {roll_no,name,dept,year,section,score,band,delta,priority,confidence,
   *        headline,primary_driver,stage,model_pct,anomaly,cohort,guardrails,
   *        explained_by_cohort}
   */
  worklist: ({ mentor, capacity, page, pageSize, ...opts } = {}) =>
    get(`/worklist${query({ mentor, capacity, page, page_size: pageSize })}`, opts),

  /**
   * -> {total, page, page_size, pages, students:[{roll_no,name,dept,year,
   *     section,status,admitted_on,score,band}]}
   * NOTE: no attendance/cgpa here. /analytics/roster carries those.
   */
  students: ({ mentor, q, risk, page, pageSize, ...opts } = {}) =>
    get(`/students${query({ mentor, q, risk, page, page_size: pageSize })}`, opts),

  /** -> {students:[{roll_no,name,dept,year,section,admitted_on,weeks,stage:{...}}]} */
  onboarding: (limit, opts) => get(`/onboarding${query({ limit })}`, opts),
};

/* ------------------------------------------------------------- student ---- */
export const student = {
  /**
   * -> {student, stage, ledger, hybrid, forecast, delta, attendance,
   *     review, suggested_playbook, interventions, feedback}
   * ledger.components carries the per-signal breakdown that makes the score
   * explainable; hybrid.model is the ML opinion beside it.
   */
  detail: (rollNo, opts) => get(`/student/${encodeURIComponent(rollNo)}`, opts),

  /** The score-free view intended for the student themselves. Public route. */
  publicView: (rollNo) => get(`/student/${encodeURIComponent(rollNo)}/public`),

  /** -> the what-if payload with `levers` but no changes applied. */
  levers: (rollNo, opts) => get(`/student/${encodeURIComponent(rollNo)}/whatif`, opts),

  /**
   * -> {changes_applied, before:{score,band}, after:{score,band}, score_change,
   *     band_moved, lines:[{key,label,was,now,change,reason}], exact,
   *     statement, caveat, levers, to_reach_low?, model?}
   */
  whatIf: (rollNo, changes) =>
    post(`/student/${encodeURIComponent(rollNo)}/whatif`, changes),
};

/* ----------------------------------------------------------- analytics ---- */
export const analytics = {
  /** -> {measured:{rows,method,caution}, self_reported:{...}, measured_total, closed_total, note} */
  effectiveness: (opts) => get('/analytics/effectiveness', opts),

  /** -> {students:[{roll_no,name,gender,cgpa,band,score,attendance_pct}], total} */
  roster: (mentor, opts) => get(`/analytics/roster${query({ mentor })}`, opts),

  /** -> {dimensions:{gender|category|first_generation|hostel:[{group,n,flagged,
   *      flag_rate,disparity_ratio,review}]}, method, note} */
  fairness: (opts) => get('/analytics/fairness', opts),

  /** staff. -> {entries:[{id,actor,action,subject,detail,at}]} */
  audit: (limit, opts) => get(`/audit${query({ limit })}`, opts),
};

/* ------------------------------------------------------- model & config --- */
export const model = {
  /** -> {trained, mode, kind, horizon_weeks, target, target_is_not,
   *      excluded_features, beats_baselines, overall, data, ...} */
  status: (opts) => get('/model', opts),
};

export const config = {
  get: (opts) => get('/config', opts),
  /** staff. Backend validates and merges onto defaults; rejects unknown keys. */
  update: (cfg) => put('/config', cfg),
};

/** -> {default, mentors:[{id,name,role,scope,section}]} */
export const mentors = (opts) => get('/mentors', opts);

/* ------------------------------------------------------- interventions ---- */
export const interventions = {
  /** staff. -> {id, status} */
  create: ({ rollNo, mentor, playbook, trigger, actionText, followupDays }) =>
    post('/interventions', {
      roll_no: rollNo, mentor, playbook, trigger,
      action_text: actionText ?? '', followup_days: followupDays ?? 21,
    }),

  /** staff. -> {id,status,outcome,measured,measure_note} */
  close: (id, outcome, { mentor, notes } = {}) =>
    post(`/interventions/${encodeURIComponent(id)}/outcome`,
      { outcome, mentor, notes: notes ?? '' }),

  /** staff. Mentor agreement/disagreement with a flag. */
  feedback: ({ rollNo, mentor, verdict, reason }) =>
    post('/feedback', { roll_no: rollNo, mentor, verdict, reason: reason ?? '' }),
};

/* --------------------------------------------------- data operations ------ */
/** All staff-only. Destructive ones are confirmed in the UI before calling. */
export const dataOps = {
  /**
   * Preview by default. Nothing is written until commit=true, so the mentor
   * sees the detected column mapping first.
   */
  upload: (file, { commit = false } = {}) => {
    const form = new FormData();
    form.append('file', file);
    return post(`/upload${query({ commit })}`, form, { isForm: true });
  },

  admit: (payload) => post('/students', payload),
  admitBulk: (rows) => post('/students/bulk', rows),
  remove: (rollNo) => del(`/students/${encodeURIComponent(rollNo)}`),
  setStatus: (rollNo, status, note) =>
    patch(`/students/${encodeURIComponent(rollNo)}/status`, { status, note: note ?? '' }),

  addAttendance: ({ rollNo, weekStart, attended, held }) =>
    post('/attendance', { roll_no: rollNo, week_start: weekStart, attended, held }),

  addAssessment: ({ rollNo, which, marks, maxMarks }) =>
    post('/assessment', { roll_no: rollNo, which, marks, max_marks: maxMarks ?? 30 }),

  /** Recompute every score. Slow at institution size; show progress. */
  refresh: () => post('/refresh'),

  /** Re-derive whether past interventions helped, from attendance data. */
  measure: () => post('/measure'),

  /** DESTRUCTIVE: drops and rebuilds every table from demo_data/. */
  demoReset: () => post('/demo/reset'),
};

export default {
  auth, dashboard, student, analytics, model, config, mentors,
  interventions, dataOps,
};
