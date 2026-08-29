"""
Sahay - HTTP layer.

Deliberately thin. Every route is a few lines over service.py, which is where
the logic lives and where it is tested. If a route is longer than 6 lines,
the logic belongs in service.py instead.

    pip install fastapi "uvicorn[standard]" python-multipart pandas openpyxl
    python generate_demo_data.py
    python service.py            # builds sahay.db
    uvicorn main:app --reload

    http://127.0.0.1:8000        the app
    http://127.0.0.1:8000/docs   auto-generated API docs (show these to judges)
"""

import io
import os

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional

from pydantic import BaseModel

import service


def _fields(m):
    """pydantic v2 uses model_dump, v1 uses dict. Support both."""
    return m.model_dump() if hasattr(m, "model_dump") else m.dict()

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")

app = FastAPI(title="Sahay - Student Early Warning & Support",
              description="Rules Mode. Transparent additive risk ledger. "
                          "Human approval required for every intervention.",
              version="0.1.0")

if not os.path.exists(service.DB_PATH):
    service.reset_db()
con = service.connect()
service.get_model()          # load once at boot, not on the first request
if service.snapshots_stale(con):
    service.refresh_scores(con)


# ----------------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------------
@app.get("/api/summary")
def summary(mentor: Optional[str] = None):
    return service.get_summary(con, mentor)


@app.get("/api/worklist")
def worklist(mentor: Optional[str] = None, capacity: int = Query(5, ge=1, le=50),
             page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    return service.get_worklist(con, mentor, capacity, page, page_size)


@app.get("/api/students")
def students(mentor: Optional[str] = None, q: str = "", page: int = Query(1, ge=1),
             page_size: int = Query(50, ge=1, le=200)):
    """Paginated. At 20,000 students the full table is not a response body."""
    return service.list_students(con, mentor, q, page, page_size)


@app.get("/api/onboarding")
def onboarding(limit: int = Query(50, ge=1, le=200)):
    """Admitted but not yet scoreable. Visible on purpose."""
    return service.get_onboarding(con, limit)


@app.get("/api/model")
def model_info():
    """What the model predicts, what it refuses to look at, and how it scored
    against the baselines. Point judges here."""
    return service.model_status(con)


@app.get("/api/student/{roll_no}")
def student(roll_no: str):
    d = service.get_student(con, roll_no.upper())
    if not d:
        raise HTTPException(404, "student not found")
    return d


@app.get("/api/student/{roll_no}/public")
def student_public(roll_no: str):
    """What the student sees. No score. No band. No label."""
    d = service.get_student_public_view(con, roll_no.upper())
    if not d:
        raise HTTPException(404, "student not found")
    return d


class WhatIfIn(BaseModel):
    attendance_pct: Optional[float] = None
    latest_ia_pct: Optional[float] = None
    submission_pct: Optional[float] = None
    backlogs: Optional[int] = None
    fee_status: Optional[str] = None


@app.post("/api/student/{roll_no}/whatif")
def whatif(roll_no: str, body: WhatIfIn):
    """Recompute the ledger under hypothetical values.

    Exact, because the score is a sum of fixed rules and this is the same
    arithmetic on different numbers.
    """
    changes = {k: v for k, v in _fields(body).items() if v is not None}
    r = service.what_if(con, roll_no.upper(), changes)
    if r is None:
        raise HTTPException(404, "student not found")
    return r


@app.get("/api/student/{roll_no}/whatif")
def whatif_levers(roll_no: str):
    """The controls and where they currently sit, with no changes applied."""
    r = service.what_if(con, roll_no.upper(), {})
    if r is None:
        raise HTTPException(404, "student not found")
    return r


@app.post("/api/measure")
def measure(actor: str = "admin"):
    """Work out from attendance data whether past interventions helped."""
    return service.measure_outcomes(con, actor)


@app.get("/api/analytics/effectiveness")
def effectiveness():
    return service.get_effectiveness(con)


@app.get("/api/analytics/fairness")
def fairness():
    return service.get_fairness(con)


@app.get("/api/audit")
def audit(limit: int = Query(100, ge=1, le=1000)):
    return service.get_audit(con, limit)


@app.get("/api/config")
def config():
    return service.get_config(con)


@app.get("/api/mentors")
def mentors():
    """Section mentors plus the institution-wide roles."""
    service.load_mentors(con)
    return {"default": service.default_mentor(con),
            "mentors": [{"id": k, **v} for k, v in service.MENTORS.items()]}


# ----------------------------------------------------------------------------
# Write
# ----------------------------------------------------------------------------
class InterventionIn(BaseModel):
    roll_no: str
    mentor: Optional[str] = None
    playbook: str
    trigger: str
    action_text: str = ""
    followup_days: int = 21


class OutcomeIn(BaseModel):
    outcome: str
    mentor: Optional[str] = None
    notes: str = ""


class FeedbackIn(BaseModel):
    roll_no: str
    mentor: Optional[str] = None
    verdict: str
    reason: str = ""


@app.post("/api/interventions")
def create_intervention(body: InterventionIn):
    return service.create_intervention(
        con, body.roll_no.upper(), body.mentor or service.default_mentor(con),
        body.playbook, body.trigger, body.action_text, body.followup_days)


@app.post("/api/interventions/{iv_id}/outcome")
def close_intervention(iv_id: str, body: OutcomeIn):
    try:
        return service.close_intervention(
            con, iv_id, body.outcome, body.mentor or service.default_mentor(con),
            body.notes)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/feedback")
def feedback(body: FeedbackIn):
    try:
        return service.record_feedback(
            con, body.roll_no.upper(), body.mentor or service.default_mentor(con),
            body.verdict, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/config")
def update_config(cfg: dict):
    return service.set_config(con, cfg)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), commit: bool = False, actor: str = "admin"):
    """Preview by default. Nothing is written until commit=true, so the mentor
    always sees and confirms the column mapping first."""
    raw = await file.read()
    buf = io.BytesIO(raw)
    buf.name = file.filename
    try:
        rep = service.handle_upload(con, buf, actor, commit=commit)
        if commit:
            rep["refresh"] = service.refresh_scores(con, actor)
        return rep
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}",
                             "hint": "Check the file has a roll-number column."},
                            status_code=422)


