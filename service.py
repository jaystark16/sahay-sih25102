"""
Sahay - service layer.

All business logic and persistence. Deliberately free of any FastAPI import so
you can unit-test it with plain python, and so main.py stays a thin router.

    python service.py     # rebuilds sahay.db from demo_data/ and self-tests
"""

import csv
import json
import os
import sqlite3
from datetime import date, datetime, timedelta

import lifecycle
import ml
import outcomes
from ingest import ingest_file
from risk_engine import (DEFAULT_CONFIG, WHAT_IF_FIELDS, apply_changes,
                         build_worklist, current_levers, detect_cohort_anomalies,
                         intervention_effectiveness, minimum_change_to, risk_delta,
                         score_student, suggest_playbook)
from risk_engine import what_if as what_if_engine

BASE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(BASE, "demo_data")
DB_PATH = os.path.join(BASE, "sahay.db")

# Only the institution-wide roles are fixed. Section mentors are generated from
# the data, one per section, because a mentor with 4,883 mentees is not a
# mentor. A real caseload is about forty, and the whole product argument rests
# on a worklist a person can actually work through.
STAFF = {
    "hod":   {"name": "Dr. A. Bose", "role": "hod", "scope": (None, None)},
    "admin": {"name": "Registrar",   "role": "admin", "scope": (None, None)},
}
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
    except sqlite3.OperationalError:
        pass
    MENTORS = m
    return m


