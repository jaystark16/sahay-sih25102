"""
Sahay - preflight.

Run this FIRST, on your own machine, before you touch anything else. It checks
the things that break at 3am and tells you what to type to fix each one.

    python preflight.py
"""

import importlib
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ok, warn, bad = [], [], []


def check(label, fn, fix=None, fatal=True):
    try:
        r = fn()
        if r is True or r is None:
            ok.append(label)
        elif r is False:
            (bad if fatal else warn).append((label, "", fix))
        else:
            ok.append(f"{label}  ({r})")
    except Exception as e:
        (bad if fatal else warn).append((label, f"{type(e).__name__}: {e}", fix))


# ---- python ----------------------------------------------------------------
check(f"Python {sys.version_info.major}.{sys.version_info.minor}",
      lambda: sys.version_info >= (3, 8),
      "Install Python 3.8 or newer.")

# ---- packages --------------------------------------------------------------
for mod, pipname, fatal in [
        ("numpy", "numpy", True), ("pandas", "pandas", True),
        ("openpyxl", "openpyxl", True), ("sklearn", "scikit-learn", True),
        ("joblib", "joblib", True), ("fastapi", "fastapi", True),
        ("uvicorn", "uvicorn[standard]", True),
        ("multipart", "python-multipart", False),
        ("psycopg2", "psycopg2-binary", True),
        ("dotenv", "python-dotenv", True),
        ("bcrypt", "bcrypt", True)]:
    check(f"package {pipname}",
          (lambda m=mod: importlib.import_module(m).__version__
           if hasattr(importlib.import_module(m), "__version__") else True),
          f"pip install {pipname}", fatal)

# ---- pydantic version, which is the usual FastAPI surprise -----------------
def _pyd():
    import pydantic
    v = getattr(pydantic, "VERSION", "?")
    from pydantic import BaseModel
    from typing import Optional

    class T(BaseModel):
        a: str
        b: Optional[str] = None
    T(a="x")
    return f"v{v}, models load"


check("pydantic", _pyd, "pip install -U pydantic fastapi")

# ---- our own modules -------------------------------------------------------
sys.path.insert(0, BASE)
for m in ("ingest", "risk_engine", "ml", "lifecycle", "service", "main"):
    def _imp(mm=m):
        try:
            return bool(importlib.import_module(mm))
        except ModuleNotFoundError as e:
            missing = str(e).split("'")[1] if "'" in str(e) else "?"
            if missing != mm:
                # Do not blame the file. The real cause is upstream.
                raise ModuleNotFoundError(
                    f"{mm}.py is present but needs '{missing}', which is not installed")
            raise
    check(f"module {m}.py", _imp,
          f"If {m}.py is missing, put it in {BASE}. "
          f"If a package is missing, install it and run this again.")

# ---- data files ------------------------------------------------------------
for f, fix in [("demo_data/master.csv", "python generate_demo_data.py"),
               ("demo_data/01_attendance_register.xlsx", "python generate_demo_data.py"),
               ("static/index.html", "index.html must be in a folder named static/")]:
    check(f, (lambda p=f: os.path.exists(os.path.join(BASE, p))), fix)

check("model.joblib (optional)",
      lambda: os.path.exists(os.path.join(BASE, "model.joblib")),
      "python ml.py   (without it the app runs in Rules Mode, which is fine)",
      fatal=False)
# The database is a Postgres server now, not a local file, so the question is
# whether we can reach it and whether the schema is there -- not whether
# sahay.db exists (it is a leftover, and checking for it passed even when the
# real database was unreachable).
def _env_file_encoding():
    """The .env file is readable as UTF-8 text.

    Worth checking explicitly, because PowerShell's `>>` and `Out-File` write
    UTF-16LE by default, and that is exactly how this repo's .gitignore and
    requirements.txt were corrupted: the file looks fine in an editor and is
    unparseable everywhere else. Use `Set-Content -Encoding utf8`, or an
    editor's save-as, not shell redirection.
    """
    if not os.path.exists(os.path.join(BASE, ".env")):
        return "no .env file (using the environment directly)"
    raw = open(os.path.join(BASE, ".env"), "rb").read()
    if b"\x00" in raw:
        raise ValueError("contains NUL bytes -- it was written as UTF-16, "
                         "probably by PowerShell '>>' or Out-File")
    raw.decode("utf-8")
    return f"{len(raw)} bytes, valid UTF-8"


check(".env is UTF-8", _env_file_encoding,
      "rewrite it with an editor, or: Set-Content .env -Encoding utf8")


