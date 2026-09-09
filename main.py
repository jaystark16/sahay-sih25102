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
from contextlib import asynccontextmanager

import psycopg2
from fastapi import (Depends, FastAPI, File, HTTPException, Query, Request,
                     UploadFile)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from typing import Annotated, List, Optional

from pydantic import BaseModel, Field

import auth
import database
import jsonsafe
import service


class SafeJSONResponse(JSONResponse):
    """The single place every response body is serialised.

    Starlette's JSONResponse renders with allow_nan=False, so one NaN anywhere
    in a payload is a ValueError and the client gets an unexplained HTTP 500 --
    on an endpoint that worked until someone uploaded a spreadsheet. The
    individual NaN sources are fixed at their sources (see risk_engine._finite
    and ingest.parse_marks), but this is the net under all of them: a value that
    cannot be represented becomes null rather than an outage.
    """

    def render(self, content) -> bytes:
        return jsonsafe.dumps(content, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")


def error_body(code: str, message: str, hint: str = None, fields=None):
    """The one error shape this API returns.

    There were five: HTTPException's {"detail": "..."}, request validation's
    {"detail": [{...}]} -- same key, incompatible type -- the hand-rolled
    {"error": ..., "hint": ...} on /api/upload, a bare 500, and per-row
    {"errors": [...]} at status 200. A client could not tell them apart, and
    the frontend's `throw new Error(d.detail)` rendered "[object Object]"
    whenever validation failed. `fields` carries the per-field detail that used
    to overload `detail`.
    """
    err = {"code": code, "message": message}
    if hint:
        err["hint"] = hint
    if fields:
        err["fields"] = fields
    return {"error": err}


def _fields(m):
    """pydantic v2 uses model_dump, v1 uses dict. Support both."""
    return m.model_dump() if hasattr(m, "model_dump") else m.dict()


# Every inbound float must be finite.
#
# json.loads accepts the bare NaN and Infinity literals, and pydantic's float
# has allow_inf_nan=True by default, so {"cgpa": NaN} was a perfectly valid
# request body. The value was then stored, or handed to the response encoder --
# where Starlette's json.dumps(..., allow_nan=False) raised and the client got
# an opaque 500, sometimes permanently, because the NaN had been committed.
# Refusing it at the edge turns that into a 422 that names the field.
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]

