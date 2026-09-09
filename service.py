"""
Sahay - service layer.

All business logic and persistence. Deliberately free of any FastAPI import so
you can unit-test it with plain python, and so main.py stays a thin router.

    python service.py     # rebuilds sahay.db from demo_data/ and self-tests
"""

import copy
import csv
import json
import math
import os
from datetime import date, datetime, timedelta

import database
import jsonsafe

import auth
import lifecycle

# ml is optional, because the deployed API does not ship it.
#
# scikit-learn, xgboost, shap and scipy come to roughly 220 MB, and a Vercel
# Python function is capped at 250 MB unzipped -- so the model libraries cannot
# be installed on the server. They stay in requirements-ml.txt and are used on
# your own machine, where `python ml.py` trains and `python service.py`
# refreshes; refresh_scores writes each student's probability into
# risk_snapshots.model_pct. The deployed API then reads that stored number out
# of the database instead of recomputing it (see _stored_model_result).
#
# What is lost is only live recomputation: the attendance forecast on the
# student page, and the model's delta in what-if. Both were already optional --
# every call site guards on `if b:` -- so they simply do not appear.
try:
    import ml
except ImportError:                     # pragma: no cover - depends on install
    ml = None
import outcomes
from ingest import ingest_file
from risk_engine import (DEFAULT_CONFIG, PLAYBOOKS, WHAT_IF_FIELDS, apply_changes,
                         build_worklist, current_levers, detect_cohort_anomalies,
                         intervention_effectiveness, minimum_change_to, risk_delta,
                         score_student, suggest_playbook)
from risk_engine import what_if as what_if_engine

BASE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(BASE, "demo_data")

# Only the institution-wide roles are fixed. Section mentors are generated from
# the data, one per section, because a mentor with 4,883 mentees is not a
# mentor. A real caseload is about forty, and the whole product argument rests
# on a worklist a person can actually work through.
# There is one role in this system: mentor. Every account belongs to a person
# who is responsible for a caseload of students and answers for it.
STAFF = {}
MENTOR_TITLES = ["Dr.", "Prof.", "Dr.", "Prof.", "Dr."]
MENTOR_SURNAMES = ["Rao", "Iyer", "Fernandes", "Menon", "Kulkarni", "Bose", "Nair",
                   "Sharma", "Reddy", "Pillai", "Joshi", "Das", "Shetty", "Gupta",
                   "Banerjee", "Kamath", "Hegde", "Mehta", "Chopra", "Vaidya"]
MENTOR_INITIALS = list("SMKRAPVNTGLBDJH")

# Kept so existing callers keep working; refreshed from the database on load.
MENTORS = dict(STAFF)


def load_mentors(con):
    """Refresh MENTORS from the database. Called after seeding and at startup."""
    global MENTORS
    m = dict(STAFF)
    try:
        for r in con.execute("SELECT id,name,role,dept,year,section FROM mentors "
                             "ORDER BY dept, year, section"):
            m[r["id"]] = {"name": r["name"], "role": r["role"],
                          "scope": (r["dept"], r["year"]),
                          "section": r["section"]}
    except database.PostgresWrapper.Error:
        con.rollback()
    MENTORS = m
    return m


PRIMARY_MENTOR = "mentor"
PRIMARY_MENTOR_NAME = "Dr. K. Fernandes"

# Roles that see the whole institution. A mentor sees their caseload and
# nothing else.
INSTITUTION_ROLES = ("hod", "principal", "admin")


def is_institution_role(role):
    return role in INSTITUTION_ROLES


# ----------------------------------------------------------------------------
# Scope: what a session is allowed to see
# ----------------------------------------------------------------------------
# This is the authorization boundary, and it is deliberately the only way to
# answer the question.
#
# Every scoped endpoint used to take `mentor` from the query string, so the
# scope of a request was whatever the browser said it was. `?mentor=` omitted
# dropped the filter entirely and returned the whole institution; naming
# another mentor returned their caseload. Authentication was enforced and
# authorization was not.
#
# resolve_scope() derives the answer from the verified session instead. A
# mentor is pinned to their own id whatever the request asks for; only an
# institution role may widen to everything or narrow to a named mentor, and
# that is what makes the HOD's mentor drill-down safe.

class ScopeDenied(Exception):
    """A session asked for data outside its authority."""


def resolve_scope(user, requested_mentor=None):
    """The mentor_id to filter on, or None for institution-wide.

    Raises ScopeDenied when a mentor asks for someone else's caseload, rather
    than silently substituting their own -- a request that was refused is
    something the caller should know about.
    """
    role = (user or {}).get("role")
    uid = (user or {}).get("id")
    if is_institution_role(role):
        # HOD/principal/admin: None means the institution, a name means that
        # mentor's caseload. Both are within authority.
        return (requested_mentor or "").strip() or None
    req = (requested_mentor or "").strip()
    if req and req != uid:
        raise ScopeDenied(f"{uid} may not view the caseload of {req}")
    return uid


# ----------------------------------------------------------------------------
# Caseload assignment
# ----------------------------------------------------------------------------
def _sync_student_mentor(con, roll_no):
    """Point students.mentor_id at the active assignment, or NULL if none.

    The denormalised column is what every scoped query and risk_snapshots read,
    so it has to follow the assignment table rather than drift from it.
    """
    row = con.execute("SELECT mentor_id FROM mentor_assignments "
                      "WHERE roll_no=? AND status='active'", (roll_no,)).fetchone()
    con.execute("UPDATE students SET mentor_id=? WHERE roll_no=?",
                (row["mentor_id"] if row else None, roll_no))


def current_assignment(con, roll_no):
    return con.execute(
        "SELECT mentor_id, assigned_at, assigned_by FROM mentor_assignments "
        "WHERE roll_no=? AND status='active'", (roll_no,)).fetchone()


def is_assigned_to(con, roll_no, mentor_id):
    """Authoritative caseload membership check."""
    return con.execute(
        "SELECT 1 FROM mentor_assignments WHERE roll_no=? AND mentor_id=? "
        "AND status='active'", (roll_no, mentor_id)).fetchone() is not None


def may_view_student(con, user, roll_no):
    """Can this session open this student's record?"""
    if is_institution_role((user or {}).get("role")):
        return True
    return is_assigned_to(con, roll_no, (user or {}).get("id"))


def assign_student(con, roll_no, mentor_id, actor, reason=""):
    """Give a student to a mentor, ending any existing assignment first.

    Reassignment is end-then-insert so the history survives: the previous
    mentor's row stays, marked ended, and the new one is active. That is what
    makes "was this student ever mine" answerable, which a single mentor_id
    column could never do.
    """
    roll_no = (roll_no or "").strip().upper()
    s = con.execute("SELECT roll_no, name FROM students WHERE roll_no=?",
                    (roll_no,)).fetchone()
    if not s:
        raise ValueError(f"{roll_no} is not a student at this institution")
    if mentor_id not in MENTORS:
        raise ValueError(f"{mentor_id} is not a mentor")

    prior = current_assignment(con, roll_no)
    if prior and prior["mentor_id"] == mentor_id:
        raise ValueError(f"{s['name']} is already assigned to "
                         f"{MENTORS[mentor_id]['name']}")

    now = datetime.now().isoformat(timespec="seconds")
    if prior:
        con.execute("UPDATE mentor_assignments SET status='ended', ended_at=?, "
                    "ended_by=?, end_reason=? WHERE roll_no=? AND status='active'",
                    (now, actor, reason or "reassigned", roll_no))
    con.execute("INSERT INTO mentor_assignments "
                "(roll_no,mentor_id,assigned_at,assigned_by,status) "
                "VALUES (?,?,?,?,'active')", (roll_no, mentor_id, now, actor))
    _sync_student_mentor(con, roll_no)
    con.execute("UPDATE risk_snapshots SET mentor_id=? WHERE roll_no=?",
                (mentor_id, roll_no))
    audit(con, actor, "caseload_assigned", roll_no,
          f"{s['name']} assigned to {MENTORS[mentor_id]['name']}"
          + (f" (from {MENTORS.get(prior['mentor_id'], {}).get('name', prior['mentor_id'])})"
             if prior else ""))
    con.commit()
    return {"roll_no": roll_no, "name": s["name"], "mentor_id": mentor_id,
            "mentor_name": MENTORS[mentor_id]["name"],
            "previous_mentor_id": prior["mentor_id"] if prior else None,
            "assigned_at": now}


def drop_student(con, roll_no, mentor_id, actor, reason=""):
    """Remove a student from a mentor's caseload. NOT a deletion.

    The student, their attendance, interventions, outcomes, risk history and
    audit trail are all untouched. Only the assignment ends, and even that is
    kept as a row rather than removed, so the institution can see who held the
    case and when.
    """
    roll_no = (roll_no or "").strip().upper()
    s = con.execute("SELECT roll_no, name FROM students WHERE roll_no=?",
                    (roll_no,)).fetchone()
    if not s:
        raise ValueError(f"{roll_no} is not a student at this institution")
    if not is_assigned_to(con, roll_no, mentor_id):
        raise ValueError(f"{s['name']} is not on this caseload")

    now = datetime.now().isoformat(timespec="seconds")
    con.execute("UPDATE mentor_assignments SET status='ended', ended_at=?, "
                "ended_by=?, end_reason=? WHERE roll_no=? AND mentor_id=? "
                "AND status='active'",
                (now, actor, reason or "dropped from caseload", roll_no, mentor_id))
    _sync_student_mentor(con, roll_no)
    con.execute("UPDATE risk_snapshots SET mentor_id=NULL WHERE roll_no=?", (roll_no,))
    audit(con, actor, "caseload_dropped", roll_no,
          f"{s['name']} removed from {MENTORS.get(mentor_id, {}).get('name', mentor_id)}"
          f"'s caseload. Student record retained.")
    con.commit()
    return {"roll_no": roll_no, "name": s["name"], "dropped_from": mentor_id,
            "ended_at": now, "student_retained": True}


def caseload_counts(con):
    """Active caseload size per mentor, for the HOD's mentor view."""
    rows = con.execute(
        "SELECT a.mentor_id, COUNT(*) n FROM mentor_assignments a "
        "WHERE a.status='active' GROUP BY a.mentor_id").fetchall()
    return {r["mentor_id"]: r["n"] for r in rows}


def seed_role_demo(con, sizes=(27, 21, 34, 24, 39, 31), actor="system"):
    """Give the institution an HOD and several mentors with real caseloads.

    The starting state was one account holding all 5,003 students, which is not
    a mentor -- it is an institution. This keeps every student and splits
    RESPONSIBILITY instead of splitting the data: the HOD still sees all 5,003,
    and each mentor sees the twenty-to-forty they answer for.

    Caseloads are sampled across the whole roster rather than by department, so
    a mentor holds a believable mix -- three CSE, four ECE, six MECH -- which is
    what a real pastoral allocation looks like. Sizes differ on purpose;
    identical caseloads look generated.

    Students left unassigned are not a bug. 4,800-odd with no mentor is exactly
    the coverage gap an HOD should be able to see, and institution_overview
    reports it.
    """
    import random as _random
    rng = _random.Random(25102)          # deterministic: same demo every time

    # --- mentors and the HOD ------------------------------------------------
    people = [(PRIMARY_MENTOR, PRIMARY_MENTOR_NAME, "mentor")]
    for i, surname in enumerate(["Rao", "Iyer", "Menon", "Kulkarni", "Bose"], start=2):
        people.append((f"mentor{i}", f"Dr. {MENTOR_INITIALS[i]}. {surname}", "mentor"))
    people.append(("hod", "Prof. S. Deshpande", "hod"))

    con.execute("DELETE FROM mentors")
    con.executebatch(
        "INSERT INTO mentors (id,name,role,dept,year,section) VALUES (?,?,?,?,?,?)",
        [(pid, name, role, None, None, None) for pid, name, role in people])
    con.commit()
    load_mentors(con)

    # --- caseloads ----------------------------------------------------------
    # Only students who can actually be worked with: scoreable, active.
    pool = [r["roll_no"] for r in con.execute(
        "SELECT roll_no FROM students WHERE status IN ('active','enrolled') "
        "ORDER BY roll_no")]
    rng.shuffle(pool)

    con.execute("UPDATE mentor_assignments SET status='ended', ended_at=?, "
                "ended_by=?, end_reason='caseloads rebuilt' WHERE status='active'",
                (datetime.now().isoformat(timespec="seconds"), actor))
    con.execute("UPDATE students SET mentor_id=NULL")

    now = datetime.now().isoformat(timespec="seconds")
    mentors = [p[0] for p in people if p[2] == "mentor"]
    rows, assigned = [], {}
    cursor = 0
    for mid, n in zip(mentors, sizes):
        take = pool[cursor:cursor + n]
        cursor += n
        assigned[mid] = take
        rows += [(r, mid, now, actor, "active") for r in take]

    con.executebatch("INSERT INTO mentor_assignments "
                     "(roll_no,mentor_id,assigned_at,assigned_by,status) "
                     "VALUES (?,?,?,?,?)", rows)
    con.executebatch("UPDATE students SET mentor_id=? WHERE roll_no=?",
                     [(mid, r) for mid, rl in assigned.items() for r in rl])
    con.executebatch("UPDATE risk_snapshots SET mentor_id=? WHERE roll_no=?",
                     [(mid, r) for mid, rl in assigned.items() for r in rl])
    con.execute("UPDATE risk_snapshots SET mentor_id=NULL WHERE roll_no IN "
                "(SELECT roll_no FROM students WHERE mentor_id IS NULL)")
    audit(con, actor, "caseloads_seeded", "institution",
          f"{len(rows)} assignments across {len(mentors)} mentors")
    con.commit()

    # Each mentor needs a few open cases of their own, or the tracking half of
    # the product is invisible to them: the seeded interventions all belonged
    # to students nobody was assigned, so a mentor's strip read "Open
    # interventions 0" beside the HOD's 12 -- true, and useless to demonstrate.
    seeded_iv = 0
    for mid in mentors:
        flagged = con.execute(
            "SELECT r.roll_no, r.primary_driver FROM risk_snapshots r "
            "WHERE r.mentor_id=? AND r.flagged=1 "
            "  AND NOT EXISTS (SELECT 1 FROM interventions i "
            "                  WHERE i.roll_no=r.roll_no AND i.status='open') "
            "ORDER BY r.priority DESC LIMIT 3", (mid,)).fetchall()
        for f in flagged:
            driver = f["primary_driver"] or "attendance_level"
            pb = PLAYBOOKS.get(driver) or PLAYBOOKS["attendance_level"]
            try:
                create_intervention(con, f["roll_no"], mid,
                                    pb["title"], pb["trigger"], pb["draft"],
                                    pb.get("followup_days", 21))
                seeded_iv += 1
            except (ValueError, database.PostgresWrapper.Error):
                con.rollback()

    out = {"mentors": [], "hod": "hod", "open_interventions": seeded_iv,
           "assigned": len(rows), "unassigned": len(pool) - len(rows)}
    for mid in mentors:
        c = caseload_of(con, mid)
        out["mentors"].append({"mentor_id": mid, "name": c["mentor_name"],
                               "caseload": c["count"],
                               "departments": len(c["departments"]),
                               "cohorts": c["cohort_count"]})
    return out


