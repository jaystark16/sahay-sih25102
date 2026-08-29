"""
Sahay - student lifecycle.

Answers the question "what happens when a student joins the college?"

A student exists in the system from admission day, before any attendance or
marks exist. That creates a cold-start problem that most dropout systems ignore
and then quietly get wrong: with two weeks of data they will happily print a
risk score, and it will be noise dressed as insight.

Our rule instead:

    weeks 0-3     ONBOARDING   no score at all. The profile exists, data is
                               accumulating, and the UI says exactly that.
    weeks 4-9     RULES ONLY   thresholds can fire on observed attendance.
                               No model output: 10 weeks is the model's
                               documented minimum and we do not fake it.
    weeks 10+     FULL         rules ledger and model prediction together.

The stage is shown to the mentor as a badge. A system that admits what it does
not know yet is the one a mentor keeps using.
"""

from datetime import date, datetime, timedelta

ONBOARDING_WEEKS = 4
MODEL_MIN_WEEKS = 10

STAGES = {
    "onboarding": {
        "label": "Collecting data",
        "scoring": "none",
        "explain": "Profile created. Attendance and assessment data are still "
                   "accumulating, so no risk assessment is shown yet.",
    },
    "rules_only": {
        "label": "Rules only",
        "scoring": "rules",
        "explain": "Enough attendance history for threshold rules. Not enough "
                   "for the model, which needs 10 weeks, so no prediction is shown.",
    },
    "full": {
        "label": "Rules + model",
        "scoring": "hybrid",
        "explain": "Full history available. Both the transparent rules ledger and "
                   "the model prediction are shown.",
    },
}

STATUSES = ("enrolled", "active", "on_leave", "graduated", "left")


def stage_for(weeks_of_data):
    if weeks_of_data < ONBOARDING_WEEKS:
        return "onboarding"
    if weeks_of_data < MODEL_MIN_WEEKS:
        return "rules_only"
    return "full"


def stage_info(weeks_of_data):
    s = stage_for(weeks_of_data)
    info = dict(STAGES[s])
    info["stage"] = s
    info["weeks_of_data"] = weeks_of_data
    if s == "onboarding":
        info["weeks_until_next"] = ONBOARDING_WEEKS - weeks_of_data
    elif s == "rules_only":
        info["weeks_until_next"] = MODEL_MIN_WEEKS - weeks_of_data
    else:
        info["weeks_until_next"] = 0
    return info


# ----------------------------------------------------------------------------
# Roll number allocation
# ----------------------------------------------------------------------------
def next_roll_no(con, dept, admission_year):
    """Allocate the next roll number in the institution's own format.

    Format: YYDEPTNNNN, e.g. 26CSE0043. Matches what ingest.normalize_roll
    already reconciles, so a student admitted through this route and the same
    student appearing later in a spreadsheet resolve to one record.
    """
    yy = f"{admission_year % 100:02d}"
    prefix = f"{yy}{dept.upper()}"
    row = con.execute(
        "SELECT roll_no FROM students WHERE roll_no LIKE ? ORDER BY roll_no DESC LIMIT 1",
        (prefix + "%",)).fetchone()
    n = 0
    if row:
        tail = row["roll_no"][len(prefix):]
        if tail.isdigit():
            n = int(tail)
    return f"{prefix}{n + 1:04d}"