BASE = os.path.dirname(os.path.abspath(__file__))

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Schema, model and seed data -- once at startup, not per request.

    All of this used to run at import time, which meant a database outage
    presented as a process that refused to start rather than an API returning
    an error, and the full DDL script re-ran on every connect().

    A database failure here is logged and swallowed rather than raised. If it
    propagated, the process would exit, and on a host that restarts crashed
    services that is a crash loop -- with the real reason scrolling past in
    between restarts. Starting anyway means /docs answers the health check,
    the logs hold one clear message, and the routes return the standard
    database_error envelope until the database comes back. Nothing here is
    required for the process to serve a request: init_schema is idempotent and
    the pool opens connections per request regardless.
    """
    service.get_model()          # local file, no database involved
    try:
        con = database.checkout()
    except Exception as e:
        print(f"WARNING: no database at startup ({type(e).__name__}: {e}). "
              f"Serving anyway; API routes will error until it is reachable. "
              f"Check SUPABASE_DATABASE_URL.")
    else:
        try:
            service.init_schema(con)
            auth.ensure_auth_schema(con)
            auth.seed_users(con, service.MENTORS)
            if service.db_is_empty(con):
                # Deliberately do NOT seed the demo data here. Against a local
                # SQLite file that was cheap. Against a remote Postgres it is a
                # 5,000-student write that takes minutes, holds the port
                # closed, and would time out a platform health check before the
                # process ever became reachable. Seeding is explicit:
                #   python service.py   (or POST /api/demo/reset, authenticated)
                print("WARNING: no students in the database. The API will "
                      "serve empty results until you seed it with:  "
                      "python service.py")
            elif service.snapshots_stale(con):
                service.refresh_scores(con)
        except Exception as e:
            print(f"WARNING: startup database work failed "
                  f"({type(e).__name__}: {e}). Serving anyway.")
        finally:
            con.release()

    yield

    database.close_pool()


app = FastAPI(title="Sahay - Student Early Warning & Support",
              description="Rules Mode. Transparent additive risk ledger. "
                          "Human approval required for every intervention.",
              version="0.1.0",
              default_response_class=SafeJSONResponse,
              lifespan=lifespan)


# ----------------------------------------------------------------------------
# Error handling. Four handlers, one body shape -- see error_body().
# ----------------------------------------------------------------------------
_STATUS_CODES = {
    400: "bad_request", 401: "unauthenticated", 403: "forbidden",
    404: "not_found", 409: "conflict", 422: "validation_failed",
}


@app.exception_handler(StarletteHTTPException)
def handle_http_exception(request: Request, exc: StarletteHTTPException):
    code = _STATUS_CODES.get(exc.status_code, "error")
    detail = exc.detail
    # A handler may pass a dict through HTTPException to add a hint.
    if isinstance(detail, dict):
        body = error_body(detail.get("code", code), detail.get("message", ""),
                          detail.get("hint"), detail.get("fields"))
    else:
        body = error_body(code, str(detail))
    return SafeJSONResponse(body, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError):
    """Request validation, flattened into `fields`.

    FastAPI's default puts a list of objects under `detail`, the same key that
    carries a plain string for every other error. That type collision is what
    made the frontend render "[object Object]", so the per-field detail moves
    to its own key and `message` stays a string the UI can always display.
    """
    fields = [{"field": ".".join(str(p) for p in e.get("loc", ())[1:]) or "body",
               "message": e.get("msg", "")} for e in exc.errors()]
    summary = "; ".join(f'{f["field"]}: {f["message"]}' for f in fields[:3])
    return SafeJSONResponse(
        error_body("validation_failed", summary or "Request validation failed",
                   hint="Check the highlighted fields and try again.",
                   fields=fields),
        status_code=422)


@app.exception_handler(psycopg2.Error)
def handle_db_error(request: Request, exc: psycopg2.Error):
    """Database errors, without leaking SQL to the client.

    The message goes to the server log; the client gets a stable code. Note
    that get_con() has already rolled the transaction back by the time this
    runs, so the connection is safe to reuse.
    """
    print(f"ERROR db {request.method} {request.url.path}: "
          f"{type(exc).__name__}: {exc}")
    return SafeJSONResponse(
        error_body("database_error", "The database rejected this request.",
                   hint="This is a server-side problem, not your input."),
        status_code=500)


@app.exception_handler(Exception)
def handle_unexpected(request: Request, exc: Exception):
    print(f"ERROR {request.method} {request.url.path}: "
          f"{type(exc).__name__}: {exc}")
    return SafeJSONResponse(
        error_body("internal_error", "Something went wrong handling this request."),
        status_code=500)

# ----------------------------------------------------------------------------
# Database connections
# ----------------------------------------------------------------------------
def get_con():
    """One pooled connection per request.

    Every route depends on this rather than sharing a module-level connection.
    The old global broke three ways at once: concurrent requests used it
    simultaneously from FastAPI's threadpool (psycopg2 connections are not
    safe for that), a single failed statement left the transaction aborted so
    every later request returned InFailedSqlTransaction, and when Supabase's
    pooler dropped the idle connection every route thereafter returned
    "connection already closed" until the process was restarted.

    The rollback in the finally block is what keeps a failed request from
    poisoning the next one that picks up the same connection.
    """
    con = database.checkout()
    try:
        yield con
    except Exception:
        con.rollback()
        raise
    finally:
        con.release()


# Allowed browser origins. The frontend is deployed separately (Vercel) from
# the API, so the deployed origin has to be listed or every request from it is
# blocked -- and it cannot be hardcoded here, because it changes per
# deployment. Set CORS_ORIGINS to a comma-separated list.
#
# The localhost entries cover `npm run dev`: vite.config.js pins port 3000 and
# proxies /api, and 5173 is Vite's own default.
_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173",
                "http://localhost:3000", "http://127.0.0.1:3000"]
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",")
                if o.strip()] or _DEV_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------------
class LoginIn(BaseModel):
    user_id: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


def _token_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return ""


# ----------------------------------------------------------------------------
# Authentication dependencies
#
# 32 of the 36 routes had no authentication at all -- including PUT /api/config,
# POST /api/upload and POST /api/demo/reset, which drops every table. These
# three dependencies are how every route gets a caller, and how `actor` stops
# being a client-supplied query parameter that anyone could set to "admin".
# ----------------------------------------------------------------------------
def require_user(request: Request, con=Depends(get_con)):
    """Any signed-in account."""
    user = auth.verify_token(con, _token_from_request(request))
    if not user:
        raise HTTPException(401, {
            "code": "unauthenticated",
            "message": "Sign in to continue.",
        })
    return user


def require_active_user(user=Depends(require_user)):
    """A signed-in account that has finished setting itself up.

    An account seeded with a generated password can do nothing but change that
    password, which is what makes must_change_password more than a suggestion.
    """
    if user.get("must_change_password"):
        raise HTTPException(403, {
            "code": "password_change_required",
            "message": "Set a new password before using the app.",
            "hint": "POST /api/auth/change-password",
        })
    return user


def scoped(mentor: Optional[str] = None, user=Depends(require_active_user)):
    """(user, mentor_id) where mentor_id is what this session may actually see.

    The one place a request's scope is decided, and it is decided from the
    session rather than the query string.

    Before this, every scoped route took `mentor` straight from the URL and
    passed it to the service layer, which filtered only when the name happened
    to belong to a mentor. So `?mentor=` omitted returned the entire
    institution to any signed-in account, and `?mentor=someone_else` returned
    that person's caseload. Authentication was enforced; authorization was not.

    A mentor is now pinned to their own id and gets 403 for anyone else's.
    Institution roles may pass a mentor name to narrow the view, which is what
    the HOD's per-mentor drill-down uses, or omit it for the whole institution.
    """
    try:
        return user, service.resolve_scope(user, mentor)
    except service.ScopeDenied as e:
        raise HTTPException(403, {
            "code": "forbidden_scope",
            "message": "You can only see the students assigned to you.",
            "hint": str(e),
        })


def require_institution(user=Depends(require_active_user)):
    """Institution-wide operations: HOD, principal, admin."""
    if not service.is_institution_role(user.get("role")):
        raise HTTPException(403, {
            "code": "forbidden",
            "message": "This view is for the head of department or principal.",
        })
    return user


def authorize_student(roll_no: str, con, user):
    """403 unless this session may open this student's record.

    get_student() had no scope check at all, so any signed-in mentor could read
    any of the 5,003 records by roll number. Scoping the list endpoints without
    scoping this one would only have hidden the directory, not the data.
    """
    if not service.may_view_student(con, user, roll_no):
        raise HTTPException(403, {
            "code": "forbidden_student",
            "message": "That student is not on your caseload.",
            "hint": "Ask the head of department to assign them to you.",
        })


def require_staff(user=Depends(require_active_user)):
    """Any staff account: mentor, HOD, principal or admin.

    This is the "you work here" gate, not the "you may see everything" gate.
    Institution-wide access is require_institution and per-student access is
    authorize_student; a route that only needs the first must not accidentally
    grant the others.
    """
    if user["role"] not in ("mentor", "hod", "principal", "admin", "staff"):
        raise HTTPException(403, {
            "code": "forbidden",
            "message": "This action is restricted to staff accounts.",
        })
    return user


# The old name, kept because ~15 routes reference it. Same meaning it always
# had -- "a staff account" -- with hod and principal added, since they are
# staff and were being refused config, uploads and admissions.
require_mentor = require_staff


def actor_id(user=Depends(require_mentor)) -> str:
    """Who is performing a write, for the audit trail.

    Every mutating route used to take `actor` as a query parameter defaulting
    to "admin", so the audit log recorded whatever the caller typed. Injecting
    it means the recorded actor is the verified session, and `actor` disappears
    from the public API surface -- while the route bodies, which already refer
    to a local named `actor`, keep working untouched.
    """
    return user["id"]


@app.post("/api/auth/login")
def api_login(body: LoginIn, con=Depends(get_con)):
    try:
        return auth.login(con, body.user_id, body.password)
    except PermissionError as e:
        # Rate limited. 429 rather than 401 so a client can tell "wrong
        # password" from "stop trying for a while".
        raise HTTPException(429, {"code": "too_many_attempts", "message": str(e)})
    except ValueError as e:
        raise HTTPException(401, {"code": "invalid_credentials", "message": str(e)})


@app.post("/api/auth/logout")
def api_logout(request: Request, con=Depends(get_con), user=Depends(require_user)):
    # require_user has already validated it; logout needs the raw value so it
    # revokes this session specifically.
    auth.logout(con, _token_from_request(request))
    return {"ok": True}


@app.get("/api/auth/me")
def api_me(user=Depends(require_user)):
    return user


@app.post("/api/auth/change-password")
def api_change_password(body: ChangePasswordIn, con=Depends(get_con),
                        user=Depends(require_user)):
    try:
        auth.change_password(con, user["id"], body.old_password, body.new_password)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/auth/users")
def api_users(con=Depends(get_con), user=Depends(require_mentor)):
    """List all staff accounts."""
    return {"users": auth.list_users(con)}


# ----------------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------------
@app.get("/api/dashboard")
def dashboard(scope=Depends(scoped), con=Depends(get_con)):
    _user, mentor_id = scope
    return service.get_dashboard(con, mentor_id)


@app.get("/api/summary")
def summary(scope=Depends(scoped), con=Depends(get_con)):
    _user, mentor_id = scope
    return service.get_summary(con, mentor_id)


@app.get("/api/worklist")
def worklist(capacity: int = Query(5, ge=1, le=50),
             page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
             scope=Depends(scoped), con=Depends(get_con)):
    _user, mentor_id = scope
    return service.get_worklist(con, mentor_id, capacity, page, page_size)


@app.get("/api/students")
def students(q: str = "", risk: str = "all", page: int = Query(1, ge=1),
             page_size: int = Query(50, ge=1, le=200),
             scope=Depends(scoped), con=Depends(get_con)):
    """Paginated, and scoped to the session.

    For a mentor this is "my students" -- the default search surface the spec
    asks for. It never returns the institution: resolve_scope pins mentor_id to
    the caller, so there is no query string that widens it.
    """
    _user, mentor_id = scope
    return service.list_students(con, mentor_id, q, risk, page, page_size)


@app.get("/api/onboarding")
def onboarding(limit: int = Query(50, ge=1, le=200),
               scope=Depends(scoped), con=Depends(get_con)):
    """Admitted but not yet scoreable. Visible on purpose."""
    # Wrapped in an object rather than returned as a bare JSON array, so a
    # total or a cursor can be added later without changing the response type.
    _user, mentor_id = scope
    return {"students": service.get_onboarding(con, limit, mentor_id)}


@app.get("/api/model")
def model_info(con=Depends(get_con), user=Depends(require_active_user)):
    """What the model predicts, what it refuses to look at, and how it scored
    against the baselines. Point judges here."""
    return service.model_status(con)


@app.get("/api/student/{roll_no}")
def student(roll_no: str, con=Depends(get_con), user=Depends(require_active_user)):
    roll_no = roll_no.upper()
    authorize_student(roll_no, con, user)
    d = service.get_student(con, roll_no)
    if not d:
        raise HTTPException(404, "student not found")
    return d


@app.get("/api/student/{roll_no}/public")
def student_public(roll_no: str, con=Depends(get_con)):
    """What the student sees. No score. No band. No label."""
    d = service.get_student_public_view(con, roll_no.upper())
    if not d:
        raise HTTPException(404, "student not found")
    return d


class WhatIfIn(BaseModel):
    attendance_pct: Optional[FiniteFloat] = None
    latest_ia_pct: Optional[FiniteFloat] = None
    submission_pct: Optional[FiniteFloat] = None
    backlogs: Optional[int] = None
    fee_status: Optional[str] = None


@app.post("/api/student/{roll_no}/whatif")
def whatif(roll_no: str, body: WhatIfIn, con=Depends(get_con), user=Depends(require_active_user)):
    """Recompute the ledger under hypothetical values.

    Exact, because the score is a sum of fixed rules and this is the same
    arithmetic on different numbers.
    """
    roll_no = roll_no.upper()
    authorize_student(roll_no, con, user)
    changes = {k: v for k, v in _fields(body).items() if v is not None}
    r = service.what_if(con, roll_no, changes)
    if r is None:
        raise HTTPException(404, "student not found")
    return r


@app.get("/api/student/{roll_no}/whatif")
def whatif_levers(roll_no: str, con=Depends(get_con), user=Depends(require_active_user)):
    """The controls and where they currently sit, with no changes applied."""
    roll_no = roll_no.upper()
    authorize_student(roll_no, con, user)
    r = service.what_if(con, roll_no, {})
    if r is None:
        raise HTTPException(404, "student not found")
    return r


@app.post("/api/measure")
def measure(actor: str = Depends(actor_id), con=Depends(get_con)):
    """Work out from attendance data whether past interventions helped."""
    return service.measure_outcomes(con, actor)


@app.get("/api/analytics/effectiveness")
def effectiveness(con=Depends(get_con), user=Depends(require_active_user)):
    return service.get_effectiveness(con)


@app.get("/api/analytics/roster")
def analytics_roster(scope=Depends(scoped), con=Depends(get_con)):
    _user, mentor_id = scope
    return service.get_roster(con, mentor_id)


@app.get("/api/analytics/fairness")
def fairness(con=Depends(get_con), user=Depends(require_active_user)):
    return service.get_fairness(con)


@app.get("/api/audit")
def audit(limit: int = Query(100, ge=1, le=1000), con=Depends(get_con),
          user=Depends(require_mentor)):
    return {"entries": service.get_audit(con, limit)}


@app.get("/api/config")
def config(con=Depends(get_con), user=Depends(require_active_user)):
    return service.get_config(con)


@app.get("/api/mentors")
def mentors(con=Depends(get_con), user=Depends(require_active_user)):
    """Section mentors plus the institution-wide roles."""
    # Use what load_mentors returns rather than re-reading service.MENTORS:
    # the global is rebound on every call, so between the call and the read
    # another request's refresh could swap it underneath this one.
    roster = service.load_mentors(con)
    return {"default": service.default_mentor(con),
            "mentors": [{"id": k, **v} for k, v in roster.items()]}


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
def create_intervention(body: InterventionIn, con=Depends(get_con),
                        user=Depends(require_mentor)):
    """Log an approved action against a student on your own caseload.

    `body.mentor` is ignored. It used to be trusted, falling back to
    default_mentor(con) -- so the browser decided whose name went on an
    intervention and into the audit trail. The acting mentor is the verified
    session.
    """
    roll_no = body.roll_no.upper()
    authorize_student(roll_no, con, user)
    try:
        return service.create_intervention(
            con, roll_no, user["id"],
            body.playbook, body.trigger, body.action_text, body.followup_days)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/interventions/{iv_id}/outcome")
def close_intervention(iv_id: str, body: OutcomeIn, con=Depends(get_con),
                       user=Depends(require_mentor)):
    """Record how an action turned out. The student must be on your caseload."""
    owner = con.execute("SELECT roll_no FROM interventions WHERE id=?",
                        (iv_id,)).fetchone()
    if not owner:
        raise HTTPException(404, f"intervention {iv_id} not found")
    authorize_student(owner["roll_no"], con, user)
    try:
        return service.close_intervention(
            con, iv_id, body.outcome, user["id"], body.notes)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/feedback")
def feedback(body: FeedbackIn, con=Depends(get_con), user=Depends(require_mentor)):
    """Mentor agreement or disagreement with a flag. Own caseload only."""
    roll_no = body.roll_no.upper()
    authorize_student(roll_no, con, user)
    try:
        return service.record_feedback(
            con, roll_no, user["id"], body.verdict, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/config")
def update_config(cfg: dict, con=Depends(get_con),
                  user=Depends(require_institution)):
    """Update the scoring thresholds. Institution roles only.

    These thresholds decide every risk score in the institution. A mentor
    changing them to make their own 27 students look calmer would silently
    rescore the other 4,976, so this sits with the HOD.

    This route used to accept a raw dict and write it over the config row
    wholesale, unauthenticated. Because risk_engine does
    `cfg = config or DEFAULT_CONFIG`, an empty body was harmless -- but any
    non-empty object was truthy while missing every expected key, and the
    engine indexes it directly (cfg["bands"]["high_at"],
    cfg["attendance_level"]["threshold_pct"]). So a single anonymous
    PUT {"x": 1} raised KeyError on /api/summary, /api/worklist,
    /api/student/{roll}, both what-if routes and /api/refresh, and kept doing
    so until someone put a valid config back.

    Now: staff only, validated, and merged onto the defaults so the result is
    always a complete config.
    """
    try:
        merged = service.validate_config(cfg)
    except ValueError as e:
        raise HTTPException(422, {
            "code": "invalid_config",
            "message": str(e),
            "hint": "Only known keys are accepted, and every value must be a "
                    "finite number in range.",
        })
    return service.set_config(con, merged, user["id"])


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), commit: bool = False, actor: str = Depends(actor_id), con=Depends(get_con)):
    """Preview by default. Nothing is written until commit=true, so the mentor
    always sees and confirms the column mapping first."""
    raw = await file.read()
    buf = io.BytesIO(raw)
    buf.name = file.filename
    # Only the parse is the user's problem. The previous version wrapped this
    # whole block in `except Exception` and reported everything -- including a
    # failure of the refresh_scores() call below, which runs *after* the data is
    # already committed -- as "check the file has a roll-number column". A
    # genuine server bug was being blamed on the spreadsheet.
    try:
        rep = service.handle_upload(con, buf, actor, commit=commit)
    except (ValueError, KeyError, TypeError, IndexError) as e:
        raise HTTPException(422, {
            "code": "unreadable_upload",
            "message": f"Could not read that file: {type(e).__name__}: {e}",
            "hint": "Check the file has a roll-number column.",
        })
    if commit:
        # Deliberately outside the try: if scoring fails the rows are already
        # committed, so this must surface as a 500 rather than be disguised as
        # a bad upload.
        rep["refresh"] = service.refresh_scores(con, actor)
    return rep


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
    cgpa: Optional[FiniteFloat] = None
    mentor_id: Optional[str] = None


class AttendanceIn(BaseModel):
    roll_no: str
    week_start: str
    attended: int
    held: int


class AssessmentIn(BaseModel):
    roll_no: str
    which: str
    marks: Optional[FiniteFloat] = None
    max_marks: int = 30


class StatusIn(BaseModel):
    status: str
    note: str = ""


@app.post("/api/students")
def admit(body: AdmitIn, actor: str = Depends(actor_id), con=Depends(get_con)):
    """Register a student on admission day. No academic data needed."""
    try:
        return service.admit_student(con, actor=actor, **_fields(body))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/students/{roll_no}")
def remove_student(roll_no: str, con=Depends(get_con),
                   user=Depends(require_institution)):
    """Erase a student from the institution. Restricted, and irreversible.

    require_institution, not require_mentor. This destroys the record, the
    attendance history, the interventions and the score history, and a mentor
    who wants a student off their list wants DELETE /api/mentor/caseload/{roll}
    instead -- which ends the assignment and keeps every one of those.

    Exposing this to mentors merely because the route existed is how "remove
    from my list" becomes "delete a person's history".
    """
    try:
        result = service.remove_student(con, roll_no, actor=user["id"])
    except ValueError as e:
        raise HTTPException(404, str(e))
    service.refresh_scores(con, actor=user["id"])
    return result


# ----------------------------------------------------------------------------
# Caseload: assignment, not creation; dropping, not deletion
# ----------------------------------------------------------------------------
class CaseloadIn(BaseModel):
    roll_no: str
    reason: str = ""


class AssignIn(BaseModel):
    roll_no: str
    mentor_id: str
    reason: str = ""


@app.get("/api/mentor/caseload")
def my_caseload(con=Depends(get_con), user=Depends(require_mentor)):
    """Who is on my list, and how many. The mentor's own scope, restated."""
    mentor_id = user["id"]
    if service.is_institution_role(user.get("role")):
        return {"mentor_id": None, "count": None,
                "note": "Institution roles are not scoped to a caseload."}
    return service.caseload_of(con, mentor_id)


