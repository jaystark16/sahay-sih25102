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
        ("multipart", "python-multipart", False)]:
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
check("sahay.db", lambda: os.path.exists(os.path.join(BASE, "sahay.db")),
      "python service.py")


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