def caseload_of(con, mentor_id):
    """Summary of one mentor's active caseload, with its spread.

    The spread matters: a caseload is not a section. A mentor can hold three
    CSE, four ECE and six MECH students, and the UI should be able to say so
    rather than implying a single cohort.
    """
    rows = con.execute(
        "SELECT s.roll_no, s.name, s.dept, s.year, s.section, s.status "
        "FROM mentor_assignments a JOIN students s ON s.roll_no = a.roll_no "
        "WHERE a.mentor_id=? AND a.status='active' "
        "ORDER BY s.dept, s.year, s.section, s.name", (mentor_id,)).fetchall()
    depts, sections = {}, set()
    for r in rows:
        depts[r["dept"]] = depts.get(r["dept"], 0) + 1
        sections.add(f"{r['dept']} {r['year']}-{r['section']}")
    return {
        "mentor_id": mentor_id,
        "mentor_name": MENTORS.get(mentor_id, {}).get("name", mentor_id),
        "count": len(rows),
        "departments": dict(sorted(depts.items())),
        "cohort_count": len(sections),
        "students": [dict(r) for r in rows],
    }


def directory_lookup(con, q, limit=10):
    """Minimum information needed to identify a student, institution-wide.

    No score, no band, no attendance -- picking the right person needs a name,
    a roll number and a cohort, and nothing else. Whoever currently holds them
    is included because assigning an already-assigned student is a
    reassignment, and the caller should see that before they confirm.
    """
    term = f"%{(q or '').strip()}%"
    rows = con.execute(
        "SELECT s.roll_no, s.name, s.dept, s.year, s.section, s.status, "
        "       s.mentor_id "
        "FROM students s "
        "WHERE s.name ILIKE ? OR s.roll_no ILIKE ? "
        "ORDER BY s.name LIMIT ?", (term, term, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # Popped unconditionally. Inside a conditional expression the pop only
        # ran when the student had a mentor, so an unassigned student kept the
        # raw mentor_id key and the response shape varied row to row.
        mid = d.pop("mentor_id", None)
        d["current_mentor_name"] = MENTORS.get(mid, {}).get("name") if mid else None
        out.append(d)
    return out


def institution_overview(con):
    """The HOD's top level: institution, then departments, then cohorts.

    Aggregated in SQL rather than by shipping 5,000 rows to the browser and
    counting there. The HOD's question is "how is my institution doing", and
    the answer is a few hundred bytes.
    """
    if snapshots_stale(con):
        refresh_scores(con)

    totals = con.execute(
        "SELECT COUNT(*) students, COUNT(r.roll_no) scored, "
        "  COUNT(CASE WHEN r.band='High' THEN 1 END) high, "
        "  COUNT(CASE WHEN r.band='Medium' THEN 1 END) medium, "
        "  COUNT(CASE WHEN r.band='Low' THEN 1 END) low, "
        "  COUNT(CASE WHEN r.delta >= 10 THEN 1 END) rising, "
        "  COUNT(CASE WHEN s.mentor_id IS NULL THEN 1 END) unassigned "
        "FROM students s LEFT JOIN risk_snapshots r ON r.roll_no = s.roll_no"
    ).fetchone()

    depts = [dict(r) for r in con.execute(
        "SELECT s.dept, COUNT(*) students, "
        "  COUNT(CASE WHEN r.band='High' THEN 1 END) high, "
        "  COUNT(CASE WHEN r.band='Medium' THEN 1 END) medium, "
        "  COUNT(CASE WHEN r.delta >= 10 THEN 1 END) rising, "
        "  COUNT(CASE WHEN s.mentor_id IS NOT NULL THEN 1 END) assigned, "
        "  ROUND(AVG(r.score)::numeric, 1)::float8 avg_score "
        "FROM students s LEFT JOIN risk_snapshots r ON r.roll_no = s.roll_no "
        "GROUP BY s.dept ORDER BY s.dept")]

    cohorts = [dict(r) for r in con.execute(
        "SELECT s.dept, s.year, s.section, COUNT(*) students, "
        "  COUNT(CASE WHEN r.band='High' THEN 1 END) high, "
        "  COUNT(CASE WHEN r.delta >= 10 THEN 1 END) rising, "
        "  ROUND(AVG(r.score)::numeric, 1)::float8 avg_score "
        "FROM students s LEFT JOIN risk_snapshots r ON r.roll_no = s.roll_no "
        "GROUP BY s.dept, s.year, s.section "
        "ORDER BY COUNT(CASE WHEN r.band='High' THEN 1 END) DESC, s.dept "
        "LIMIT 60")]

    return {
        "totals": dict(totals),
        "departments": depts,
        "cohorts": cohorts,
        "mentors": mentor_overview(con),
    }


def mentor_overview(con):
    """Caseload and risk load per mentor, for the HOD's mentor drill-down."""
    rows = con.execute(
        "SELECT a.mentor_id, COUNT(*) caseload, "
        "  COUNT(CASE WHEN r.band='High' THEN 1 END) high, "
        "  COUNT(CASE WHEN r.band='Medium' THEN 1 END) medium, "
        "  COUNT(CASE WHEN r.delta >= 10 THEN 1 END) rising, "
        "  COUNT(DISTINCT s.dept) departments, "
        "  COUNT(DISTINCT s.dept || s.year || s.section) cohorts "
        "FROM mentor_assignments a "
        "JOIN students s ON s.roll_no = a.roll_no "
        "LEFT JOIN risk_snapshots r ON r.roll_no = a.roll_no "
        "WHERE a.status='active' GROUP BY a.mentor_id").fetchall()
    # Counted through the student's assignment, not through interventions.mentor.
    # That column stores the mentor's display NAME ("Dr. K. Fernandes"), not
    # their id, so grouping by it and looking the result up by mentor_id
    # matched nothing and every mentor showed 0 open cases. Going via
    # students.mentor_id also means a reassigned student's open case follows
    # them to the new mentor, which is the behaviour that matches "who is
    # responsible for this now".
    open_iv = {r["mentor_id"]: r["n"] for r in con.execute(
        "SELECT s.mentor_id, COUNT(*) n FROM interventions i "
        "JOIN students s ON s.roll_no = i.roll_no "
        "WHERE i.status='open' AND s.mentor_id IS NOT NULL "
        "GROUP BY s.mentor_id")}
    out = []
    for r in rows:
        d = dict(r)
        d["mentor_name"] = MENTORS.get(d["mentor_id"], {}).get("name", d["mentor_id"])
        d["open_interventions"] = open_iv.get(d["mentor_id"], 0)
        out.append(d)
    return sorted(out, key=lambda x: (-x["high"], -x["caseload"]))


def assignment_history(con, roll_no):
    """Every mentor this student has had, newest first."""
    return [dict(r) for r in con.execute(
        "SELECT mentor_id, assigned_at, assigned_by, status, ended_at, ended_by, "
        "end_reason FROM mentor_assignments WHERE roll_no=? "
        "ORDER BY id DESC", ((roll_no or "").strip().upper(),))]


def _seed_mentors(con):
    """One mentor, holding the whole caseload.

    A cohort this size is exactly what a single mentor is meant to carry, which
    is the point of capping the weekly worklist and routing section-wide drops
    out of it. The per-section scoping in get_worklist / get_dashboard is
    unchanged and still keys off mentor_id, so adding a second mentor and
    splitting the roster needs no code change.
    """
    con.execute("DELETE FROM mentors")
    con.execute("INSERT INTO mentors (id,name,role,dept,year,section) "
                "VALUES (?,?,?,?,?,?)",
                (PRIMARY_MENTOR, PRIMARY_MENTOR_NAME, "mentor", None, None, None))
    con.execute("UPDATE students SET mentor_id=?", (PRIMARY_MENTOR,))
    con.commit()
    load_mentors(con)
    return 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
  roll_no TEXT PRIMARY KEY, name TEXT, dept TEXT, year INT, section TEXT,
  gender TEXT, category TEXT, first_gen INT, hostel INT,
  ia1 REAL, ia2 REAL, ia3 REAL, cgpa REAL, ia_max INT DEFAULT 30,
  backlogs INT, submission_pct REAL, fee_status TEXT,
  mentor_id TEXT, last_contact_at TEXT,
  status TEXT DEFAULT 'active', admitted_on TEXT, term_start TEXT,
  weeks_of_data INT DEFAULT 0
);
CREATE TABLE IF NOT EXISTS attendance (
  roll_no TEXT, week_index INT, week_start TEXT, pct REAL,
  attended INT, held INT,
  PRIMARY KEY (roll_no, week_index)
);
CREATE TABLE IF NOT EXISTS interventions (
  id TEXT PRIMARY KEY, roll_no TEXT, mentor TEXT, trigger TEXT, playbook TEXT,
  action_text TEXT, approved_by_mentor INT DEFAULT 0,
  created_at TEXT, followup_at TEXT, status TEXT, outcome TEXT, notes TEXT,
  baseline_week INT, baseline_value REAL, measured_value REAL,
  measured_change REAL, measured_outcome TEXT, measured_at TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
  id SERIAL PRIMARY KEY, roll_no TEXT, mentor TEXT,
  verdict TEXT, reason TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id SERIAL PRIMARY KEY, actor TEXT, action TEXT,
  subject TEXT, detail TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS uploads (
  id SERIAL PRIMARY KEY, filename TEXT, actor TEXT,
  rows_keyed INT, rows_unmatched INT, report TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS risk_snapshots (
  roll_no TEXT PRIMARY KEY, score INT, band TEXT, delta INT, priority REAL,
  confidence REAL, headline TEXT, primary_driver TEXT, stage TEXT,
  model_pct REAL, anomaly INT, cohort TEXT, guardrail TEXT,
  dept TEXT, year INT, section TEXT, mentor_id TEXT, name TEXT, computed_at TEXT,
  flagged INT DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mentors (
  id TEXT PRIMARY KEY, name TEXT, role TEXT, dept TEXT, year INT, section TEXT
);
-- Who is responsible for whom, and who has been.
--
-- students.mentor_id alone cannot answer "was this student ever mine", cannot
-- record who made the assignment, and has nowhere to put a reassignment. It
-- stays as the denormalised current-owner cache, because risk_snapshots and
-- every scoped query already read it and an indexed single-column filter is
-- what keeps the worklist flat at institution size -- but this table is the
-- record of truth and _sync_student_mentor() below keeps the two in step.
--
-- A student may have many rows here and at most one with status='active';
-- ux_assign_active_student enforces that, so REASSIGN is end-then-insert and
-- cannot silently leave a student with two mentors.
CREATE TABLE IF NOT EXISTS mentor_assignments (
  id SERIAL PRIMARY KEY,
  roll_no TEXT NOT NULL,
  mentor_id TEXT NOT NULL,
  assigned_at TEXT NOT NULL,
  assigned_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  ended_at TEXT,
  ended_by TEXT,
  end_reason TEXT
);
CREATE TABLE IF NOT EXISTS effectiveness_cache (
  stamp TEXT PRIMARY KEY, payload TEXT, computed_at TEXT
);
CREATE TABLE IF NOT EXISTS cohort_alerts (
  cohort TEXT PRIMARY KEY, payload TEXT, computed_at TEXT
);
-- Indexes last: every referenced table must already exist.
CREATE INDEX IF NOT EXISTS ix_students_mentor ON students(mentor_id);
CREATE INDEX IF NOT EXISTS ix_students_dept ON students(dept, year, section);
CREATE INDEX IF NOT EXISTS ix_students_status ON students(status);
CREATE INDEX IF NOT EXISTS ix_att_roll ON attendance(roll_no, week_index);
CREATE INDEX IF NOT EXISTS ix_iv_roll ON interventions(roll_no);
CREATE INDEX IF NOT EXISTS ix_iv_status ON interventions(status);
CREATE INDEX IF NOT EXISTS ix_snap_mentor ON risk_snapshots(mentor_id, priority DESC);
CREATE INDEX IF NOT EXISTS ix_snap_band ON risk_snapshots(band);
CREATE INDEX IF NOT EXISTS ix_snap_flagged ON risk_snapshots(flagged, priority DESC);
CREATE INDEX IF NOT EXISTS ix_students_weeks ON students(weeks_of_data);
-- At most one active mentor per student. A partial unique index rather than
-- application logic, so a concurrent double-assign fails in the database
-- instead of quietly producing a student with two owners.
CREATE UNIQUE INDEX IF NOT EXISTS ux_assign_active_student
  ON mentor_assignments(roll_no) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS ix_assign_mentor
  ON mentor_assignments(mentor_id, status);
CREATE INDEX IF NOT EXISTS ix_assign_roll ON mentor_assignments(roll_no);
"""


# CGPA band by backlogs carried forward. Mirrors generate_demo_data.cgpa_for so
# a migrated database and a freshly generated one agree.
CGPA_BANDS = ((0, 8.0, 9.0), (2, 7.0, 8.0), (99, 5.0, 6.0))


def cgpa_for(backlogs, engagement):
    """engagement: 0..1, positions the student inside their band."""
    b = backlogs or 0
    lo, hi = next((lo, hi) for cap, lo, hi in CGPA_BANDS if b <= cap)
    pos = min(1.0, max(0.0, engagement if engagement is not None else 0.5))
    return round(lo + pos * (hi - lo), 2)


def _backfill_cgpa(con):
    """Derive CGPA for rows that predate the column, from backlogs + attendance."""
    eng = {r["roll_no"]: (r["m"] or 0) / 100.0 for r in con.execute(
        "SELECT roll_no, AVG(pct) m FROM attendance GROUP BY roll_no")}
    rows = [(cgpa_for(r["backlogs"], eng.get(r["roll_no"], 0.5)), r["roll_no"])
            for r in con.execute("SELECT roll_no, backlogs FROM students "
                                 "WHERE cgpa IS NULL")]
    if rows:
        con.executemany("UPDATE students SET cgpa=? WHERE roll_no=?", rows)
        con.commit()
    return len(rows)


def _migrate(con):
    """Additive migrations for databases created before a column existed.

    SCHEMA only runs on a full reset, so an existing sahay.db would otherwise
    crash on the first query that names a newer column.
    """
    try:
        # Information schema query for Postgres
        cols = {r["column_name"] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'risk_snapshots'")}
    except database.PostgresWrapper.Error:
        con.rollback()
        return
    try:
        scols = {r["column_name"] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'students'")}
    except database.PostgresWrapper.Error:
        con.rollback()
        scols = set()
    if scols and "cgpa" not in scols:
        con.execute("ALTER TABLE students ADD COLUMN cgpa REAL")
        con.commit()
        # One-off, for rows that predate the column. Deliberately NOT run on
        # every connect: a student admitted today has no prior semester, so
        # deriving a CGPA for them would invent an academic record.
        _backfill_cgpa(con)

    if cols and "flagged" not in cols:
        con.execute("ALTER TABLE risk_snapshots ADD COLUMN flagged INT DEFAULT 0")
        # Old rows were worklist-only, so every one of them was flagged work.
        con.execute("UPDATE risk_snapshots SET flagged=1")
        con.execute("CREATE INDEX IF NOT EXISTS ix_snap_flagged "
                    "ON risk_snapshots(flagged, priority DESC)")
        con.commit()


def init_schema(con):
    """Create tables, run migrations, cache the mentor roster.

    Split out of connect() so the web app can do it exactly once at startup.
    It used to run on every connect(), which was harmless when there was a
    single process-wide connection but is pure waste -- a full DDL script plus
    a migration probe plus a roster query -- once a connection is checked out
    per request.
    """
    con.executescript(SCHEMA)
    con.commit()
    _migrate(con)
    load_mentors(con)
    return con


def connect():
    """A ready-to-use connection, schema included.

    This is the entry point for the CLI scripts (service.py, check.py, ml.py),
    which each want one connection they own outright. The web app instead
    initialises the schema once at startup and then checks connections out of
    a pool per request -- see database.checkout() and main.get_con().
    """
    return init_schema(database.connect())


def audit(con, actor, action, subject="", detail=""):
    con.execute("INSERT INTO audit (actor,action,subject,detail,at) VALUES (?,?,?,?,?)",
                (actor, action, subject, detail, datetime.now().isoformat(timespec="seconds")))


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
def get_config(con):
    row = con.execute("SELECT v FROM settings WHERE k='config'").fetchone()
    return json.loads(row["v"]) if row else DEFAULT_CONFIG


def validate_config(cfg):
    """Merge a partial config onto the defaults, rejecting anything unknown.

    Returns a complete config. Raises ValueError with a specific message.

    The rules are deliberately strict, because risk_engine indexes this
    structure directly rather than using .get() -- so a config that is merely
    incomplete is indistinguishable from a config that is wrong, and both take
    out every scoring endpoint.
    """
    if not isinstance(cfg, dict):
        raise ValueError("config must be an object")

    merged = copy.deepcopy(DEFAULT_CONFIG)
    for section, values in cfg.items():
        if section not in merged:
            raise ValueError(f"unknown config section: {section!r}")
        default = merged[section]
        if not isinstance(default, dict):
            merged[section] = values
            continue
        if not isinstance(values, dict):
            raise ValueError(f"{section} must be an object")
        for key, val in values.items():
            if key not in default:
                raise ValueError(f"unknown config key: {section}.{key}")
            ref = default[key]
            if isinstance(ref, bool):
                if not isinstance(val, bool):
                    raise ValueError(f"{section}.{key} must be true or false")
            elif isinstance(ref, (int, float)):
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValueError(f"{section}.{key} must be a number")
                if not math.isfinite(val):
                    raise ValueError(f"{section}.{key} must be a finite number")
                if val < 0:
                    raise ValueError(f"{section}.{key} must not be negative")
                if key.endswith("_pct") and val > 100:
                    raise ValueError(f"{section}.{key} must be between 0 and 100")
            merged[section][key] = val

    b = merged["bands"]
    if b["medium_at"] >= b["high_at"]:
        raise ValueError("bands.medium_at must be less than bands.high_at")
    return merged


def set_config(con, cfg, actor="admin"):
    con.execute("INSERT INTO settings (k,v) VALUES ('config',?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (jsonsafe.dumps(cfg),))
    audit(con, actor, "config_changed", "config", jsonsafe.dumps(cfg)[:400])
    con.commit()
    return cfg


# ----------------------------------------------------------------------------
# Seeding
# ----------------------------------------------------------------------------
def _assign_mentor(dept, year, section="A"):
    """The mentor for a section, creating one if the section is new."""
    return f"m{str(dept).lower()}{year}{str(section).lower()}"


def _cgpa_from_row(r):
    """CGPA from master.csv, or derived if the file predates the column."""
    if r.get("cgpa"):
        return float(r["cgpa"])
    weeks = [float(r[k]) for k in r if k.startswith("w") and k[1:].isdigit() and r[k]]
    eng = (sum(weeks) / len(weeks) / 100.0) if weeks else 0.5
    return cgpa_for(int(r["backlogs"]), eng)


def reset_db(actor="admin"):
    """Rebuild everything from demo_data/. This is the Reset Demo button."""
    con = connect()
    # Postgres doesn't have executescript by default with ?, but our wrapper supports it.
    # However we need to drop all tables first to truly reset.
    con.executescript("""
        DROP TABLE IF EXISTS students, attendance, interventions, feedback, audit, settings, uploads, risk_snapshots, mentors, mentor_assignments, effectiveness_cache, cohort_alerts CASCADE;
    """ + SCHEMA)

    weeks = {}
    with open(os.path.join(DEMO, "week_starts.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            weeks[int(r["week_index"])] = r["week_start"]

    # Every insert below is batched rather than issued row by row. The previous
    # version ran one round trip per student and per intervention -- around
    # 5,600 of them, plus 130,000 attendance rows -- inside a single
    # transaction. On a local SQLite file that was instant. Against a remote
    # Postgres at even 50ms latency it is well over an hour, which is why the
    # Reset Demo button appeared to hang and Supabase eventually cancelled the
    # statement.
    n = 0
    student_rows, attendance_rows = [], []
    with open(os.path.join(DEMO, "master.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            year = int(r["year"])
            student_rows.append(
                (r["roll_no"], r["name"], r["dept"], year, r["section"], r["gender"],
                 r["category"], int(r["first_gen"]), int(r["hostel"]),
                 float(r["ia1"]) if r["ia1"] else None,
                 float(r["ia2"]) if r["ia2"] else None,
                 float(r["ia3"]) if r["ia3"] else None,
                 int(r["backlogs"]), float(r["submission_pct"]), r["fee_status"],
                 _cgpa_from_row(r),
                 _assign_mentor(r["dept"], year, r["section"]), None))
            for i in range(26):
                pct = float(r[f"w{i:02d}"])
                attendance_rows.append(
                    (r["roll_no"], i, weeks[i], pct, int(round(pct / 100 * 40)), 40))
            n += 1

    con.executebatch(
        "INSERT INTO students (roll_no,name,dept,year,section,gender,category,"
        "first_gen,hostel,ia1,ia2,ia3,backlogs,submission_pct,fee_status,cgpa,"
        "mentor_id,last_contact_at,status,admitted_on,term_start) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active','2025-07-07','2025-07-07')",
        student_rows)
    con.commit()

    con.executebatch("INSERT INTO attendance (roll_no,week_index,week_start,"
                     "pct,attended,held) VALUES (?,?,?,?,?,?)", attendance_rows,
                     page_size=1000)
    con.commit()

    with open(os.path.join(DEMO, "interventions_seed.csv"), encoding="utf-8") as f:
        iv_rows = [(r["id"], r["roll_no"], r["mentor"], r["trigger"], r["playbook"],
                    "", 1, r["created_at"], r["followup_at"], r["status"], r["outcome"])
                   for r in csv.DictReader(f)]
    con.executebatch(
        "INSERT INTO interventions (id,roll_no,mentor,trigger,playbook,"
        "action_text,approved_by_mentor,created_at,followup_at,status,outcome)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)", iv_rows)
    # One set-based UPDATE instead of one per intervention.
    con.execute("UPDATE students SET last_contact_at = sub.latest FROM ("
                "  SELECT roll_no, MAX(created_at) AS latest FROM interventions"
                "  GROUP BY roll_no) AS sub "
                "WHERE students.roll_no = sub.roll_no "
                "AND (students.last_contact_at IS NULL "
                "     OR students.last_contact_at < sub.latest)")
    con.commit()

    con.execute("UPDATE students SET weeks_of_data = (SELECT COUNT(*) FROM attendance a "
                "WHERE a.roll_no = students.roll_no)")
    set_config(con, DEFAULT_CONFIG, actor)
    audit(con, actor, "demo_reset", "database", f"{n} students seeded")
    con.commit()
    # Work out from the attendance data whether each seeded intervention helped,
    # so the effectiveness screen is measured rather than asserted from day one.
    _seed_mentors(con)
    m = outcomes.measure_all(con, audit_fn=audit, actor=actor)
    joined = _seed_recent_admissions(con, actor)

    # Rebuild the ROLES, not one mentor holding the institution.
    #
    # This used to end with _seed_mentors(), which creates a single account and
    # runs `UPDATE students SET mentor_id = 'mentor'` across all 5,003 rows. A
    # demo reset therefore undid the whole role model: no HOD, no caseloads,
    # one mentor owning everything, and mentor_assignments left holding rows
    # for students that had just been dropped and recreated. seed_role_demo
    # rebuilds the HOD, the six mentors and their 20-40 student caseloads, and
    # is safe to call here because refresh_scores has already run above.
    roles = seed_role_demo(con, actor=actor)
    n_mentors = len(roles["mentors"])
    load_mentors(con)
    # Accounts last, so every mentor that now exists has a login. seed_users
    # gives new accounts auth.DEMO_PASSWORD with must_change_password=0, so an
    # account that appears here is an account that can actually be used.
    auth.seed_users(con, MENTORS)
    con.execute("UPDATE students SET weeks_of_data = (SELECT COUNT(*) FROM attendance a "
                "WHERE a.roll_no = students.roll_no)")
    con.commit()
    return {"students": n, "outcomes_measured": m["measured"],
            "outcome_breakdown": m["breakdown"], "recent_admissions": joined,
            "mentors": n_mentors}


def _seed_recent_admissions(con, actor="system"):
    """A handful of students who joined recently, with partial history.

    Without these every seeded student has a full term of data and the three
    scoring stages are invisible. The demo needs at least one student in each.
    """
    from datetime import date as _d
    # One per stage, no more: at a 14-student demo size six of these would be
    # nearly half the cohort. 0 weeks -> onboarding, 6 -> rules_only, 14 -> full.
    plan = [("Meera Nair", "CSE", 1, 0, "F"),
            ("Kavya Reddy", "ECE", 1, 6, "F"),
            ("Rahul Menon", "MEC", 1, 14, "M")]
    made = []
    for name, dept, year, weeks, gender in plan:
        try:
            start = _d(2025, 7, 7)
            st = lifecycle.admit(con, name=name, dept=dept, year=year, section="A",
                                 admission_date=start.isoformat(), actor=actor)
        except ValueError:
            continue
        pct = 88.0
        for w in range(weeks):
            pct = max(35.0, pct - (2.6 if w > 5 else 0.4))
            con.execute("INSERT INTO attendance (roll_no,week_index,week_start,pct,"
                        "attended,held) VALUES (?,?,?,?,?,?) ON CONFLICT"
                        "(roll_no,week_index) DO UPDATE SET pct=EXCLUDED.pct",
                        (st["roll_no"], w,
                         (start + timedelta(weeks=w)).isoformat(),
                         round(pct, 1), int(round(pct / 100 * 40)), 40))
        con.execute("UPDATE students SET status=?, weeks_of_data=?, gender=? "
                    "WHERE roll_no=?",
                    ("active" if weeks else "enrolled", weeks, gender, st["roll_no"]))
        made.append({"roll_no": st["roll_no"], "name": name, "weeks": weeks,
                     "stage": lifecycle.stage_for(weeks)})
    con.commit()
    return made


# ----------------------------------------------------------------------------
# Feature assembly
# ----------------------------------------------------------------------------
def _features(con, roll_no):
    s = con.execute("SELECT * FROM students WHERE roll_no=?", (roll_no,)).fetchone()
    if not s:
        return None, None
    att = con.execute("SELECT pct FROM attendance WHERE roll_no=? ORDER BY week_index",
                      (roll_no,)).fetchall()
    f = {"weekly_attendance": [a["pct"] for a in att],
         "ia1": s["ia1"], "ia2": s["ia2"], "ia3": s["ia3"],
         "ia1_max": s["ia_max"], "ia2_max": s["ia_max"], "ia3_max": s["ia_max"],
         "backlogs": s["backlogs"], "submission_pct": s["submission_pct"],
         "fee_status": s["fee_status"]}
    return dict(s), f


def _weeks_since_contact(last_contact_at, weekly_len=26):
    if not last_contact_at:
        return 8
    try:
        d = datetime.fromisoformat(last_contact_at).date()
    except ValueError:
        return 8
    return max(0, (date(2025, 7, 7) + timedelta(weeks=weekly_len) - d).days // 7)


def _cohort(con, mentor_id=None):
    """Everything the engine needs, for the slice this user is allowed to see."""
    q = "SELECT * FROM students"
    args = ()
    # mentor_id here has already been through resolve_scope(), which is the
    # authorization boundary: None means an institution role asked for the
    # whole institution, a value means that caseload. Re-checking the role
    # here would let a request that resolve_scope refused slip through as
    # unscoped, which is exactly how `?mentor=` omitted returned all 5,003.
    if mentor_id:
        q += " WHERE mentor_id=?"
        args = (mentor_id,)
    rows = con.execute(q, args).fetchall()

    att = {}
    for a in con.execute("SELECT roll_no, pct FROM attendance ORDER BY roll_no, week_index"):
        att.setdefault(a["roll_no"], []).append(a["pct"])

    out = []
    for s in rows:
        out.append({
            "roll_no": s["roll_no"], "name": s["name"], "dept": s["dept"],
            "year": s["year"], "section": s["section"],
            "weeks_since_contact": _weeks_since_contact(s["last_contact_at"]),
            "features": {
                "weekly_attendance": att.get(s["roll_no"], []),
                "ia1": s["ia1"], "ia2": s["ia2"], "ia3": s["ia3"],
                "ia1_max": s["ia_max"], "ia2_max": s["ia_max"], "ia3_max": s["ia_max"],
                "backlogs": s["backlogs"], "submission_pct": s["submission_pct"],
                "fee_status": s["fee_status"],
            },
        })
    return out


# ----------------------------------------------------------------------------
# Public API used by main.py
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# The AI layer: rules and model together, with disagreement made visible
# ----------------------------------------------------------------------------
_BUNDLE = None
_BUNDLE_MTIME = None


def get_model():
    """Load the model, and notice if it appears or changes on disk.

    The previous version cached "no model" permanently. If the server was
    started before ml.py had finished writing model.joblib, it stayed in Rules
    Mode until someone restarted it, and the student page quietly showed the
    stage message instead of a prediction with no clue why. Checking the file's
    timestamp costs nothing and also picks up a retrained model.
    """
    global _BUNDLE, _BUNDLE_MTIME
    if ml is None:
        return None
    try:
        mtime = os.path.getmtime(ml.MODEL_PATH)
    except OSError:
        # No model.joblib on this host. THIS is the branch the deployed
        # function takes, not the `ml is None` one above: ml.py's only
        # module-level third-party import is numpy, which pandas already
        # installs, so `import ml` succeeds in the deployed API and every
        # heavy import sits inside a function.
        #
        # Four separate call sites tested `ml is None` to mean "the deployed
        # host", and all four were therefore wrong there: /api/model claimed
        # nothing had been trained, the student page said the same, the stored
        # prediction was never read, and refresh_scores overwrote all 5,001
        # stored predictions with NULL. The honest question is the return
        # value of this function -- can this host compute a prediction -- and
        # callers should ask that, not guess from the import.
        _BUNDLE, _BUNDLE_MTIME = None, None
        return None
    if _BUNDLE is None or mtime != _BUNDLE_MTIME:
        try:
            _BUNDLE = ml.load_bundle()
            _BUNDLE_MTIME = mtime
        except Exception:
            _BUNDLE, _BUNDLE_MTIME = None, None
    return _BUNDLE


REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "model_report.json")


def _model_report():
    """The training report, read without importing the model libraries.

    model_report.json is plain JSON written by ml.py, so it can be read
    anywhere -- including the deployed API, which has no scikit-learn. That
    lets /api/model keep reporting what the model actually is and how it
    scored, instead of claiming no model was ever trained.
    """
    try:
        with open(REPORT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def model_evaluation():
    """The Logistic Regression / Random Forest / XGBoost comparison.

    Read out of model_report.json, which ml.py --evaluate writes and which
    travels with the deployment. Nothing is computed here: the numbers come
    from real predictions on a held-out fold at training time, and this host
    has no scikit-learn to recompute them with even if it wanted to.

    Returns available:False rather than an empty table when the evaluation has
    not been run, so the UI can say which it is instead of rendering zeros.
    """
    report = _model_report() or {}
    ev = report.get("model_evaluation")
    if not ev:
        return {"available": False,
                "reason": "No model comparison has been run yet.",
                "hint": "python ml.py --evaluate"}
    return {"available": True, **ev}


def _stored_prediction_count(con):
    """How many students currently have a stored model prediction."""
    try:
        return con.execute("SELECT COUNT(model_pct) c FROM risk_snapshots"
                           ).fetchone()["c"]
    except database.PostgresWrapper.Error:
        return None


def _stored_model_result(con, roll_no):
    """This student's prediction as of the last refresh, from the database.

    refresh_scores writes model_pct into risk_snapshots wherever it runs. When
    the model libraries are absent we serve that stored number rather than
    dropping the prediction entirely.

    probability is derived back from the stored percentage, which ml.predict
    clamps to [1%, 99%] for display. Inside that range the round-trip is exact;
    outside it the true probability was more extreme than what is shown. Only
    the two disagreement thresholds in _hybrid read it, and both sit well
    inside the clamp, so the reconstruction is safe for that use.
    """
    row = con.execute("SELECT model_pct, anomaly FROM risk_snapshots WHERE roll_no=?",
                      (roll_no,)).fetchone()
    if not row or row["model_pct"] is None:
        return {"available": False,
                "reason": "No stored prediction for this student yet. Run "
                          "`python service.py` to refresh the scores."}
    pct = float(row["model_pct"])
    report = _model_report() or {}
    out = {
        "available": True,
        "probability": round(pct / 100.0, 4),
        "percent": round(pct, 1),
        "horizon_weeks": (report.get("task") or {}).get("horizon_weeks"),
        # Matches ml._statement's wording deliberately. "About X% likely to
        # fall further within the next 6 weeks" claimed both a calibrated
        # probability and a horizon, and the model has neither: its label
        # carries no time dimension, and on this cohort it averages ~40% where
        # the observed six-week rate is ~2%. It ranks well (ROC AUC 0.91
        # against a real six-week outcome); it does not quantify.
        "statement": "How much this student's attendance pattern resembles "
                     "those the model was trained to flag. Use it to rank who "
                     "to contact first; it is not a probability.",
        "computed": "stored",
        "note": "Served from the last scoring run rather than recomputed now.",
    }
    # The anomaly flag is stored alongside the probability, so the "unusual
    # pattern" badge should survive too. Without this the stored path silently
    # dropped it and the same student showed the badge on a dev machine and not
    # in production. Shaped like ml.predict's `anomaly` so the UI needs no
    # special case; the isolation-forest score itself is not stored, and no
    # caller reads it.
    if row["anomaly"]:
        out["anomaly"] = {
            "unusual": True,
            "note": "Pattern unlike the rest of the cohort. Worth a look even "
                    "if no rule fired.",
        }
    return out


def model_status(con):
    b = get_model()
    if not b:
        report = _model_report()
        if report:
            # A trained model exists and its report travels with the repo, but
            # this host cannot load it -- the deployed function carries neither
            # the 220 MB of libraries nor model.joblib. Report that honestly:
            # the predictions on the student pages are real, they were computed
            # elsewhere and stored.
            #
            # This used to be `if ml is None and report:`, which never fired on
            # the deployed host. ml.py's only module-level third-party import is
            # numpy, which pandas already installs, so `import ml` SUCCEEDS
            # there -- every heavy import is inside a function. `ml` was
            # therefore not None, this branch was skipped, and production
            # reported "Rules Mode -- no model file" about a trained xgboost
            # that beats both baselines. Whether the libraries imported was
            # never the question; whether a trained model exists is.
            return {"trained": True, "mode": "Rules + Model (stored)",
                    "kind": report.get("shipped_model"),
                    "horizon_weeks": report["task"]["horizon_weeks"],
                    "target": report["task"]["target"],
                    "target_is_not": report["task"]["target_is_not"],
                    "excluded_features": report["task"]["excluded_features"],
                    "beats_baselines": report["model_beats_baselines"],
                    "beats_rules_ledger": report.get("beats_rules_ledger"),
                    "beats_persistence_baseline": report.get("beats_persistence_baseline"),
                    "early_warning_gain_over_rules":
                        report.get("early_warning_pr_auc_gain_over_rules"),
                    "early_warning": report.get("early_warning"),
                    "overall": report["overall"],
                    "data": report["data"],
                    # Counted, not asserted. "Predictions are stored" is a
                    # claim about the database, and for a while it was false
                    # while this endpoint went on making it.
                    "stored_predictions": _stored_prediction_count(con),
                    "why": "Predictions are computed during scoring and stored, "
                           "because the model libraries are too large to deploy "
                           "as a serverless function."}
        return {"trained": False, "mode": "Rules Mode",
                "why": "No model has been trained yet. Threshold rules only, "
                       "which is the correct state before an institution has "
                       "enough history."}
    r = b["report"]
    return {"trained": True, "mode": "Rules + Model",
            "kind": b["kind"], "horizon_weeks": b["horizon"],
            "target": r["task"]["target"],
            "target_is_not": r["task"]["target_is_not"],
            "excluded_features": r["task"]["excluded_features"],
            "beats_baselines": r["model_beats_baselines"],
            "beats_rules_ledger": r.get("beats_rules_ledger"),
            "beats_persistence_baseline": r.get("beats_persistence_baseline"),
            "early_warning_gain_over_rules": r.get("early_warning_pr_auc_gain_over_rules"),
            "early_warning": r.get("early_warning"),
            "overall": r["overall"],
            "data": r["data"]}


# Plain-language review thresholds. CGPA is not part of the risk ledger, so it
# has no entry in DEFAULT_CONFIG; the rest read from the live config so the
# review can never contradict the score shown beside it.
CGPA_LOW = 6.0
CGPA_WEAK = 7.0
CGPA_GOOD = 8.0


def _join_plain(parts):
    """a / a and b / a, b and c -- read aloud without sounding like a list."""
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def plain_review(student, features, cfg):
    """A mentor-readable account of what is actually wrong with this student.

    SHAP contributions answer "which input moved the model", which is the wrong
    question for someone about to phone a nineteen-year-old. This answers "what
    is going wrong", in the words a mentor would use.
    """
    att_min = cfg["attendance_level"]["threshold_pct"]
    sub_min = cfg["submission"]["threshold_pct"]
    weeks = [w for w in (features.get("weekly_attendance") or []) if w is not None]
    recent = sum(weeks[-4:]) / len(weeks[-4:]) if weeks else None

    concerns, positives, short = [], [], []

    # --- attendance ---------------------------------------------------------
    if recent is not None:
        if recent < att_min * 0.66:
            concerns.append(("Attendance", f"Attendance is very low at {recent:.0f}%, "
                                           f"well under the {att_min:.0f}% needed."))
            short.append("attendance is very low")
        elif recent < att_min:
            concerns.append(("Attendance", f"Attendance is low at {recent:.0f}%, "
                                           f"under the {att_min:.0f}% needed."))
            short.append("attendance is low")
        else:
            positives.append(("Attendance", f"Attendance is fine at {recent:.0f}%."))

    # --- cgpa ---------------------------------------------------------------
    cgpa = student.get("cgpa")
    if cgpa is not None:
        if cgpa < CGPA_LOW:
            concerns.append(("CGPA", f"CGPA is low at {cgpa:.2f}."))
            short.append("CGPA is low")
        elif cgpa < CGPA_WEAK:
            concerns.append(("CGPA", f"CGPA is on the low side at {cgpa:.2f}."))
            short.append("CGPA is on the low side")
        elif cgpa >= CGPA_GOOD:
            positives.append(("CGPA", f"CGPA is good at {cgpa:.2f}."))

    # --- submissions --------------------------------------------------------
    sub = student.get("submission_pct")
    if sub is not None:
        if sub < sub_min * 0.66:
            concerns.append(("Submissions", f"Most assignments have not been handed "
                                            f"in — only {sub:.0f}% submitted."))
            short.append("most assignments are missing")
        elif sub < sub_min:
            concerns.append(("Submissions", f"Some assignments have not been handed "
                                            f"in — {sub:.0f}% submitted."))
            short.append("some assignments are missing")
        else:
            positives.append(("Submissions", f"Assignments are being handed in "
                                             f"({sub:.0f}%)."))

    # --- backlogs -----------------------------------------------------------
    backlogs = student.get("backlogs") or 0
    if backlogs:
        word = "backlog" if backlogs == 1 else "backlogs"
        concerns.append(("Backlogs", f"{backlogs} {word} still to clear."))
        short.append(f"there {'is' if backlogs == 1 else 'are'} {backlogs} {word}")

    # --- the one-line verdict ----------------------------------------------
    name = (student.get("name") or "This student").split()[0]
    if concerns:
        line = _join_plain(short)
        # Not .capitalize(): that would lowercase the rest and turn CGPA into cgpa.
        summary = line[0].upper() + line[1:] + "."
    elif recent is None:
        # No attendance at all. A clean bill of health here would be a guess,
        # and this student is exactly the one the lifecycle stages exist for.
        summary = (f"There is not enough data on {name} yet. Attendance is still "
                   f"being collected, so nothing can be judged.")
    else:
        summary = f"Nothing is going wrong for {name} right now."

    return {
        "summary": summary,
        "concerns": [{"area": a, "text": t} for a, t in concerns],
        "positives": [{"area": a, "text": t} for a, t in positives],
        "concern_count": len(concerns),
    }


def _hybrid(ledger, mlres, stage):
    """Put the two opinions side by side and flag it when they disagree.

    A system that hides disagreement between its rules and its model is hiding
    the most informative thing it knows.
    """
    out = {"rules_score": ledger["score"], "rules_band": ledger["band"],
           "stage": stage["stage"], "stage_label": stage["label"],
           "model": None, "disagreement": None}
    if stage["scoring"] != "hybrid" or not mlres or not mlres.get("available"):
        if stage["scoring"] == "hybrid" and not get_model():
            # Two different situations, and saying the wrong one is a lie the
            # /api/model page immediately contradicts.
            #
            # The test is whether a trained model EXISTS, not whether this
            # process could import the libraries. `ml is None` was the wrong
            # question: ml.py imports numpy and nothing else at module level,
            # so `import ml` succeeds on the deployed function too, and
            # production told every mentor "No prediction model has been
            # trained yet" about a trained xgboost whose own report was sitting
            # in the same deployment.
            if _model_report():
                out["model_note"] = (
                    "Predictions are computed during scoring and stored, and "
                    "none is stored for this student yet. Run `python "
                    "service.py` to refresh the scores.")
            else:
                out["model_note"] = (
                    "No prediction model has been trained yet, so this student "
                    "is scored on the transparent rules alone. Run the "
                    "training step to enable predictions.")
        else:
            out["model_note"] = (mlres or {}).get("reason") or stage["explain"]
        return out
    out["model"] = mlres
    p, r = mlres["probability"], ledger["score"]
    if p >= 0.30 and r < 30:
        out["disagreement"] = {
            "kind": "model_higher",
            "message": "The model sees a pattern the thresholds do not. Usually a "
                       "shape in the trend rather than a level any single rule "
                       "checks. Worth a look."}
    elif p < 0.05 and r >= 60:
        out["disagreement"] = {
            "kind": "rules_higher",
            "message": "Thresholds are firing but the model does not expect further "
                       "decline. Often a student who is stably low rather than "
                       "actively falling."}
    return out


def _batch_scores(con, students, cfg):
    """Score a whole cohort. Rules in python, the model in ONE matrix call.

    An empty dict means "this host could not compute predictions at all" --
    either there is no loadable model or the batch call failed. That is not the
    same as "no student scored", and refresh_scores has to tell the two apart
    before it writes NULL over predictions computed elsewhere.
    """
    b = get_model()
    mlres = {}
    if b:
        payload = [{"roll_no": s["roll_no"],
                    "att": s["features"]["weekly_attendance"],
                    "marks": [s["features"].get("ia1"), s["features"].get("ia2"),
                              s["features"].get("ia3")]} for s in students]
        try:
            mlres = ml.predict_batch(b, payload)
        except Exception as e:                                  # noqa: BLE001
            # Was a bare `except Exception: mlres = {}`. A model that loads but
            # cannot predict is a real fault, and silently degrading to Rules
            # Mode is how it would go unnoticed for weeks.
            print(f"WARNING: model predict_batch failed, keeping stored "
                  f"predictions: {type(e).__name__}: {e}")
            mlres = {}
    return mlres



# ----------------------------------------------------------------------------
# Scoring refresh
# ----------------------------------------------------------------------------
# Scoring 20,000 students takes seconds. Doing it on every page load makes the
# worklist a nine-second request. Risk changes when DATA changes, not when
# someone opens a page, so scores are computed once into risk_snapshots and the
# worklist becomes an indexed SQL query that is flat in cohort size.
#
# refresh_scores() runs after any ingest, any admission and any attendance
# entry. In production it is also a nightly job.

def refresh_scores(con, actor="system"):
    import time as _t
    t0 = _t.time()
    cfg = get_config(con)
    students = _cohort(con, None)
    alerts = detect_cohort_anomalies(students, cfg)
    wl = build_worklist(students, capacity=10 ** 9, config=cfg, cohort_alerts=alerts)
    mlres = _batch_scores(con, students, cfg)

    # The predictions already on record, kept only when THIS host cannot
    # compute any.
    #
    # This is what made the deployed API contradict itself. The model libraries
    # are too large for a serverless function, so predictions are computed
    # during a local scoring run and stored in risk_snapshots.model_pct for the
    # API to serve. But refresh_scores also runs on the deployed host -- after
    # an admission, a deletion, an upload commit or the admin Refresh button --
    # and it rebuilt every row with model_pct = NULL, because that host has no
    # model to ask. One click of Remove student in production silently erased
    # all 5,001 stored predictions, and the student page then reported that no
    # model had ever been trained.
    #
    # A host that cannot compute a prediction has learned nothing about it, so
    # it now carries the stored value forward instead of destroying it. When a
    # model IS loadable its per-student verdict is authoritative, including
    # "not enough history yet", so nothing stale survives a real scoring run.
    prior = {}
    if not mlres:
        prior = {r["roll_no"]: (r["model_pct"], r["anomaly"]) for r in
                 con.execute("SELECT roll_no, model_pct, anomaly FROM risk_snapshots")}

    meta = {r["roll_no"]: dict(r) for r in con.execute(
        "SELECT roll_no,name,dept,year,section,mentor_id FROM students")}
    now = datetime.now().isoformat(timespec="seconds")
    # Precompute once. Searching `students` per row is O(N^2) and at 20,000
    # students that alone is minutes.
    weeks_by_roll = {s["roll_no"]: len(s["features"]["weekly_attendance"])
                     for s in students}
    rows = []
    # `flagged` separates "this is work for a mentor this week" from "this
    # student is scored and healthy". Both belong in risk_snapshots: the
    # directory and the band counts need every scoreable student, while the
    # worklist reads only the flagged ones.
    buckets = ([(i, 1) for i in wl["this_week"]]
               + [(i, 1) for i in wl["watch"]]
               + [(i, 1) for i in wl["routed_to_cohort"]]
               + [(i, 0) for i in wl.get("healthy", [])])
    for item, is_flagged in buckets:
        rn = item["roll_no"]
        m = mlres.get(rn) or {}
        md = meta.get(rn, {})
        weeks = weeks_by_roll.get(rn, 0)
        if m.get("available"):
            pct, anom = m.get("percent"), int(bool(m.get("anomaly_unusual")))
        elif mlres:
            pct, anom = None, 0        # a real model looked and declined to score
        else:
            pct, anom = prior.get(rn, (None, 0))   # nothing was asked; keep what we had
        rows.append((rn, item["score"], item["band"], item["delta"], item["priority"],
                     item["confidence"], item["headline"], item["primary_driver"],
                     lifecycle.stage_for(weeks),
                     pct,
                     anom,
                     (item.get("explained_by_cohort") or {}).get("cohort"),
                     jsonsafe.dumps(item.get("guardrails") or []),
                     md.get("dept"), md.get("year"), md.get("section"),
                     md.get("mentor_id"), md.get("name"), now, is_flagged))

    con.execute("DELETE FROM risk_snapshots")
    # Batched: this is one row per student, so at institution size it is the
    # difference between a few hundred round trips and twenty thousand.
    con.executebatch(
        "INSERT INTO risk_snapshots (roll_no,score,band,delta,priority,confidence,"
        "headline,primary_driver,stage,model_pct,anomaly,cohort,guardrail,dept,"
        "year,section,mentor_id,name,computed_at,flagged) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows, page_size=1000)
    con.execute("DELETE FROM cohort_alerts")
    # Columns named rather than positional, and ON CONFLICT because `cohort` is
    # the primary key and detect_cohort_anomalies can name one twice in a batch.
    con.executebatch("INSERT INTO cohort_alerts (cohort,payload,computed_at) "
                     "VALUES (?,?,?) ON CONFLICT(cohort) DO UPDATE SET "
                     "payload=EXCLUDED.payload, computed_at=EXCLUDED.computed_at",
                     [(a["cohort"], jsonsafe.dumps(a), now) for a in alerts])
    flagged_n = sum(f for _, f in buckets)
    audit(con, actor, "scores_refreshed", "cohort",
          f"{len(rows)} scored ({flagged_n} flagged), {len(alerts)} cohort alerts")
    con.commit()
    return {"scored": len(rows), "flagged": flagged_n, "cohort_alerts": len(alerts),
            "seconds": round(_t.time() - t0, 2), "at": now}


def snapshots_stale(con):
    return con.execute("SELECT COUNT(*) c FROM risk_snapshots").fetchone()["c"] == 0


def db_is_empty(con):
    """No students yet, i.e. a database that has never been seeded.

    This replaces the old os.path.exists(DB_PATH) check. That question made
    sense while the database was a local SQLite file; against a remote Postgres
    there is no file to stat, and connect() already runs SCHEMA with
    CREATE TABLE IF NOT EXISTS, so "do the tables exist" is always yes. What the
    callers actually want to know is whether there is any data in them.
    """
    return con.execute("SELECT COUNT(*) c FROM students").fetchone()["c"] == 0


def default_mentor(con):
    """The first section mentor, used when no one is named."""
    r = con.execute("SELECT id FROM mentors WHERE role='mentor' ORDER BY id "
                    "LIMIT 1").fetchone()
    return r["id"] if r else "admin"


def get_worklist(con, mentor_id=None, capacity=5, page=1, page_size=50):
    """Served from risk_snapshots. Flat in cohort size: an indexed SQL query
    returns the top `capacity` rows whether the college has 400 students or
    40,000.

    `mentor_id` of None means the whole institution. It must NOT fall back to
    some default mentor, or an institution-wide view silently shows one
    mentor's caseload and is mistaken for the whole college.
    """
    if snapshots_stale(con):
        refresh_scores(con)

    scope, args = "1=1", []
    # mentor_id here has already been through resolve_scope(), which is the
    # authorization boundary: None means an institution role asked for the
    # whole institution, a value means that caseload. Re-checking the role
    # here would let a request that resolve_scope refused slip through as
    # unscoped, which is exactly how `?mentor=` omitted returned all 5,003.
    if mentor_id:
        scope, args = "mentor_id=?", [mentor_id]

    def rows(where, extra=(), limit=None, offset=0):
        q = (f"SELECT roll_no,name,dept,year,section,score,band,delta,priority,"
             f"confidence,headline,primary_driver,stage,model_pct,anomaly,cohort,"
             f"guardrail FROM risk_snapshots WHERE {scope} AND {where} "
             f"ORDER BY priority DESC, delta DESC, score DESC")
        if limit is not None:
            q += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        out = []
        for r in con.execute(q, (*args, *extra)):
            d = dict(r)
            d["anomaly"] = bool(d["anomaly"])
            d["guardrails"] = json.loads(d.pop("guardrail") or "[]")
            d["explained_by_cohort"] = {"cohort": d["cohort"]} if d["cohort"] else None
            out.append(d)
        return out

    def count(where, extra=()):
        return con.execute(f"SELECT COUNT(*) c FROM risk_snapshots WHERE {scope} "
                           f"AND {where}", (*args, *extra)).fetchone()["c"]

    individual = "flagged=1 AND cohort IS NULL"
    this_week = rows(individual, limit=capacity)
    total_flagged = count(individual)
    watch_total = max(0, total_flagged - capacity)
    watch = rows(individual, limit=page_size, offset=capacity + (page - 1) * page_size)

    alerts = [json.loads(r["payload"]) for r in
              con.execute("SELECT payload FROM cohort_alerts")]
    stamp = con.execute("SELECT MAX(computed_at) m FROM risk_snapshots").fetchone()["m"]
    in_scope = con.execute(f"SELECT COUNT(*) c FROM students WHERE {scope}",
                           args).fetchone()["c"]

    return {"this_week": this_week, "watch": watch,
            "watch_page": {"page": page, "page_size": page_size, "total": watch_total,
                           "pages": max(1, -(-watch_total // page_size))},
            "routed_to_cohort_count": count("flagged=1 AND cohort IS NOT NULL"),
            "capacity": capacity, "total_flagged": total_flagged,
            "total_routed": count("flagged=1 AND cohort IS NOT NULL"),
            "cohort_alerts": alerts,
            "mentor": MENTORS.get(mentor_id, {}).get("name", mentor_id),
            "mentor_id": mentor_id, "students_in_scope": in_scope,
            "mode": model_status(con)["mode"],
            "onboarding": lifecycle.onboarding_queue(con, 20, mentor_id),
            "scores_computed_at": stamp}


def list_students(con, mentor_id=None, q="", risk="all", page=1, page_size=50):
    """Paginated directory. At 20,000 students you cannot ship the whole table."""
    where, args = ["1=1"], []
    # mentor_id here has already been through resolve_scope(), which is the
    # authorization boundary: None means an institution role asked for the
    # whole institution, a value means that caseload. Re-checking the role
    # here would let a request that resolve_scope refused slip through as
    # unscoped, which is exactly how `?mentor=` omitted returned all 5,003.
    if mentor_id:
        where.append("s.mentor_id=?")
        args.append(mentor_id)
    if q:
        # ILIKE, not LIKE: SQLite's LIKE is case-insensitive for ASCII but
        # Postgres' is case-sensitive, so searching "meera" silently stopped
        # matching "Meera Nair" -- an empty result set rather than an error.
        where.append("(s.roll_no ILIKE ? OR s.name ILIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
        
    # Band filtering, so a drill-down from the dashboard or the risk
    # distribution chart lands on exactly the cohort that was clicked rather
    # than an approximation of it. Values are matched against a fixed map, so
    # nothing user-supplied reaches the SQL.
    RISK_FILTERS = {
        "at_risk": "r.band IN ('Medium', 'High')",
        "high": "r.band = 'High'",
        "medium": "r.band = 'Medium'",
        "low": "r.band = 'Low'",
        # delta >= 10 exactly matches get_summary's "rising" count. Using
        # delta > 0 here would show 1,087 students behind a metric reading
        # 544 -- a drill-down that contradicts the number it came from.
        "rising": "r.delta >= 10",
        "unscored": "r.band IS NULL",
    }
    clause = RISK_FILTERS.get(risk)
    if clause:
        where.append(clause)

    w = " AND ".join(where)
    total = con.execute(f"SELECT COUNT(*) c FROM students s LEFT JOIN risk_snapshots r ON s.roll_no = r.roll_no WHERE {w}", args).fetchone()["c"]
    rows = con.execute(
        f"SELECT s.roll_no, s.name, s.dept, s.year, s.section, s.status, s.admitted_on, r.score, r.band "
        f"FROM students s LEFT JOIN risk_snapshots r ON s.roll_no = r.roll_no "
        f"WHERE {w} ORDER BY s.roll_no LIMIT ? OFFSET ?",
        (*args, page_size, (page - 1) * page_size)).fetchall()
    return {"total": total, "page": page, "page_size": page_size,
            "pages": max(1, -(-total // page_size)),
            "students": [dict(r) for r in rows]}


def get_summary(con, mentor_id=None):
    if snapshots_stale(con):
        refresh_scores(con)
    scope, args = "1=1", []
    # mentor_id here has already been through resolve_scope(), which is the
    # authorization boundary: None means an institution role asked for the
    # whole institution, a value means that caseload. Re-checking the role
    # here would let a request that resolve_scope refused slip through as
    # unscoped, which is exactly how `?mentor=` omitted returned all 5,003.
    if mentor_id:
        scope, args = "mentor_id=?", [mentor_id]
    bands = {"Low": 0, "Medium": 0, "High": 0}
    for r in con.execute(f"SELECT band, COUNT(*) c FROM risk_snapshots WHERE {scope} "
                         f"GROUP BY band", args):
        # Guarded the way get_dashboard already guards the identical loop: a
        # NULL or unexpected band value used to raise KeyError here and 500
        # /api/summary while /api/dashboard carried on working.
        if r["band"] in bands:
            bands[r["band"]] = r["c"]
    total = con.execute(f"SELECT COUNT(*) c FROM students WHERE {scope}", args).fetchone()["c"]
    rising = con.execute(f"SELECT COUNT(*) c FROM risk_snapshots WHERE {scope} "
                         f"AND delta >= 10", args).fetchone()["c"]
    scored = sum(bands.values())
    return {"total_students": total, "scored": scored,
            "not_yet_scoreable": total - scored, "bands": bands, "rising": rising,
            # Scoped like every other number on this strip. It was a bare
            # COUNT over the whole interventions table, so a mentor with 27
            # students saw the institution's 12 open actions beside their own
            # counts of 3 and 2 -- a number they could not reconcile and did
            # not own.
            "open_interventions": con.execute(
                f"SELECT COUNT(*) c FROM interventions i WHERE i.status='open'"
                + (" AND i.roll_no IN (SELECT roll_no FROM students "
                   "WHERE mentor_id=?)" if mentor_id else ""),
                ([mentor_id] if mentor_id else [])).fetchone()["c"],
            "mode": model_status(con)["mode"], "data_source": "Synthetic demo data"}


def get_roster(con, mentor_id=None):
    """Every student in scope with the four facts a mentor reads at a glance.

    Attendance is the mean across all recorded weeks, so a student admitted
    last week correctly shows no percentage rather than a misleading 0.
    """
    scope, args = "1=1", []
    # mentor_id here has already been through resolve_scope(), which is the
    # authorization boundary: None means an institution role asked for the
    # whole institution, a value means that caseload. Re-checking the role
    # here would let a request that resolve_scope refused slip through as
    # unscoped, which is exactly how `?mentor=` omitted returned all 5,003.
    if mentor_id:
        scope, args = "s.mentor_id=?", [mentor_id]
    # Two Postgres details in this one query, both of which SQLite tolerated:
    #   1. attendance.pct is REAL, so AVG() returns double precision, and
    #      Postgres has no round(double precision, integer) -- only
    #      round(numeric, integer). Cast to numeric to round, then back to
    #      float8 so psycopg2 hands back a float rather than a Decimal.
    #   2. An output alias is only allowed in ORDER BY when it is the entire
    #      sort key, so "ORDER BY attendance_pct IS NULL" does not resolve.
    #      NULLS LAST expresses the same intent and does resolve.
    rows = con.execute(
        f"SELECT s.roll_no, s.name, s.gender, s.cgpa, r.band, r.score, "
        f"       ROUND((SELECT AVG(a.pct) FROM attendance a "
        f"              WHERE a.roll_no = s.roll_no)::numeric, 1)::float8 "
        f"       AS attendance_pct "
        f"FROM students s LEFT JOIN risk_snapshots r ON r.roll_no = s.roll_no "
        f"WHERE {scope} ORDER BY attendance_pct ASC NULLS LAST", args
    ).fetchall()
    return {"students": [dict(r) for r in rows], "total": len(rows)}


def get_dashboard(con, mentor_id=None):
    scope, args = "1=1", []
    # mentor_id here has already been through resolve_scope(), which is the
    # authorization boundary: None means an institution role asked for the
    # whole institution, a value means that caseload. Re-checking the role
    # here would let a request that resolve_scope refused slip through as
    # unscoped, which is exactly how `?mentor=` omitted returned all 5,003.
    if mentor_id:
        scope, args = "mentor_id=?", [mentor_id]
        
    bands = {"Low": 0, "Medium": 0, "High": 0}
    for r in con.execute(f"SELECT band, COUNT(*) c FROM risk_snapshots WHERE {scope} "
                         f"GROUP BY band", args):
        if r["band"]:
            bands[r["band"]] = r["c"]
            
    total = con.execute(f"SELECT COUNT(*) c FROM students WHERE {scope}", args).fetchone()["c"]
    rising = con.execute(f"SELECT COUNT(*) c FROM risk_snapshots WHERE {scope} "
                         f"AND delta >= 10", args).fetchone()["c"]
                         
    cgpa_row = con.execute(
        f"SELECT AVG(cgpa) as cgpa FROM students WHERE {scope} AND cgpa IS NOT NULL",
        args).fetchone()
    avg_cgpa = round(cgpa_row["cgpa"], 2) if cgpa_row and cgpa_row["cgpa"] is not None else 7.50

    return {
        "at_risk": bands["Medium"] + bands["High"],
        "total_students": total,
        "rising": rising,
        "average_cgpa": avg_cgpa
    }

def get_student(con, roll_no):
    cfg = get_config(con)
    s, f = _features(con, roll_no)
    if not s:
        return None
    ledger = score_student(f, cfg)
    d = risk_delta(f, config=cfg)
    weeks = len([w for w in f["weekly_attendance"] if w is not None])
    stage = lifecycle.stage_info(weeks)

    b = get_model()
    mlres, fc = None, None
    if b and stage["scoring"] == "hybrid":
        try:
            mlres = ml.predict(b, f["weekly_attendance"],
                               [f.get("ia1"), f.get("ia2"), f.get("ia3")])
            fc = ml.forecast_attendance(f["weekly_attendance"])
        except Exception as e:
            mlres = {"available": False, "reason": f"{type(e).__name__}: {e}"}
    elif stage["scoring"] == "hybrid":
        # No loadable model on THIS host, but refresh_scores stored this
        # student's probability when it last ran somewhere that had one.
        # Serving the stored value keeps the prediction on the page; only the
        # live recomputation and the attendance forecast are unavailable.
        #
        # The condition was `ml is None`, which is false on the deployed
        # function -- ml.py imports only numpy at module level, so `import ml`
        # succeeds there and every heavy import is inside a function. So this
        # branch never ran in production, the stored predictions were never
        # read, and the page fell through to "no model has been trained".
        # `not b` is the real question: can this host compute or not.
        mlres = _stored_model_result(con, roll_no)

    att = con.execute("SELECT week_index, week_start, pct FROM attendance "
                      "WHERE roll_no=? ORDER BY week_index", (roll_no,)).fetchall()
    ivs = con.execute("SELECT * FROM interventions WHERE roll_no=? ORDER BY created_at DESC",
                      (roll_no,)).fetchall()
    fb = con.execute("SELECT * FROM feedback WHERE roll_no=? ORDER BY id DESC LIMIT 5",
                     (roll_no,)).fetchall()
    return {
        # Never expose gender/category on the mentor's screen. They exist solely
        # so the fairness audit can run, and are never scoring inputs.
        "student": {k: s[k] for k in ("roll_no", "name", "dept", "year", "section",
                                      "backlogs", "submission_pct", "fee_status", "cgpa",
                                      "ia1", "ia2", "ia3", "ia_max", "last_contact_at",
                                      "status", "admitted_on")},
        "stage": stage,
        "ledger": ledger if stage["scoring"] != "none" else None,
        "hybrid": _hybrid(ledger, mlres, stage),
        "forecast": fc,
        "delta": d if stage["scoring"] != "none" else None,
        "attendance": [dict(a) for a in att],
        "review": plain_review(dict(s), f, cfg),
        "suggested_playbook": suggest_playbook(ledger) if stage["scoring"] != "none" else None,
        "interventions": [dict(i) for i in ivs],
        "feedback": [dict(x) for x in fb],
    }


def get_student_public_view(con, roll_no):
    """What the STUDENT sees. No score, no band, no label. This screen is the
    answer to the stigmatisation question - show it, do not just claim it."""
    cfg = get_config(con)
    s, f = _features(con, roll_no)
    if not s:
        return None
    ledger = score_student(f, cfg)
    actions = []
    for c in ledger["components"]:
        if c["available"] and c["points"]:
            actions.append({
                "attendance_level": "Aim for 75% attendance this month. Speak to your mentor if something is making that hard.",
                "attendance_decline": "Your attendance changed recently. A short chat with your mentor can sort out what changed.",
                "assessment_level": "A remedial slot is available for your weakest subject.",
                "assessment_trend": "Book a doubt-clearing session before the next internal.",
                "backlogs": "A senior who cleared this subject can be paired with you.",
                "submission": "Two assignments are outstanding. Your mentor can help you plan the order.",
                "fee": "You may be eligible for a scholarship or an instalment plan. The office can help, confidentially.",
            }.get(c["key"]))
    return {
        "name": s["name"],
        "attendance_recent": [a["pct"] for a in con.execute(
            "SELECT pct FROM attendance WHERE roll_no=? ORDER BY week_index", (roll_no,))][-8:],
        "marks": [s["ia1"], s["ia2"], s["ia3"]],
        "suggestions": [a for a in actions if a][:3],
        "support_available": ["Talk to your mentor", "Scholarship desk",
                              "Counselling (confidential)", "Peer mentoring"],
        "note": "This view never shows a risk score or a risk label.",
    }


def create_intervention(con, roll_no, mentor_id, playbook, trigger, action_text,
                        followup_days=21):
    now = datetime.now()
    # Derived from MAX(id), not COUNT(*). remove_student deletes intervention
    # rows, so the count regressed and the next insert reused a live id --
    # a primary-key violation which, pre-pooling, also poisoned the shared
    # transaction and took the rest of the API down with it.
    last = con.execute("SELECT MAX(id) m FROM interventions WHERE id LIKE 'IV%'"
                       ).fetchone()["m"]
    nxt = 1
    if last and last[2:].isdigit():
        nxt = int(last[2:]) + 1
    iid = f"IV{nxt:04d}"
    if not con.execute("SELECT 1 FROM students WHERE roll_no=?",
                       (roll_no,)).fetchone():
        # No foreign keys on the child tables, so without this the insert
        # succeeded and created an intervention against a student who does
        # not exist -- returning 200 as though it had worked.
        raise ValueError(f"{roll_no} not found")
    con.execute("INSERT INTO interventions (id,roll_no,mentor,trigger,playbook,"
                "action_text,approved_by_mentor,created_at,followup_at,status,outcome)"
                " VALUES (?,?,?,?,?,?,1,?,?,'open','')",
                (iid, roll_no, MENTORS.get(mentor_id, {}).get("name", mentor_id),
                 trigger, playbook, action_text,
                 now.date().isoformat(),
                 (now + timedelta(days=followup_days)).date().isoformat()))
    con.execute("UPDATE students SET last_contact_at=? WHERE roll_no=?",
                (now.date().isoformat(), roll_no))
    audit(con, mentor_id, "intervention_approved", roll_no, f"{playbook} -> {iid}")
    con.commit()
    return {"id": iid, "status": "open"}


def close_intervention(con, iv_id, outcome, mentor_id, notes=""):
    if outcome not in ("improved", "unchanged", "worsened", "unreachable"):
        raise ValueError("invalid outcome")
    con.execute("UPDATE interventions SET status='closed', outcome=?, notes=? WHERE id=?",
                (outcome, notes, iv_id))
    audit(con, mentor_id, "intervention_closed", iv_id, outcome)
    con.commit()
    # Also measure it from the data, so the mentor's view and the record's view
    # are both available and can be compared.
    row = con.execute("SELECT roll_no, created_at FROM interventions WHERE id=?",
                      (iv_id,)).fetchone()
    measured, note = None, None
    if row:
        att = outcomes.attendance_matrix(con).get(row["roll_no"])
        w = outcomes.week_index(row["created_at"])
        m = outcomes.measure_one(att, w) if (att and w is not None) else None
        if m:
            con.execute("UPDATE interventions SET baseline_value=?, measured_value=?, "
                        "measured_change=?, measured_outcome=?, baseline_week=?, "
                        "measured_at=? WHERE id=?",
                        (m["baseline"], m["after"], m["change"], m["outcome"], w,
                         datetime.now().isoformat(timespec="seconds"), iv_id))
            con.commit()
            measured = m
        else:
            # Not a failure. There is simply no attendance recorded yet for the
            # weeks after this action, and saying so is better than silence.
            try:
                when = (datetime.fromisoformat(row["created_at"][:10])
                        + timedelta(weeks=outcomes.FOLLOWUP_WEEKS + 1)).date().isoformat()
            except ValueError:
                when = "later"
            note = (f"Recorded. The attendance check needs "
                    f"{outcomes.FOLLOWUP_WEEKS} weeks of data after this action, "
                    f"so it will run once the register reaches {when}.")
    return {"id": iv_id, "status": "closed", "outcome": outcome,
            "measured": measured, "measure_note": note}


def record_feedback(con, roll_no, mentor_id, verdict, reason=""):
    """One-tap mentor feedback. This is how the system earns its labels."""
    if verdict not in ("confirm_at_risk", "not_at_risk", "at_risk_not_visible"):
        raise ValueError("invalid verdict")
    con.execute("INSERT INTO feedback (roll_no,mentor,verdict,reason,created_at) "
                "VALUES (?,?,?,?,?)",
                (roll_no, mentor_id, verdict, reason, datetime.now().isoformat(timespec="seconds")))
    audit(con, mentor_id, "feedback", roll_no, verdict)
    con.commit()
    labelled = con.execute("SELECT COUNT(*) c FROM feedback").fetchone()["c"]
    needed = 400
    return {"labelled": labelled, "needed_for_hybrid": needed,
            "progress_pct": round(min(100, labelled / needed * 100), 1),
            "mode": "Rules Mode" if labelled < needed else "Hybrid Mode (ready)"}


def _effectiveness_stamp(con):
    """Changes only when the intervention record changes, so it is a safe key."""
    r = con.execute(
        "SELECT COUNT(*) n, COALESCE(MAX(measured_at),'') m, "
        "COALESCE(SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END),0) c "
        "FROM interventions").fetchone()
    return f"{r['n']}-{r['c']}-{r['m']}"


def get_effectiveness(con, force=False):
    """Two answers to 'did it work', and they are not the same question.

    MEASURED comes from attendance data. SELF-REPORTED comes from what the
    mentor ticked. Showing both, with the matched comparison beside them, is the
    honest version of this screen.
    """
    # The matched comparison scans the whole cohort at every intervention week,
    # which is over a second at 20,000 students. It only changes when the
    # intervention record changes, so it is cached against a stamp of that record
    # rather than recomputed on every page view.
    stamp = _effectiveness_stamp(con)
    if not force:
        hit = con.execute("SELECT payload FROM effectiveness_cache WHERE stamp=?",
                          (stamp,)).fetchone()
        if hit:
            return json.loads(hit["payload"])

    measured = outcomes.matched_effect(con)
    reported = intervention_effectiveness(
        [dict(r) for r in con.execute("SELECT * FROM interventions")])
    n_meas = con.execute(
        "SELECT COUNT(*) c FROM interventions WHERE measured_outcome IS NOT NULL "
        "AND measured_outcome != 'not_measurable'").fetchone()["c"]
    out = {
        "measured": measured,
        "self_reported": reported,
        "measured_total": n_meas,
        "closed_total": reported["closed_total"],
        "note": "Measured from attendance records, not from what anyone reported.",
    }
    con.execute("DELETE FROM effectiveness_cache")
    con.execute("INSERT INTO effectiveness_cache VALUES (?,?,?)",
                (stamp, jsonsafe.dumps(out),
                 datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return out


def measure_outcomes(con, actor="system"):
    return outcomes.measure_all(con, audit_fn=audit, actor=actor)


def what_if(con, roll_no, changes):
    """Recompute the ledger under hypothetical values. Exact, not estimated."""
    cfg = get_config(con)
    s, f = _features(con, roll_no)
    if not s:
        return None
    res = what_if_engine(f, changes, cfg)
    res["levers"] = {"fields": WHAT_IF_FIELDS, "current": current_levers(f, cfg)}
    res["to_reach_low"] = {
        k: minimum_change_to(f, "Low", k, cfg)
        for k in ("attendance_pct", "submission_pct", "backlogs")
    }
    # The model can also be re-run, but that answer is an ESTIMATE and must not
    # be presented with the same confidence as the arithmetic above.
    b = get_model()
    if b:
        try:
            mod = apply_changes(f, changes, cfg)
            before = ml.predict(b, f["weekly_attendance"],
                                [f.get("ia1"), f.get("ia2"), f.get("ia3")])
            after = ml.predict(b, mod["weekly_attendance"],
                               [mod.get("ia1"), mod.get("ia2"), mod.get("ia3")])
            if before.get("available") and after.get("available"):
                res["model"] = {
                    "exact": False,
                    "before_percent": before["percent"],
                    "after_percent": after["percent"],
                    "note": "The prediction also moves, but unlike the score above "
                            "this is an estimate from a model, not arithmetic.",
                }
        except Exception:
            pass
    return res


def get_fairness(con):
    """Subgroup audit. Runs automatically, not on request.

    Reads risk_snapshots rather than rescoring the cohort, so it stays fast at
    institution size. Reports the flag rate per subgroup and the disparity ratio
    against the least-flagged group; past 1.25 is marked for review, following
    the four-fifths convention used in adverse-impact testing.
    """
    if snapshots_stale(con):
        refresh_scores(con)
    rows = con.execute(
        "SELECT s.gender, s.category, s.first_gen, s.hostel, r.band "
        "FROM students s JOIN risk_snapshots r ON r.roll_no = s.roll_no").fetchall()

    dims = {"gender": lambda m: m["gender"],
            "category": lambda m: m["category"],
            "first_generation": lambda m: None if m["first_gen"] is None else
                                ("Yes" if m["first_gen"] else "No"),
            "hostel": lambda m: None if m["hostel"] is None else
                       ("Hostel" if m["hostel"] else "Day scholar")}
    buckets = {d: {} for d in dims}
    for m in rows:
        flagged = int(m["band"] in ("Medium", "High"))
        for d, fn in dims.items():
            g = fn(m)
            if g is None:
                continue
            b = buckets[d].setdefault(g, {"n": 0, "flagged": 0})
            b["n"] += 1
            b["flagged"] += flagged

    out = {}
    for d, groups in buckets.items():
        rs = [{"group": g, "n": v["n"], "flagged": v["flagged"],
               "flag_rate": round(v["flagged"] / v["n"] * 100, 1) if v["n"] else 0.0}
              for g, v in groups.items()]
        base = min((r["flag_rate"] for r in rs if r["n"] >= 20), default=0) or 1
        for r in rs:
            r["disparity_ratio"] = round(r["flag_rate"] / base, 2)
            r["review"] = r["disparity_ratio"] > 1.25 and r["n"] >= 20
        rs.sort(key=lambda r: -r["flag_rate"])
        out[d] = rs
    return {"dimensions": out,
            "method": "Flag rate per subgroup vs the least-flagged group (n>=20). "
                      "Ratios above 1.25 are marked for review.",
            "note": "These attributes are audit-only. None of them is a scoring input, "
                    "and none is shown on the mentor's screen."}


def handle_upload(con, path_or_buffer, actor="admin", commit=False):
    """Ingest a file. Returns the mapping report for mentor confirmation.
    Nothing is written until commit=True."""
    report, records = ingest_file(path_or_buffer)
    applied = 0
    if commit:
        for rec in records:
            exists = con.execute("SELECT 1 FROM students WHERE roll_no=?",
                                 (rec["roll_no"],)).fetchone()
            if not exists:
                con.execute("INSERT INTO students (roll_no,name,dept,year,section,mentor_id) "
                            "VALUES (?,?,?,?,?,?)",
                            (rec["roll_no"], rec.get("name"), rec.get("dept"),
                             rec.get("year"), rec.get("section"),
                             _assign_mentor(rec.get("dept"), rec.get("year"))))
            sets, args = [], []
            for col, key in (("ia1", "ia1"), ("ia2", "ia2"), ("ia3", "ia3"),
                             ("backlogs", "backlogs"), ("submission_pct", "submission_pct"),
                             ("fee_status", "fee_status"), ("name", "name")):
                if rec.get(key) is not None:
                    sets.append(f"{col}=?")
                    args.append(rec[key])
            if sets:
                con.execute(f"UPDATE students SET {','.join(sets)} WHERE roll_no=?",
                            (*args, rec["roll_no"]))
            for i, w in enumerate(rec.get("weekly_attendance") or []):
                if w["attendance_pct"] is not None:
                    con.execute("INSERT INTO attendance (roll_no,week_index,"
                                "week_start,pct) VALUES (?,?,?,?) ON CONFLICT"
                                "(roll_no,week_index) DO UPDATE SET pct=EXCLUDED.pct",
                                (rec["roll_no"], i, w["week"], w["attendance_pct"]))
            applied += 1
        con.commit()

    con.execute("INSERT INTO uploads (filename,actor,rows_keyed,rows_unmatched,report,at) "
                "VALUES (?,?,?,?,?,?)",
                (str(getattr(path_or_buffer, "name", path_or_buffer))[:200], actor,
                 report["rows_keyed"], report["rows_unmatched"],
                 jsonsafe.dumps(report), datetime.now().isoformat(timespec="seconds")))
    audit(con, actor, "upload_committed" if commit else "upload_previewed",
          str(getattr(path_or_buffer, "name", path_or_buffer))[:120],
          f"{report['rows_keyed']} keyed")
    con.commit()
    report["applied"] = applied
    report["committed"] = commit
    return report


def get_audit(con, limit=100):
    return [dict(r) for r in con.execute(
        "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]


# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
# Lifecycle passthroughs (thin, so main.py stays a router)
# ----------------------------------------------------------------------------
def _record_admission_assignment(con, roll_no, actor):
    """Mirror an admission's auto-assigned mentor into mentor_assignments.

    lifecycle.admit() picks a mentor with _auto_mentor() and writes
    students.mentor_id directly, which predates the assignment table. Without
    this the two disagree the moment anyone is admitted: the directory filters
    on students.mentor_id and counted the new student, caseload_of() reads
    mentor_assignments and did not, so a mentor's own screens showed 28 and 27
    at the same time.

    Written here rather than inside lifecycle so that module stays free of the
    assignment concept, and idempotent so a re-admission cannot create a second
    active row -- the partial unique index would refuse it anyway.
    """
    mid = con.execute("SELECT mentor_id FROM students WHERE roll_no=?",
                      (roll_no,)).fetchone()
    mid = mid["mentor_id"] if mid else None
    if not mid or is_assigned_to(con, roll_no, mid):
        return
    con.execute("UPDATE mentor_assignments SET status='ended', ended_at=?, "
                "ended_by=?, end_reason='superseded on admission' "
                "WHERE roll_no=? AND status='active'",
                (datetime.now().isoformat(timespec="seconds"), actor, roll_no))
    con.execute("INSERT INTO mentor_assignments "
                "(roll_no,mentor_id,assigned_at,assigned_by,status) "
                "VALUES (?,?,?,?,'active')",
                (roll_no, mid, datetime.now().isoformat(timespec="seconds"), actor))
    con.commit()


def admit_student(con, actor="admin", **kw):
    res = lifecycle.admit(con, actor=actor, audit_fn=audit, **kw)
    _record_admission_assignment(con, res["roll_no"], actor)
    return res


def admit_students_bulk(con, records, actor="admin"):
    res = lifecycle.admit_bulk(con, records, actor=actor, audit_fn=audit)
    for s in res.get("students", []):
        _record_admission_assignment(con, s["roll_no"], actor)
    return res


def remove_student(con, roll_no, actor="mentor"):
    """Delete a student and everything keyed to them.

    None of the child tables declare a foreign key, so nothing cascades. Left
    alone, the attendance rows and interventions of a deleted student stay in
    the database for ever and quietly skew every cohort statistic.
    """
    roll_no = (roll_no or "").strip().upper()
    row = con.execute("SELECT roll_no, name FROM students WHERE roll_no=?",
                      (roll_no,)).fetchone()
    if not row:
        raise ValueError(f"{roll_no} not found")

    removed = {}
    # mentor_assignments included: a genuine deletion means the person is gone,
    # and leaving their assignment rows behind would keep them in a mentor's
    # caseload count for ever with no student to open. This is the one place
    # assignment history is discarded rather than ended, because there is no
    # longer a subject for it to be history of.
    for table in ("attendance", "interventions", "feedback", "risk_snapshots",
                  "mentor_assignments"):
        cur = con.execute(f"DELETE FROM {table} WHERE roll_no=?", (roll_no,))
        removed[table] = cur.rowcount
    con.execute("DELETE FROM students WHERE roll_no=?", (roll_no,))
    audit(con, actor, "student_removed", roll_no,
          f"{row['name']} removed with "
          f"{removed['attendance']} attendance weeks, "
          f"{removed['interventions']} interventions")
    con.commit()
    return {"roll_no": roll_no, "name": row["name"], "removed": removed}


def set_student_status(con, roll_no, status, actor="admin", note=""):
    return lifecycle.set_status(con, roll_no, status, actor, note, audit_fn=audit)


def add_attendance(con, roll_no, week_start, attended, held, actor="staff"):
    return lifecycle.record_attendance(con, roll_no, week_start, attended, held,
                                       actor, audit_fn=audit)


def add_assessment(con, roll_no, which, marks, max_marks=30, actor="staff"):
    return lifecycle.record_assessment(con, roll_no, which, marks, max_marks,
                                       actor, audit_fn=audit)


def update_student(con, roll_no, actor="staff", **kw):
    return lifecycle.update_record(con, roll_no, actor=actor, audit_fn=audit, **kw)


def get_onboarding(con, limit=50, mentor_id=None):
    return lifecycle.onboarding_queue(con, limit, mentor_id)

def seed_open_interventions(con, count=12, actor="system"):
    """Give the demo a realistic set of interventions that are still running.

    interventions_seed.csv is history: every one of its 900 rows is closed with
    an outcome. That leaves the tracking half of the product loop invisible --
    "Open interventions" reads 0, no student shows a follow-up, and nobody can
    see what an in-progress case looks like.

    "Open" is a statement about today, not about whenever the CSV was
    generated, so these are created here against the students who are actually
    flagged right now, using each student's own suggested playbook. Requires
    risk_snapshots to be populated, so call it after refresh_scores.

    Idempotent: students who already have an open intervention are skipped.
    """
    # Spread across primary drivers rather than taking the top N by priority.
    # The highest-priority students almost all share one driver, so a plain
    # LIMIT produced twelve copies of the same playbook -- true, but it hides
    # the range of actions the system actually routes to. Every student here is
    # genuinely flagged and still gets their own suggested playbook; only the
    # selection order changes.
    candidates = con.execute(
        "SELECT r.roll_no, r.mentor_id, r.primary_driver FROM risk_snapshots r "
        "WHERE r.flagged = 1 AND r.cohort IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM interventions i "
        "                WHERE i.roll_no = r.roll_no AND i.status = 'open') "
        "ORDER BY r.priority DESC LIMIT ?", (count * 25,)).fetchall()

    by_driver = {}
    for row in candidates:
        by_driver.setdefault(row["primary_driver"], []).append(row)

    flagged, pools = [], list(by_driver.values())
    while pools and len(flagged) < count:
        for pool in list(pools):
            if not pool:
                pools.remove(pool)
                continue
            flagged.append(pool.pop(0))
            if len(flagged) >= count:
                break

    fallback_mentor = default_mentor(con)
    made = 0
    for row in flagged:
        detail = get_student(con, row["roll_no"])
        pb = (detail or {}).get("suggested_playbook")
        if not pb:
            # No dominant driver, so no standard action to suggest. Skipping is
            # correct: inventing one would misrepresent what the rules decided.
            continue
        try:
            create_intervention(con, row["roll_no"],
                                row["mentor_id"] or fallback_mentor,
                                pb["title"], pb["trigger"],
                                action_text="",
                                followup_days=pb.get("followup_days", 21))
            made += 1
        except ValueError:
            # Student vanished between the query and the insert; not fatal.
            continue
    return made


if __name__ == "__main__":
    print("Rebuilding database...")
    print(" ", reset_db())
    con = connect()

    # Scores first: seed_open_interventions picks the students who are actually
    # flagged, so it needs risk_snapshots to exist.
    refresh_scores(con)
    opened = seed_open_interventions(con)
    print(f"  {opened} interventions left open, so the demo shows work in progress")

    mid = default_mentor(con)
    s = get_summary(con, mid)
    print(f"\nSummary  {s['total_students']} students  {s['bands']}  "
          f"rising={s['rising']}  open interventions={s['open_interventions']}")
    wl = get_worklist(con, mid, 5)
    print(f"\nWorklist for {wl['mentor']}  ({wl['students_in_scope']} in scope, "
          f"{wl['total_flagged']} flagged, {wl['total_routed']} routed for review)")
    for i, w in enumerate(wl["this_week"], 1):
        print(f"  {i}. {w['roll_no']} {w['name'][:20]:20s} {w['score']:3d} "
              f"{w['delta']:+3d}  {w['headline'][:46]}")
    for a in wl["cohort_alerts"]:
        print(f"  [cohort] {a['cohort']}: {a['students_affected']} students, "
              f"-{a['mean_drop_pct']} pts -> {a['route_to']}")

    top = wl["this_week"][0]["roll_no"]
    d = get_student(con, top)
    print(f"\nStudent {top}: {d['ledger']['score']}/100 {d['ledger']['band']}, "
          f"{len(d['attendance'])} weeks, playbook = {d['suggested_playbook']['title']}")

    iv = create_intervention(con, top, mid, d["suggested_playbook"]["title"],
                             d["suggested_playbook"]["trigger"], "Called, will meet Friday.")
    print(f"  created {iv['id']} -> {close_intervention(con, iv['id'], 'improved', mid)}")
    print("  feedback:", record_feedback(con, top, mid, "confirm_at_risk"))

    eff = get_effectiveness(con)
    print(f"\nEffectiveness ({eff['measured_total']} measured from attendance data):")
    print(f"  {'playbook':36s}{'n':>4}{'naive':>8}{'vs control':>12}")
    for r in eff["measured"]["rows"][:4]:
        print(f"  {r['playbook'][:36]:36s}{r['n']:4d}"
              f"{str(r['improved_rate']) + '%':>8}"
              f"{str(r['estimated_effect']) + ' pts':>12}")

    fair = get_fairness(con)
    print("\nFairness audit:")
    for dim, rows in fair["dimensions"].items():
        worst = rows[0]
        print(f"  {dim:18s} highest flag rate {worst['group']:12s} "
              f"{worst['flag_rate']:5.1f}%  ratio {worst['disparity_ratio']}"
              f"{'  REVIEW' if worst['review'] else ''}")

    print("\nStudent-facing view (no score, no label):")
    pv = get_student_public_view(con, top)
    for s_ in pv["suggestions"]:
        print(f"  - {s_}")

    rep = handle_upload(con, os.path.join(DEMO, "03_fees_and_backlogs.xlsx"), "admin", commit=True)
    print(f"\nRe-upload test: {rep['rows_keyed']} keyed, {rep['applied']} applied, "
          f"{rep['rows_unmatched']} unmatched")
    print(f"Audit trail: {len(get_audit(con))} entries")
    print("\nOK")