# ----------------------------------------------------------------------------
# Admission
# ----------------------------------------------------------------------------
def admit(con, *, name, dept, year, section="A", admission_date=None, roll_no=None,
          gender=None, category=None, first_gen=None, hostel=None,
          cgpa=None, mentor_id=None, actor="admin", audit_fn=None):
    """Create a student record on admission day. No academic data required.

    Only the fields an admissions office actually has on day one. gender and
    category are optional and exist solely so the fairness audit can run; they
    are never scoring inputs and are never shown on the mentor's screen.
    """
    if not name or not dept or not year:
        raise ValueError("name, dept and year are required")
    admission_date = admission_date or date.today().isoformat()
    ad_year = datetime.fromisoformat(admission_date).year
    roll_no = (roll_no or next_roll_no(con, dept, ad_year)).upper()

    if con.execute("SELECT 1 FROM students WHERE roll_no=?", (roll_no,)).fetchone():
        raise ValueError(f"{roll_no} already exists")

    mentor_id = mentor_id or _auto_mentor(con, dept, year, section)
    con.execute(
        "INSERT INTO students (roll_no,name,dept,year,section,gender,category,"
        "first_gen,hostel,cgpa,backlogs,submission_pct,fee_status,mentor_id,"
        "status,admitted_on,term_start) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (roll_no, name.strip(), dept.upper(), int(year), (section or "A").upper(),
         gender, category,
         None if first_gen is None else int(bool(first_gen)),
         None if hostel is None else int(bool(hostel)),
         None if cgpa in (None, "") else float(cgpa),
         0, None, "Unknown", mentor_id, "enrolled", admission_date, admission_date))
    if audit_fn:
        audit_fn(con, actor, "student_admitted", roll_no, f"{name} {dept}-{year}")
    con.commit()
    return {"roll_no": roll_no, "name": name.strip(), "dept": dept.upper(),
            "year": int(year), "section": (section or "A").upper(),
            "status": "enrolled", "admitted_on": admission_date,
            "stage": stage_info(0),
            "message": f"{name} admitted as {roll_no}. No risk assessment will be "
                       f"shown until {ONBOARDING_WEEKS} weeks of data exist."}


def admit_bulk(con, records, actor="admin", audit_fn=None):
    """Whole intake at once. Partial success is reported per row rather than
    rolling the entire batch back, because one bad row in a 600-student intake
    file should not block the other 599."""
    created, failed = [], []
    for i, r in enumerate(records):
        try:
            created.append(admit(con, actor=actor, audit_fn=audit_fn, **r))
        except Exception as e:
            failed.append({"row": i, "input": r, "error": f"{type(e).__name__}: {e}"})
    return {"created": len(created), "failed": len(failed),
            "students": created[:50], "errors": failed[:20]}


def _auto_mentor(con, dept, year, section="A"):
    """The mentor who already looks after this section.

    Whatever this returns must be a mentor that actually exists. Handing back
    an id with no mentor row makes the student invisible to every mentor -- the
    record saves, and then nobody ever sees it.
    """
    row = con.execute(
        "SELECT s.mentor_id, COUNT(*) c FROM students s "
        "JOIN mentors m ON m.id = s.mentor_id "
        "WHERE s.dept=? AND s.year=? AND s.section=? "
        "GROUP BY s.mentor_id ORDER BY c DESC LIMIT 1",
        (dept.upper(), int(year), (section or "A").upper())).fetchone()
    if row:
        return row["mentor_id"]
    # New section: fall back to an existing mentor rather than inventing an id
    # for a mentor record that was never created.
    any_mentor = con.execute(
        "SELECT id FROM mentors WHERE role='mentor' ORDER BY id LIMIT 1").fetchone()
    if any_mentor:
        return any_mentor["id"]
    return f"m{str(dept).lower()}{year}{str(section or 'A').lower()}"


def set_status(con, roll_no, status, actor="admin", note="", audit_fn=None):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    cur = con.execute("SELECT status FROM students WHERE roll_no=?", (roll_no,)).fetchone()
    if not cur:
        raise ValueError("student not found")
    con.execute("UPDATE students SET status=? WHERE roll_no=?", (status, roll_no))
    if audit_fn:
        audit_fn(con, actor, "status_changed", roll_no, f"{cur['status']} -> {status} {note}")
    con.commit()
    return {"roll_no": roll_no, "from": cur["status"], "to": status}