def _dsn_structure():
    """The connection string has every part psycopg2 needs.

    Reports what is missing without ever printing the password -- a diagnostic
    you can paste into a chat or an issue safely.
    """
    from urllib.parse import urlparse
    import database

    url = database.dsn()
    if url == database.DEFAULT_DSN:
        return False

    u = urlparse(url)
    missing = []
    if u.scheme not in ("postgresql", "postgres"):
        missing.append(f"scheme is {u.scheme!r}, expected postgresql://")
    if not u.username:
        missing.append("no username")
    if not u.password:
        missing.append("no password")
    if not u.hostname:
        missing.append("no host")
    if missing:
        raise ValueError("; ".join(missing))

    notes = []
    # Supabase's pooler authenticates as postgres.<project-ref>. Using the bare
    # "postgres" user against port 6543 fails with "password authentication
    # failed", which reads like a wrong password rather than a wrong username.
    if u.port == 6543 and "." not in (u.username or ""):
        notes.append("port 6543 is the pooler, which needs the "
                     "postgres.<project-ref> username, not plain 'postgres'")
    if u.hostname and u.hostname.startswith("-"):
        notes.append(f"host {u.hostname!r} starts with '-', so it looks "
                     f"truncated (Supabase hosts begin 'aws-0-')")
    if notes:
        raise ValueError("; ".join(notes))

    return f"{u.username.split('.')[0]}...@{u.hostname}:{u.port or 5432}"


check("SUPABASE_DATABASE_URL is well formed", _dsn_structure,
      "cp .env.example .env and paste the URI from Supabase > Project "
      "Settings > Database > Connection string")


def _dsn_resolves():
    import socket
    from urllib.parse import urlparse
    import database

    host = urlparse(database.dsn()).hostname
    return f"{host} -> {socket.gethostbyname(host)}"


check("database host resolves", _dsn_resolves,
      "check the host part of SUPABASE_DATABASE_URL for typos or truncation",
      fatal=False)


def _db_reachable():
    """Connect, and say which half failed if it does not work.

    "password authentication failed" and "could not translate host name" are
    very different problems, and the raw psycopg2 message repeats itself twice
    over several lines, which buries the useful part.
    """
    import psycopg2
    import database
    try:
        con = database.connect()
    except psycopg2.OperationalError as e:
        msg = str(e).strip().splitlines()[0]
        if "password authentication failed" in msg:
            # Two common causes, and the server cannot tell them apart for us.
            # The second one bites hard: a password containing @ : / # or ? is
            # a URI delimiter, so it has to be percent-encoded or urlparse
            # splits the string in the wrong place.
            raise ValueError(
                "credentials rejected. Either the password in .env is not the "
                "current one (rotated it recently?), or it contains a "
                "character that must be percent-encoded in a URI "
                "(@ -> %40, : -> %3A, / -> %2F, # -> %23, ? -> %3F)") from None
        if "could not translate host name" in msg or "Name or service" in msg:
            raise ValueError(f"host not found -- {msg}") from None
        raise ValueError(msg) from None
    try:
        n = con.execute("SELECT COUNT(*) c FROM students").fetchone()["c"]
        return f"{n:,} students"
    finally:
        con.close()


check("database reachable, schema present", _db_reachable,
      "fix SUPABASE_DATABASE_URL, then: python service.py")


# ---- the routes actually respond ------------------------------------------
def _routes():
    import service
    con = service.connect()
    if service.snapshots_stale(con):
        service.refresh_scores(con)
    wl = service.get_worklist(con, "rao", 5)
    service.get_summary(con, "rao")
    service.get_effectiveness(con)
    service.get_fairness(con)
    if wl["this_week"]:
        service.get_student(con, wl["this_week"][0]["roll_no"])
    return f"{wl['students_in_scope']} students, {wl['total_flagged']} flagged"


check("every screen has data", _routes, "python service.py")

# ---- port ------------------------------------------------------------------
def _port():
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 8000))
        return True
    except OSError:
        return False
    finally:
        s.close()


check("port 8000 is free", _port,
      "Something is already using it. Run: uvicorn main:app --port 8001", fatal=False)

# ---- report ----------------------------------------------------------------
print()
for line in ok:
    print(f"  OK    {line}")
for label, err, fix in warn:
    print(f"  WARN  {label}" + (f"  -> {err}" if err else ""))
    if fix:
        print(f"        fix: {fix}")
for label, err, fix in bad:
    print(f"  FAIL  {label}" + (f"  -> {err}" if err else ""))
    if fix:
        print(f"        fix: {fix}")

print()
if bad:
    print(f"{len(bad)} thing(s) must be fixed before the demo will run.")
    sys.exit(1)
print("Ready. Start it with:  uvicorn main:app --reload")
print("Then open http://127.0.0.1:8000")
if warn:
    print(f"({len(warn)} warning(s) above are not blocking.)")