class AdmitIn(BaseModel):
    name: str
    dept: str
    year: int
    section: str = "A"
    admission_date: Optional[str] = None
    roll_no: Optional[str] = None
    gender: Optional[str] = None
    category: Optional[str] = None
    first_gen: Optional[bool] = None
    hostel: Optional[bool] = None
    mentor_id: Optional[str] = None


class AttendanceIn(BaseModel):
    roll_no: str
    week_start: str
    attended: int
    held: int


class AssessmentIn(BaseModel):
    roll_no: str
    which: str
    marks: Optional[float] = None
    max_marks: int = 30


class StatusIn(BaseModel):
    status: str
    note: str = ""


@app.post("/api/students")
def admit(body: AdmitIn, actor: str = "admin"):
    """Register a student on admission day. No academic data needed."""
    try:
        return service.admit_student(con, actor=actor, **_fields(body))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/students/bulk")
def admit_bulk(bodies: List[AdmitIn], actor: str = "admin"):
    """A whole intake. One bad row does not roll back the good ones."""
    return service.admit_students_bulk(con, [_fields(b) for b in bodies], actor)


@app.patch("/api/students/{roll_no}/status")
def student_status(roll_no: str, body: StatusIn, actor: str = "admin"):
    try:
        return service.set_student_status(con, roll_no.upper(), body.status,
                                          actor, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/attendance")
def attendance(body: AttendanceIn, actor: str = "staff"):
    """One week for one student, as attended/held rather than a percentage,
    because that is what a register contains and 34/40 is verifiable."""
    try:
        return service.add_attendance(con, body.roll_no.upper(), body.week_start,
                                      body.attended, body.held, actor)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/assessment")
def assessment(body: AssessmentIn, actor: str = "staff"):
    try:
        return service.add_assessment(con, body.roll_no.upper(), body.which,
                                      body.marks, body.max_marks, actor)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/refresh")
def refresh(actor: str = "admin"):
    """Recompute every score. Runs after ingest and admission; nightly in
    production. Scores change when data changes, not when a page loads."""
    return service.refresh_scores(con, actor)


@app.post("/api/demo/reset")
def demo_reset():
    """The button that saves your demo when something goes wrong on stage."""
    global con
    con.close()
    out = service.reset_db()
    con = service.connect()
    out["refresh"] = service.refresh_scores(con)
    return out


# ----------------------------------------------------------------------------
# Frontend
# ----------------------------------------------------------------------------
if os.path.isdir(STATIC):
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    p = os.path.join(STATIC, "index.html")
    if os.path.exists(p):
        return FileResponse(p)
    return JSONResponse({
        "status": "backend running, frontend not built yet",
        "try": ["/docs", "/api/summary", "/api/worklist", "/api/analytics/effectiveness"],
    })
