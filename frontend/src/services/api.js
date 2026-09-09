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
/**
 * Scope is NOT sent from here.
 *
 * These used to take a `mentor` argument and put it in the query string, which
 * made the browser the authority on what it was allowed to see -- omitting it
 * returned the whole institution. The API now derives scope from the session
 * (see resolve_scope), so a mentor gets their caseload and an HOD gets the
 * institution, whatever the request says.
 *
 * `mentor` survives on a few of these as an OPTIONAL narrowing argument, and
 * the backend honours it only for institution roles. That is the HOD's
 * per-mentor drill-down; for a mentor it is refused with 403 rather than
 * silently ignored.
 */
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

  /**
   * The model comparison, computed by `python ml.py --evaluate` from real
   * predictions on a held-out fold and shipped in model_report.json.
   *
   * -> {available, dataset:{...}, table:[...], methodology:[...],
   *     models:{logistic_regression|random_forest|xgboost|...:
   *       {model, role, roc_auc, pr_auc, brier_score,
   *        at_default_threshold:{accuracy,precision,recall,f1,confusion_matrix,basis},
   *        at_alert_threshold:{...}}}}
   * When the evaluation has not been run: {available:false, reason, hint}.
   */
  evaluation: (opts) => get('/model-evaluation', opts),
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

/* ------------------------------------------------------------ caseload ---- */
/**
 * Assignment, not creation. Dropping, not deletion.
 *
 * A mentor adding a student is taking responsibility for someone who already
 * exists in the institution -- no student record is created and none is
 * duplicated. A mentor dropping a student ends that responsibility and keeps
 * every trace of the student: record, attendance, interventions, outcomes,
 * risk history, audit trail. Deletion is a separate institution-only route.
 */
export const caseload = {
  /** -> {mentor_id, mentor_name, count, departments:{DEPT:n}, cohort_count, students:[...]} */
  mine: (opts) => get('/mentor/caseload', opts),

  /**
   * Institution-wide student lookup, for the Add flow only.
   *
   * Separate from dashboard.students on purpose: that one is scoped to the
   * caller, so a mentor could never use it to find a student who is not yet
   * theirs. Returns identification fields only -- no score, no band.
   * -> {students:[{roll_no,name,dept,year,section,status,current_mentor_name}]}
   */
  lookup: (q, { limit = 10, ...opts } = {}) =>
    get(`/directory/lookup${query({ q, limit })}`, opts),

  /** Take responsibility for an existing student. -> {roll_no,name,mentor_id,...} */
  add: (rollNo, reason) => post('/mentor/caseload', { roll_no: rollNo, reason: reason ?? '' }),

  /** Hand a student back. The student is retained. -> {..., student_retained:true} */
  drop: (rollNo) => del(`/mentor/caseload/${encodeURIComponent(rollNo)}`),

  /** -> {roll_no, history:[{mentor_id,assigned_at,status,ended_at,end_reason}]} */
  history: (rollNo, opts) => get(`/assignments/${encodeURIComponent(rollNo)}`, opts),
};

/* --------------------------------------------------------- institution ---- */
/** HOD / principal / admin only. A mentor gets 403. */
export const institution = {
  /** -> {totals:{students,scored,high,medium,low,rising,unassigned},
   *      departments:[...], cohorts:[...], mentors:[...]} */
  overview: (opts) => get('/institution', opts),

  /** -> {mentors:[{mentor_id,mentor_name,caseload,high,medium,rising,departments,cohorts,open_interventions}]} */
  mentors: (opts) => get('/institution/mentors', opts),

  /** Drill into one mentor's caseload. */
  caseloadOf: (mentorId, opts) =>
    get(`/institution/caseload/${encodeURIComponent(mentorId)}`, opts),

  /** Assign or reassign any student to any mentor. */
  assign: (rollNo, mentorId, reason) =>
    post('/assignments', { roll_no: rollNo, mentor_id: mentorId, reason: reason ?? '' }),
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
  interventions, dataOps, caseload, institution,
};