@app.get("/api/directory/lookup")
def directory_lookup(q: str = Query("", min_length=0), limit: int = Query(10, ge=1, le=25),
                     con=Depends(get_con), user=Depends(require_mentor)):
    """Find a student anywhere in the institution, to add them to a caseload.

    Deliberately separate from GET /api/students, which is scoped to the
    caller. A mentor needs to be able to identify a student who is not yet
    theirs, and cannot do that through a search that only returns their own.

    So this one is institution-wide by necessity, and narrow to compensate: it
    requires a search term, caps at 25 rows, and returns only the fields needed
    to pick the right person -- no scores, no bands, no attendance. It is a
    lookup, not a second directory.
    """
    if not q.strip():
        return {"students": [], "note": "Type at least two characters to search."}
    return {"students": service.directory_lookup(con, q, limit)}


@app.post("/api/mentor/caseload")
def add_to_my_caseload(body: CaseloadIn, con=Depends(get_con),
                       user=Depends(require_mentor)):
    """Take responsibility for an existing student.

    This creates an ASSIGNMENT. It does not create a student -- the person
    already exists in the institution and their history comes with them.
    """
    if service.is_institution_role(user.get("role")):
        raise HTTPException(400, {
            "code": "bad_request",
            "message": "Institution roles do not hold a caseload.",
            "hint": "Use POST /api/assignments to assign a student to a mentor.",
        })
    try:
        return service.assign_student(con, body.roll_no, user["id"],
                                      actor=user["id"], reason=body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/mentor/caseload/{roll_no}")
def drop_from_my_caseload(roll_no: str, con=Depends(get_con),
                          user=Depends(require_mentor)):
    """Hand a student back. The student is NOT deleted.

    Their record, attendance, interventions, outcomes, risk history and audit
    trail all remain, the HOD still sees them, and another mentor can pick them
    up. Only the assignment ends, and it is kept as a row rather than removed.
    """
    try:
        return service.drop_student(con, roll_no, user["id"], actor=user["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/institution")
def institution(con=Depends(get_con), user=Depends(require_institution)):
    """The HOD's view: institution totals, then departments, cohorts, mentors.

    Aggregated in SQL. The HOD's question is "how is my institution doing",
    and answering it by shipping 5,000 rows to the browser and counting there
    is what turns an institution view into a giant list nobody can operate.
    """
    return service.institution_overview(con)


@app.get("/api/institution/mentors")
def institution_mentors(con=Depends(get_con), user=Depends(require_institution)):
    """Caseload and risk load per mentor."""
    return {"mentors": service.mentor_overview(con)}


@app.get("/api/institution/caseload/{mentor_id}")
def institution_caseload(mentor_id: str, con=Depends(get_con),
                         user=Depends(require_institution)):
    """Drill into one mentor's caseload from the institution view."""
    return service.caseload_of(con, mentor_id)


@app.post("/api/assignments")
def assign(body: AssignIn, con=Depends(get_con), user=Depends(require_institution)):
    """Assign or reassign any student to any mentor. HOD/principal/admin."""
    try:
        return service.assign_student(con, body.roll_no, body.mentor_id,
                                      actor=user["id"], reason=body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/assignments/{roll_no}")
def assignment_history(roll_no: str, con=Depends(get_con),
                       user=Depends(require_active_user)):
    """Every mentor this student has had. Own caseload, or any if institution."""
    roll_no = roll_no.upper()
    authorize_student(roll_no, con, user)
    return {"roll_no": roll_no,
            "history": service.assignment_history(con, roll_no)}


@app.post("/api/students/bulk")
def admit_bulk(bodies: List[AdmitIn], actor: str = Depends(actor_id), con=Depends(get_con)):
    """A whole intake. One bad row does not roll back the good ones."""
    return service.admit_students_bulk(con, [_fields(b) for b in bodies], actor)


@app.patch("/api/students/{roll_no}/status")
def student_status(roll_no: str, body: StatusIn, actor: str = Depends(actor_id), con=Depends(get_con)):
    try:
        return service.set_student_status(con, roll_no.upper(), body.status,
                                          actor, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/attendance")
def attendance(body: AttendanceIn, actor: str = Depends(actor_id), con=Depends(get_con)):
    """One week for one student, as attended/held rather than a percentage,
    because that is what a register contains and 34/40 is verifiable."""
    try:
        return service.add_attendance(con, body.roll_no.upper(), body.week_start,
                                      body.attended, body.held, actor)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/assessment")
def assessment(body: AssessmentIn, actor: str = Depends(actor_id), con=Depends(get_con)):
    try:
        return service.add_assessment(con, body.roll_no.upper(), body.which,
                                      body.marks, body.max_marks, actor)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/refresh")
def refresh(actor: str = Depends(actor_id), con=Depends(get_con)):
    """Recompute every score. Runs after ingest and admission; nightly in
    production. Scores change when data changes, not when a page loads."""
    return service.refresh_scores(con, actor)


@app.post("/api/demo/reset")
def demo_reset(con=Depends(get_con), user=Depends(require_institution)):
    # require_institution, not require_mentor. This drops every table and
    # rebuilds the institution; a mentor with a 27-student caseload has no
    # business being able to erase the other 4,976.
    """The button that saves your demo when something goes wrong on stage.

    The previous version closed the process-wide connection *before* calling
    reset_db(), so if the rebuild raised, that global stayed closed and every
    later request failed with "connection already closed" until a restart.
    With a per-request connection there is nothing to close: reset_db() opens
    its own, and this one is still valid afterwards.
    """
    out = service.reset_db()
    out["refresh"] = service.refresh_scores(con)
    # Scores exist now, so the demo can be given some work in progress. Without
    # this every seeded intervention is closed and "Open interventions" reads 0,
    # which hides the tracking half of the product loop.
    out["open_interventions_seeded"] = service.seed_open_interventions(con)
    # Re-seed user accounts after the full rebuild.
    auth.ensure_auth_schema(con)
    auth.seed_users(con, service.MENTORS)
    return out


# ----------------------------------------------------------------------------
# Frontend
# ----------------------------------------------------------------------------
@app.get("/")
def index():
    """Service information.

    This deliberately does not serve a UI. There used to be a second,
    hand-written frontend at static/index.html which this route returned, but
    it had no concept of authentication -- no token handling anywhere in it --
    so once every endpoint required a bearer token it could only render
    errors. Two frontends also meant two places to fix every change.

    The React application in frontend/ is now the only UI. In the deployed
    setup it is served at / and this API is mounted under /api, so this route
    is reached only when the backend is run on its own, where a JSON pointer
    is more useful than a broken page.
    """
    return {
        "service": "Sahay API",
        "status": "ok",
        "docs": "/docs",
        "frontend": "served separately from frontend/ (npm run dev, or the "
                    "deployed build)",
    }


@app.get("/api/health")
def health(con=Depends(get_con)):
    """Readiness, and which database this instance is actually talking to.

    Deliberately public and deliberately free of anything sensitive: no DSN,
    no host, no credentials, no personal data. What it does report is the one
    thing that is otherwise impossible to determine from outside -- whether a
    deployed instance is connected to the database you think it is.

    This exists because a sign-in failure is ambiguous. auth.login returns the
    same message whether the account is missing or the password is wrong,
    which is right for anti-enumeration but means a 401 cannot distinguish a
    bad password from an instance pointed at an empty database. These counts
    settle it in one request.
    """
    out = {"status": "ok", "database": "unreachable",
           "students": None, "accounts": None, "seeded": False}
    try:
        out["students"] = con.execute(
            "SELECT COUNT(*) c FROM students").fetchone()["c"]
        out["accounts"] = con.execute(
            "SELECT COUNT(*) c FROM users").fetchone()["c"]
        out["database"] = "connected"
        out["seeded"] = bool(out["students"])
    except psycopg2.Error as e:
        out["status"] = "degraded"
        # The class of failure is useful; the message can carry the host.
        out["reason"] = type(e).__name__
    return out