# ----------------------------------------------------------------------------
# Data accumulation after admission
# ----------------------------------------------------------------------------
def record_attendance(con, roll_no, week_start, attended, held, actor="staff",
                      audit_fn=None):
    """One week of attendance for one student.

    Takes classes attended and classes held rather than a percentage, because
    that is what a register actually contains, and because 34/40 is verifiable
    while 85% is not.
    """
    if held is None or int(held) <= 0:
        raise ValueError("held must be a positive number of classes")
    if int(attended) < 0 or int(attended) > int(held):
        raise ValueError("attended must be between 0 and held")
    s = con.execute("SELECT term_start FROM students WHERE roll_no=?", (roll_no,)).fetchone()
    if not s:
        raise ValueError("student not found")

    term_start = datetime.fromisoformat(s["term_start"] or "2025-07-07").date()
    ws = datetime.fromisoformat(week_start).date()
    idx = (ws - term_start).days // 7
    if idx < 0:
        raise ValueError("week_start is before the student's term start")

    pct = round(int(attended) / int(held) * 100, 1)
    con.execute("INSERT INTO attendance (roll_no,week_index,week_start,pct,attended,held) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(roll_no,week_index) DO UPDATE SET "
                "pct=excluded.pct, attended=excluded.attended, held=excluded.held",
                (roll_no, idx, week_start, pct, int(attended), int(held)))
    con.execute("UPDATE students SET status=CASE WHEN status='enrolled' THEN 'active' "
                "ELSE status END, weeks_of_data=(SELECT COUNT(*) FROM attendance a "
                "WHERE a.roll_no=?) WHERE roll_no=?", (roll_no, roll_no))
    if audit_fn:
        audit_fn(con, actor, "attendance_recorded", roll_no, f"w{idx} {attended}/{held}")
    con.commit()

    weeks = con.execute("SELECT COUNT(*) c FROM attendance WHERE roll_no=?",
                        (roll_no,)).fetchone()["c"]
    return {"roll_no": roll_no, "week_index": idx, "pct": pct,
            "weeks_of_data": weeks, "stage": stage_info(weeks)}


def record_assessment(con, roll_no, which, marks, max_marks=30, actor="staff",
                      audit_fn=None):
    if which not in ("ia1", "ia2", "ia3"):
        raise ValueError("which must be ia1, ia2 or ia3")
    if marks is not None and (float(marks) < 0 or float(marks) > float(max_marks)):
        raise ValueError(f"marks must be between 0 and {max_marks}")
    if not con.execute("SELECT 1 FROM students WHERE roll_no=?", (roll_no,)).fetchone():
        raise ValueError("student not found")
    con.execute(f"UPDATE students SET {which}=?, ia_max=? WHERE roll_no=?",
                (None if marks is None else float(marks), int(max_marks), roll_no))
    if audit_fn:
        audit_fn(con, actor, "assessment_recorded", roll_no,
                 f"{which}={marks}/{max_marks}")
    con.commit()
    return {"roll_no": roll_no, which: marks, "max": max_marks}


def update_record(con, roll_no, *, fee_status=None, backlogs=None,
                  submission_pct=None, section=None, actor="staff", audit_fn=None):
    sets, args, changed = [], [], {}
    for col, val in (("fee_status", fee_status), ("backlogs", backlogs),
                     ("submission_pct", submission_pct), ("section", section)):
        if val is not None:
            sets.append(f"{col}=?")
            args.append(val)
            changed[col] = val
    if not sets:
        return {"roll_no": roll_no, "changed": {}}
    con.execute(f"UPDATE students SET {','.join(sets)} WHERE roll_no=?",
                (*args, roll_no))
    if audit_fn:
        audit_fn(con, actor, "record_updated", roll_no, str(changed))
    con.commit()
    return {"roll_no": roll_no, "changed": changed}


def weeks_of_data(con, roll_no):
    return con.execute("SELECT COUNT(*) c FROM attendance WHERE roll_no=?",
                       (roll_no,)).fetchone()["c"]


def onboarding_queue(con, limit=50):
    """Students admitted but not yet scoreable. The screen that tells a mentor
    'these exist, we are watching, there is nothing to act on yet'."""
    # Reads the denormalised counter. The GROUP BY version joined half a million
    # attendance rows on every page load.
    rows = con.execute(
        "SELECT roll_no, name, dept, year, section, admitted_on, "
        "weeks_of_data AS weeks FROM students "
        "WHERE status IN ('enrolled','active') AND weeks_of_data < ? "
        "ORDER BY admitted_on DESC, roll_no LIMIT ?",
        (MODEL_MIN_WEEKS, limit)).fetchall()
    return [{**dict(r), "stage": stage_info(r["weeks"])} for r in rows]
