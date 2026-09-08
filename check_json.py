"""
Sahay - JSON contract checks.

    python check_json.py

Asserts three things about the HTTP layer that the other suites do not cover:

  1. Every response body is *strictly* valid JSON. Python's json.dumps will
     happily emit bare NaN and Infinity tokens, which no browser's JSON.parse
     accepts; Starlette instead renders with allow_nan=False, which turns the
     same value into an opaque HTTP 500. Either way it is a bug, and neither
     shows up as a wrong number -- it shows up as a dead endpoint. This walks
     every route and rejects the non-finite literals explicitly.

  2. Errors all share one shape: {"error": {"code", "message", ...}}. There
     used to be five different shapes, two of which reused the key `detail`
     with incompatible types.

  3. Endpoints require authentication. Reads and writes alike.

Exits non-zero on the first category that fails, so it can gate a deploy.
"""

import json
import os
import sys

from fastapi.testclient import TestClient

import main

fails = []


def ok(name, cond, note=""):
    print(f"  {'pass' if cond else 'FAIL'}  {name:52s} {note}")
    if not cond:
        fails.append(name)


def _reject(literal):
    raise ValueError(f"non-finite JSON literal: {literal}")


def strictly_valid(text):
    """True if this is JSON a browser would accept.

    parse_constant fires for NaN, Infinity and -Infinity -- the three tokens
    json.loads accepts but the JSON spec does not.
    """
    if text == "":
        return True
    json.loads(text, parse_constant=_reject)
    return True


READS = [
    "/api/dashboard", "/api/summary", "/api/worklist", "/api/students",
    "/api/onboarding", "/api/model", "/api/config", "/api/mentors",
    "/api/analytics/roster", "/api/analytics/fairness",
    "/api/analytics/effectiveness", "/api/audit",
]

WRITES = [
    ("put", "/api/config", {}),
    ("post", "/api/refresh", None),
    ("post", "/api/measure", None),
    ("post", "/api/demo/reset", None),
    ("post", "/api/students", {"name": "T", "dept": "CSE", "year": 2}),
    ("post", "/api/attendance", {"roll_no": "X", "week_start": "2025-07-07",
                                 "attended": 1, "held": 2}),
    ("post", "/api/assessment", {"roll_no": "X", "which": "ia1", "marks": 10}),
    ("post", "/api/interventions", {"roll_no": "X", "playbook": "p",
                                    "trigger": "t"}),
    ("post", "/api/feedback", {"roll_no": "X", "verdict": "agree"}),
    ("delete", "/api/students/X", None),
    ("patch", "/api/students/X/status", {"status": "active"}),
]


def call(c, method, path, body=None, headers=None):
    fn = getattr(c, method)
    if body is not None:
        return fn(path, json=body, headers=headers)
    return fn(path, headers=headers)


def main_check():
    email = os.environ.get("SAHAY_TEST_EMAIL")
    password = os.environ.get("SAHAY_TEST_PASSWORD")

    with TestClient(main.app, raise_server_exceptions=False) as c:
        print("EVERY ENDPOINT REQUIRES AUTHENTICATION")
        unguarded = [p for p in READS if c.get(p).status_code != 401]
        ok("no read is reachable anonymously", not unguarded,
           ", ".join(unguarded) if unguarded else f"{len(READS)} reads -> 401")

        bad = []
        for m, p, b in WRITES:
            if call(c, m, p, b).status_code != 401:
                bad.append(f"{m.upper()} {p}")
        ok("no write is reachable anonymously", not bad,
           ", ".join(bad) if bad else f"{len(WRITES)} writes -> 401")

        # The one deliberate exception: the student's own score-free view.
        ok("the student's public view stays public",
           c.get("/api/student/NOSUCH/public").status_code in (200, 404))

        print("\nERRORS SHARE ONE SHAPE")
        for label, res in [
                ("401", c.get("/api/summary")),
                ("422", c.post("/api/auth/login", json={"user_id": "x"})),
        ]:
            body = res.json()
            ok(f"{label} uses the standard envelope",
               isinstance(body.get("error"), dict)
               and isinstance(body["error"].get("code"), str)
               and isinstance(body["error"].get("message"), str),
               json.dumps(body)[:60])

        print("\nEVERY BODY IS STRICTLY VALID JSON")
        checked = 0
        for p in READS + ["/api/student/NOSUCH/public"]:
            r = c.get(p)
            try:
                strictly_valid(r.text)
                checked += 1
            except ValueError as e:
                ok(f"{p} is valid JSON", False, str(e))
        ok("all unauthenticated responses are valid JSON",
           checked == len(READS) + 1, f"{checked} bodies")

        if not (email and password):
            print("\n  (set SAHAY_TEST_EMAIL and SAHAY_TEST_PASSWORD to also "
                  "check authenticated bodies, which is where the numbers are)")
        else:
            r = c.post("/api/auth/login",
                       json={"user_id": email, "password": password})
            if r.status_code != 200:
                ok("test account can sign in", False, r.text[:80])
            else:
                H = {"Authorization": f"Bearer {r.json()['token']}"}
                print("\nAUTHENTICATED BODIES ARE STRICTLY VALID JSON")
                good = 0
                for p in READS:
                    rr = c.get(p, headers=H)
                    try:
                        strictly_valid(rr.text)
                        good += 1
                    except ValueError as e:
                        ok(f"{p}", False, str(e))
                ok("every read serialises cleanly", good == len(READS),
                   f"{good}/{len(READS)}")

                roll = None
                students = c.get("/api/students?page_size=1", headers=H).json()
                if students.get("students"):
                    roll = students["students"][0]["roll_no"]
                if roll:
                    for m, p, b in [
                            ("get", f"/api/student/{roll}", None),
                            ("get", f"/api/student/{roll}/public", None),
                            ("get", f"/api/student/{roll}/whatif", None),
                            ("post", f"/api/student/{roll}/whatif",
                             {"attendance_pct": 55}),
                    ]:
                        rr = call(c, m, p, b, headers=H)
                        try:
                            strictly_valid(rr.text)
                            good = True
                        except ValueError as e:
                            good = False
                            note = str(e)
                        ok(f"{m.upper()} {p.replace(roll, '<roll>')}", good,
                           "" if good else note)

                print("\nNON-FINITE INPUT IS REFUSED, NOT STORED")
                for label, path, payload in [
                        ("NaN attendance", f"/api/student/{roll}/whatif",
                         '{"attendance_pct": NaN}'),
                        ("Infinity attendance", f"/api/student/{roll}/whatif",
                         '{"attendance_pct": Infinity}'),
                        ("NaN cgpa", "/api/students",
                         '{"name":"T","dept":"CSE","year":2,"cgpa": NaN}'),
                        ("NaN marks", "/api/assessment",
                         '{"roll_no":"%s","which":"ia1","marks": NaN}' % roll),
                ]:
                    rr = c.post(path, content=payload,
                                headers={**H, "content-type": "application/json"})
                    ok(f"{label} -> 422", rr.status_code == 422,
                       f"got {rr.status_code}")

    print()
    if fails:
        print(f"{len(fails)} PROBLEM(S):")
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
    print("JSON contract holds.")


if __name__ == "__main__":
    main_check()