def _seed_mentors(con):
    """One mentor per section, so a caseload is about forty students."""
    sections = con.execute(
        "SELECT dept, year, section, COUNT(*) n FROM students "
        "WHERE dept IS NOT NULL GROUP BY dept, year, section ORDER BY dept, year, section"
    ).fetchall()
    rows = []
    for i, r in enumerate(sections):
        mid = f"m{r['dept']}{r['year']}{r['section']}".lower()
        name = (f"{MENTOR_TITLES[i % len(MENTOR_TITLES)]} "
                f"{MENTOR_INITIALS[i % len(MENTOR_INITIALS)]}. "
                f"{MENTOR_SURNAMES[i % len(MENTOR_SURNAMES)]}")
        rows.append((mid, name, "mentor", r["dept"], r["year"], r["section"]))
    con.execute("DELETE FROM mentors")
    con.executemany("INSERT INTO mentors (id,name,role,dept,year,section) "
                    "VALUES (?,?,?,?,?,?)", rows)
    for mid, _n, _r, dept, year, sec in rows:
        con.execute("UPDATE students SET mentor_id=? WHERE dept=? AND year=? "
                    "AND section=?", (mid, dept, year, sec))
    con.commit()
    load_mentors(con)
    return len(rows)

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
  roll_no TEXT PRIMARY KEY, name TEXT, dept TEXT, year INT, section TEXT,
  gender TEXT, category TEXT, first_gen INT, hostel INT,
  ia1 REAL, ia2 REAL, ia3 REAL, ia_max INT DEFAULT 30,
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
  id INTEGER PRIMARY KEY AUTOINCREMENT, roll_no TEXT, mentor TEXT,
  verdict TEXT, reason TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT,
  subject TEXT, detail TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, actor TEXT,
  rows_keyed INT, rows_unmatched INT, report TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS risk_snapshots (
  roll_no TEXT PRIMARY KEY, score INT, band TEXT, delta INT, priority REAL,
  confidence REAL, headline TEXT, primary_driver TEXT, stage TEXT,
  model_pct REAL, anomaly INT, cohort TEXT, guardrail TEXT,
  dept TEXT, year INT, section TEXT, mentor_id TEXT, name TEXT, computed_at TEXT
);
CREATE TABLE IF NOT EXISTS mentors (
  id TEXT PRIMARY KEY, name TEXT, role TEXT, dept TEXT, year INT, section TEXT
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
CREATE INDEX IF NOT EXISTS ix_students_weeks ON students(weeks_of_data);
"""


def connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    load_mentors(con)
    return con


def audit(con, actor, action, subject="", detail=""):
    con.execute("INSERT INTO audit (actor,action,subject,detail,at) VALUES (?,?,?,?,?)",
                (actor, action, subject, detail, datetime.now().isoformat(timespec="seconds")))


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
def get_config(con):
    row = con.execute("SELECT v FROM settings WHERE k='config'").fetchone()
    return json.loads(row["v"]) if row else DEFAULT_CONFIG


def set_config(con, cfg, actor="admin"):
    con.execute("INSERT INTO settings (k,v) VALUES ('config',?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(cfg),))
    audit(con, actor, "config_changed", "config", json.dumps(cfg)[:400])
    con.commit()
    return cfg


# ----------------------------------------------------------------------------
# Seeding
# ----------------------------------------------------------------------------
def _assign_mentor(dept, year, section="A"):
    """The mentor for a section, creating one if the section is new."""
    return f"m{str(dept).lower()}{year}{str(section).lower()}"


def reset_db(actor="admin"):
    """Rebuild everything from demo_data/. This is the Reset Demo button."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = connect()
    con.executescript(SCHEMA)

    weeks = {}
    with open(os.path.join(DEMO, "week_starts.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            weeks[int(r["week_index"])] = r["week_start"]

    n = 0
    with open(os.path.join(DEMO, "master.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            year = int(r["year"])
            con.execute(
                "INSERT INTO students (roll_no,name,dept,year,section,gender,category,"
                "first_gen,hostel,ia1,ia2,ia3,backlogs,submission_pct,fee_status,"
                "mentor_id,last_contact_at,status,admitted_on,term_start) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active','2025-07-07','2025-07-07')",
                (r["roll_no"], r["name"], r["dept"], year, r["section"], r["gender"],
                 r["category"], int(r["first_gen"]), int(r["hostel"]),
                 float(r["ia1"]) if r["ia1"] else None,
                 float(r["ia2"]) if r["ia2"] else None,
                 float(r["ia3"]) if r["ia3"] else None,
                 int(r["backlogs"]), float(r["submission_pct"]), r["fee_status"],
                 _assign_mentor(r["dept"], year, r["section"]), None))
            for i in range(26):
                pct = float(r[f"w{i:02d}"])
                con.execute("INSERT INTO attendance (roll_no,week_index,week_start,"
                            "pct,attended,held) VALUES (?,?,?,?,?,?)",
                            (r["roll_no"], i, weeks[i], pct,
                             int(round(pct / 100 * 40)), 40))
            n += 1

    with open(os.path.join(DEMO, "interventions_seed.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            con.execute(
                "INSERT INTO interventions (id,roll_no,mentor,trigger,playbook,"
                "action_text,approved_by_mentor,created_at,followup_at,status,outcome)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], r["roll_no"], r["mentor"], r["trigger"], r["playbook"],
                 "", 1, r["created_at"], r["followup_at"], r["status"], r["outcome"]))
            con.execute("UPDATE students SET last_contact_at=? WHERE roll_no=? "
                        "AND (last_contact_at IS NULL OR last_contact_at < ?)",
                        (r["created_at"], r["roll_no"], r["created_at"]))

    con.execute("UPDATE students SET weeks_of_data = (SELECT COUNT(*) FROM attendance a "
                "WHERE a.roll_no = students.roll_no)")
    set_config(con, DEFAULT_CONFIG, actor)
    audit(con, actor, "demo_reset", "database", f"{n} students seeded")
    con.commit()
    # Work out from the attendance data whether each seeded intervention helped,
    # so the effectiveness screen is measured rather than asserted from day one.
    n_mentors = _seed_mentors(con)
    m = outcomes.measure_all(con, audit_fn=audit, actor=actor)
    joined = _seed_recent_admissions(con, actor)
    con.execute("UPDATE students SET weeks_of_data = (SELECT COUNT(*) FROM attendance a "
                "WHERE a.roll_no = students.roll_no)")
    con.commit()
    return {"students": n, "db": DB_PATH, "outcomes_measured": m["measured"],
            "outcome_breakdown": m["breakdown"], "recent_admissions": joined,
            "mentors": n_mentors}


def _seed_recent_admissions(con, actor="system"):
    """A handful of students who joined recently, with partial history.

    Without these every seeded student has a full term of data and the three
    scoring stages are invisible. The demo needs at least one student in each.
    """
    from datetime import date as _d
    plan = [("Meera Nair", "CSE", 1, 0), ("Arjun Pillai", "CSE", 1, 2),
            ("Kavya Reddy", "ECE", 1, 5), ("Imran Shaikh", "ECE", 1, 8),
            ("Nandini Bose", "MEC", 1, 12), ("Rahul Menon", "MEC", 1, 14)]
    made = []
    for name, dept, year, weeks in plan:
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
                        "(roll_no,week_index) DO UPDATE SET pct=excluded.pct",
                        (st["roll_no"], w,
                         (start + timedelta(weeks=w)).isoformat(),
                         round(pct, 1), int(round(pct / 100 * 40)), 40))
        con.execute("UPDATE students SET status=?, weeks_of_data=? WHERE roll_no=?",
                    ("active" if weeks else "enrolled", weeks, st["roll_no"]))
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
    if mentor_id and MENTORS.get(mentor_id, {}).get("role") == "mentor":
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
    try:
        mtime = os.path.getmtime(ml.MODEL_PATH)
    except OSError:
        _BUNDLE, _BUNDLE_MTIME = None, None
        return None
    if _BUNDLE is None or mtime != _BUNDLE_MTIME:
        try:
            _BUNDLE = ml.load_bundle()
            _BUNDLE_MTIME = mtime
        except Exception:
            _BUNDLE, _BUNDLE_MTIME = None, None
    return _BUNDLE


def model_status(con):
    b = get_model()
    if not b:
        return {"trained": False, "mode": "Rules Mode",
                "why": "No model file. Threshold rules only, which is the correct "
                       "state before an institution has enough history."}
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
            out["model_note"] = ("No prediction model has been trained yet, so this "
                                 "student is scored on the transparent rules alone. "
                                 "Run the training step to enable predictions.")
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
    """Score a whole cohort. Rules in python, the model in ONE matrix call."""
    b = get_model()
    mlres = {}
    if b:
        payload = [{"roll_no": s["roll_no"],
                    "att": s["features"]["weekly_attendance"],
                    "marks": [s["features"].get("ia1"), s["features"].get("ia2"),
                              s["features"].get("ia3")]} for s in students]
        try:
            mlres = ml.predict_batch(b, payload)
        except Exception:
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

    meta = {r["roll_no"]: dict(r) for r in con.execute(
        "SELECT roll_no,name,dept,year,section,mentor_id FROM students")}
    now = datetime.now().isoformat(timespec="seconds")
    # Precompute once. Searching `students` per row is O(N^2) and at 20,000
    # students that alone is minutes.
    weeks_by_roll = {s["roll_no"]: len(s["features"]["weekly_attendance"])
                     for s in students}
    rows = []
    for item in wl["this_week"] + wl["watch"] + wl["routed_to_cohort"]:
        rn = item["roll_no"]
        m = mlres.get(rn) or {}
        md = meta.get(rn, {})
        weeks = weeks_by_roll.get(rn, 0)
        rows.append((rn, item["score"], item["band"], item["delta"], item["priority"],
                     item["confidence"], item["headline"], item["primary_driver"],
                     lifecycle.stage_for(weeks),
                     m.get("percent") if m.get("available") else None,
                     int(bool(m.get("anomaly_unusual"))),
                     (item.get("explained_by_cohort") or {}).get("cohort"),
                     json.dumps(item.get("guardrails") or []),
                     md.get("dept"), md.get("year"), md.get("section"),
                     md.get("mentor_id"), md.get("name"), now))

    con.execute("DELETE FROM risk_snapshots")
    con.executemany(
        "INSERT INTO risk_snapshots (roll_no,score,band,delta,priority,confidence,"
        "headline,primary_driver,stage,model_pct,anomaly,cohort,guardrail,dept,"
        "year,section,mentor_id,name,computed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("DELETE FROM cohort_alerts")
    con.executemany("INSERT INTO cohort_alerts VALUES (?,?,?)",
                    [(a["cohort"], json.dumps(a), now) for a in alerts])
    audit(con, actor, "scores_refreshed", "cohort",
          f"{len(rows)} scored, {len(alerts)} cohort alerts")
    con.commit()
    return {"scored": len(rows), "cohort_alerts": len(alerts),
            "seconds": round(_t.time() - t0, 2), "at": now}


def snapshots_stale(con):
    return con.execute("SELECT COUNT(*) c FROM risk_snapshots").fetchone()["c"] == 0


def default_mentor(con):
    """The first section mentor, used when no one is named."""
    r = con.execute("SELECT id FROM mentors WHERE role='mentor' ORDER BY id "
                    "LIMIT 1").fetchone()
    return r["id"] if r else "admin"


def get_worklist(con, mentor_id=None, capacity=5, page=1, page_size=50):
    mentor_id = mentor_id or default_mentor(con)
    """Served from risk_snapshots. Flat in cohort size: an indexed SQL query
    returns the top `capacity` rows whether the college has 400 students or
    40,000."""
    if snapshots_stale(con):
        refresh_scores(con)

    scope, args = "1=1", []
    if MENTORS.get(mentor_id, {}).get("role") == "mentor":
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

    individual = "cohort IS NULL"
    this_week = rows(individual, limit=capacity)
    total_flagged = count(individual)
    watch_total = max(0, total_flagged - capacity)
    watch = rows(individual, limit=page_size, offset=capacity + (page - 1) * page_size)

    alerts = [json.loads(r["payload"]) for r in
              con.execute("SELECT payload FROM cohort_alerts")]
    stamp = con.execute("SELECT MAX(computed_at) m FROM risk_snapshots").fetchone()["m"]
    in_scope = con.execute(f"SELECT COUNT(*) c FROM students WHERE "
                           f"{scope.replace('mentor_id', 'mentor_id')}", args).fetchone()["c"]

    return {"this_week": this_week, "watch": watch,
            "watch_page": {"page": page, "page_size": page_size, "total": watch_total,
                           "pages": max(1, -(-watch_total // page_size))},
            "routed_to_cohort_count": count("cohort IS NOT NULL"),
            "capacity": capacity, "total_flagged": total_flagged,
            "total_routed": count("cohort IS NOT NULL"),
            "cohort_alerts": alerts,
            "mentor": MENTORS.get(mentor_id, {}).get("name", mentor_id),
            "mentor_id": mentor_id, "students_in_scope": in_scope,
            "mode": model_status(con)["mode"],
            "onboarding": lifecycle.onboarding_queue(con, 20),
            "scores_computed_at": stamp}


def list_students(con, mentor_id=None, q="", risk="all", page=1, page_size=50):
    """Paginated directory. At 20,000 students you cannot ship the whole table."""
    where, args = ["1=1"], []
    if mentor_id and MENTORS.get(mentor_id, {}).get("role") == "mentor":
        where.append("s.mentor_id=?")
        args.append(mentor_id)
    if q:
        where.append("(s.roll_no LIKE ? OR s.name LIKE ?)")
        args += [f"%{q.upper()}%", f"%{q}%"]
        
    if risk == "at_risk":
        where.append("r.band IN ('Medium', 'High')")

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
    mentor_id = mentor_id or default_mentor(con)
    if snapshots_stale(con):
        refresh_scores(con)
    scope, args = "1=1", []
    if MENTORS.get(mentor_id, {}).get("role") == "mentor":
        scope, args = "mentor_id=?", [mentor_id]
    bands = {"Low": 0, "Medium": 0, "High": 0}
    for r in con.execute(f"SELECT band, COUNT(*) c FROM risk_snapshots WHERE {scope} "
                         f"GROUP BY band", args):
        bands[r["band"]] = r["c"]
    total = con.execute(f"SELECT COUNT(*) c FROM students WHERE {scope}", args).fetchone()["c"]
    rising = con.execute(f"SELECT COUNT(*) c FROM risk_snapshots WHERE {scope} "
                         f"AND delta >= 10", args).fetchone()["c"]
    scored = sum(bands.values())
    return {"total_students": total, "scored": scored,
            "not_yet_scoreable": total - scored, "bands": bands, "rising": rising,
            "open_interventions": con.execute(
                "SELECT COUNT(*) c FROM interventions WHERE status='open'").fetchone()["c"],
            "mode": model_status(con)["mode"], "data_source": "Synthetic demo data"}


def get_dashboard(con, mentor_id=None):
    mentor_id = mentor_id or default_mentor(con)
    scope, args = "1=1", []
    if MENTORS.get(mentor_id, {}).get("role") == "mentor":
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
        f"SELECT AVG((IFNULL(ia1,0)+IFNULL(ia2,0)+IFNULL(ia3,0))/(ia_max*3.0)*10.0) as cgpa "
        f"FROM students WHERE {scope} AND ia_max > 0", args
    ).fetchone()
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
                                      "backlogs", "submission_pct", "fee_status",
                                      "ia1", "ia2", "ia3", "ia_max", "last_contact_at",
                                      "status", "admitted_on")},
        "stage": stage,
        "ledger": ledger if stage["scoring"] != "none" else None,
        "hybrid": _hybrid(ledger, mlres, stage),
        "forecast": fc,
        "delta": d if stage["scoring"] != "none" else None,
        "attendance": [dict(a) for a in att],
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
    n = con.execute("SELECT COUNT(*) c FROM interventions").fetchone()["c"]
    iid = f"IV{n + 1:04d}"
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
                (stamp, json.dumps(out, default=str),
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
                                "(roll_no,week_index) DO UPDATE SET pct=excluded.pct",
                                (rec["roll_no"], i, w["week"], w["attendance_pct"]))
            applied += 1
        con.commit()

    con.execute("INSERT INTO uploads (filename,actor,rows_keyed,rows_unmatched,report,at) "
                "VALUES (?,?,?,?,?,?)",
                (str(getattr(path_or_buffer, "name", path_or_buffer))[:200], actor,
                 report["rows_keyed"], report["rows_unmatched"],
                 json.dumps(report)[:4000], datetime.now().isoformat(timespec="seconds")))
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
def admit_student(con, **kw):
    return lifecycle.admit(con, audit_fn=audit, **kw)


def admit_students_bulk(con, records, actor="admin"):
    return lifecycle.admit_bulk(con, records, actor=actor, audit_fn=audit)


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


def get_onboarding(con, limit=50):
    return lifecycle.onboarding_queue(con, limit)

if __name__ == "__main__":
    print("Rebuilding database...")
    print(" ", reset_db())
    con = connect()

    mid = default_mentor(con)
    s = get_summary(con, mid)
    print(f"\nSummary  {s['total_students']} students  {s['bands']}  "
          f"rising={s['rising']}  open interventions={s['open_interventions']}")
    wl = get_worklist(con, mid, 5)
    print(f"\nWorklist for {wl['mentor']}  ({wl['students_in_scope']} in scope, "
          f"{wl['total_flagged']} flagged, {wl['total_routed']} routed to HOD)")
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
