# Sahay — Codebase Audit

> **Status: all 20 findings addressed** (2026-09-08). This file is kept as the
> record of what was wrong and why. What changed, and what the audit got wrong:
>
> - **Finding 4 was understated.** It is 32 of 36 routes unauthenticated, not
>   28 of 33 — the original count came from a bad grep.
> - **The NaN analysis was wrong about the symptom.** This file implies invalid
>   JSON reaches the browser. It does not: Starlette renders every response
>   with `allow_nan=False`, so a NaN is an HTTP 500 instead — and because some
>   NaNs were being committed to the database, the 500 was permanent. Worse,
>   not better.
> - **Three new defects were found while fixing these**, none of which are in
>   the list below: `GET /api/analytics/roster` was dead
>   (`round(double precision, integer)` does not exist in Postgres), student
>   search silently matched nothing (`LIKE` is case-sensitive in Postgres,
>   case-insensitive in SQLite), and `reset_db()` issued ~5,600 sequential
>   round trips, so the Reset Demo button could never finish against a remote
>   database.
> - **Finding 8 still needs your action:** rotate the Supabase credentials. The
>   file is untracked now, but it remains in commit `5fec554`.
>
> See `check_json.py` for the regression gate that keeps finding 3's class of
> bug from coming back.

Audited 2026-09-08 against the working tree on `master` (5,317 lines of Python + React frontend).

## Verdict

The app does not currently start, and the deployment config would not build it if it did.
Separately, 28 of 33 HTTP endpoints have no authentication — including one that drops every
table. The Postgres migration in progress (`database.py`, untracked) is roughly 80% done and
left several call sites behind.

---

## P0 — The app is broken

### 1. `main.py:44` crashes at import — `service.DB_PATH` no longer exists

The Postgres migration removed `DB_PATH` from `service.py` (0 occurrences remain), but two
callers still reference it:

- `main.py:44` — `if not os.path.exists(service.DB_PATH)`
- `check.py:54` — same

Reproduced:

```
AttributeError: module 'service' has no attribute 'DB_PATH'
```

Nothing imports successfully: not uvicorn, not the Docker image, not the Vercel function.
With a real DB connection there is no local file to stat, so the check should be dropped or
replaced with a "does the schema exist" query.

### 2. `requirements.txt` is corrupt — UTF-16LE appended mid-file

The last line was written by PowerShell `>>`, which emits UTF-16LE. Raw bytes:

```
p \0 s \0 y \0 c \0 o \0 p \0 g \0 2 \0 - \0 b \0 i \0 n \0 a \0 r \0 y \0 = \0 = \0 ...
```

`psycopg2-binary` is therefore not installable — pip sees NUL bytes. Fix by rewriting the
file as UTF-8.

**Also missing entirely:** `python-dotenv`, imported at `database.py:44`. Even with the
encoding fixed, the container fails on `from dotenv import load_dotenv`.

### 3. `api/hello.py` is not valid Python

One line, with `class`/`def` bodies joined by semicolons — `SyntaxError: invalid syntax`.
Dead file; delete it.

---

## P0 — Security

### 4. 28 of 33 endpoints are unauthenticated

Only `/api/auth/me`, `/change-password`, `/users` and `DELETE /api/students/{roll}` check a
token. Everything else is open to the internet. The worst of it:

| Endpoint | Effect when called anonymously |
|---|---|
| `POST /api/demo/reset` | **Drops every table** (`service.py:265`) and rebuilds from demo data |
| `PUT /api/config` | Rewrites the risk-scoring thresholds for all students |
| `POST /api/upload?commit=true` | Writes arbitrary attendance/marks into the DB |
| `POST /api/students/bulk` | Mass-inserts student records |
| `PATCH /api/students/{roll}/status` | Changes any student's enrolment status |
| `GET /api/student/{roll}` | Full student PII + risk score + drivers |
| `GET /api/audit` | The entire audit trail |

`/api/student/{roll}/public` was carefully designed to hide score and band from students —
but `/api/student/{roll}` sits right next to it with no guard, so that privacy design is
unenforced.

Fix: a FastAPI dependency (`user = Depends(require_user)`) applied to every route, plus a
role check on the mutating ones. The pattern already exists at `main.py:378` — it just needs
to be applied everywhere instead of hand-rolled per route.

### 5. The audit log is forgeable

Every mutation takes `actor` as a **client-supplied query parameter** with a default:
`upload(actor="admin")`, `admit(actor="admin")`, `attendance(actor="staff")`,
`refresh(actor="admin")`, `student_status(actor="admin")`, `admit_bulk(..., actor)`.
Anyone can write any name into the `audit` table. `remove_student` gets this right — it uses
`user["id"]` from the verified token. Do that everywhere and delete the parameter.

### 6. `.env` is not in `.gitignore`

`.env` holds `SUPABASE_DATABASE_URL` (live DB credentials). It is currently untracked, but
one `git add .` publishes it. Nothing in `.gitignore` matches it.

### 7. `.gitignore` is silently non-functional

Two separate problems:

- Every real rule is **commented out**: `#sahay.db`, `#model.joblib`, `#model_report.json`,
  `#demo_data/` (lines 2–5).
- The `node_modules/` and `dist/` entries (lines 21–22) are UTF-16LE — the same PowerShell
  bug as `requirements.txt`. Git reads them as garbage and they match nothing.

### 8. `sahay.db` is committed, with password hashes and live session tokens

Commit `5fec554` ("Include DB and model files for Vercel") added the 14 MB database file. It
contains the `users` table (`password_hash`, `salt`) and `sessions` (bearer tokens).
Deleting the file now does not help — it stays in history. Treat every credential in it as
compromised, rotate the Supabase DSN, and either rewrite history or start a fresh repo.

### 9. Password hashing is a single round of SHA-256

`auth.py:43` — `hashlib.sha256((salt + password).encode())`. Salted, but unstretched: a
consumer GPU does billions of SHA-256/sec. Use `bcrypt`, `argon2-cffi`, or at minimum
`hashlib.pbkdf2_hmac` with ≥600k iterations.

### 10. Default passwords are derived from the user id

`auth.py:78-84` — `Sahay@{uid[-4:].upper()}2025`, applied by `seed_users` to every mentor and
staff account. The algorithm is in the repo, so anyone who reads `auth.py` can log in as any
account that has not changed its password. The docstring acknowledges this ("Real deployments
would force a change on first login") — but that force-change does not exist. Add a
`must_change_password` flag, or generate random passwords and print them once at seed time.

### 11. No rate limiting on `/api/auth/login`

Unlimited password attempts, no lockout, no backoff. Combined with #9 and #10, this is a
straightforward credential-stuffing target.

### 12. Session handling

- Tokens live in `localStorage` (`frontend/src/App.jsx:14,48`) — readable by any XSS. An
  httpOnly cookie is the safer default.
- Expired sessions are deleted only if someone happens to present them (`auth.py:169`). The
  `sessions` table grows without bound; nothing sweeps it.
- No rotation on login, and no revoke-all on password change: `change_password` leaves every
  existing session valid, so a stolen token survives the reset meant to kill it.

---

## P1 — Correctness

### 13. `auth.py:61` — positional indexing on a dict row

```python
for r in con.execute("SELECT id FROM users").fetchall():
    con.execute("UPDATE users SET email=? WHERE id=?", (default_email(r[0]), r[0]))
```

`database.py` uses `RealDictCursor`, so rows are dict-like and `r[0]` raises `KeyError: 0`.
Should be `r["id"]`. The email migration path is dead code that will throw if it ever runs.
Same class of bug at `check.py:44` (`cur = cur[0]`).

### 14. One global connection shared across all requests

`main.py:46` creates a single `con` at import time. Every route is a sync `def`, so FastAPI
runs them in a threadpool — meaning concurrent requests use one psycopg2 connection
simultaneously. psycopg2 connections are not safe for that: expect interleaved transactions,
cursors closed under another thread, and `InterfaceError` under any real load. `demo_reset`
makes it worse by closing and reassigning the global (`main.py:434`) while other threads hold
it.

Fix: a connection pool (`psycopg2.pool.ThreadedConnectionPool`) with a per-request dependency
that checks a connection out and back.

### 15. No rollback on error — one failure poisons the process

`commit()` is called on the happy path; the only `rollback()` in the codebase is at
`auth.py:55`. In Postgres a failed statement aborts the transaction, and every subsequent
query on that connection returns `InFailedSqlTransaction`. Because the connection is a
long-lived global (#14), a single bad request bricks the API until restart. Any handler that
writes needs `try/except → rollback`.

### 16. `render.yaml` health check points at a path that does not exist

`healthCheckPath: /api/docs`, but `main.py` never sets `docs_url`, so FastAPI serves docs at
`/docs`. The check 404s, Render marks the service unhealthy, and the deploy is torn down.
Also missing `dockerfilePath` and any `envVars` entry for `SUPABASE_DATABASE_URL` — the
container would start on the localhost fallback DSN at `database.py:48`.

### 17. Four mutually exclusive deployment configs

`vercel.json` (static frontend + Python function), `render.yaml` (Docker web service),
`Dockerfile` (uvicorn on `$PORT`), and `build.sh` (npm build → `mv frontend/dist static` →
pip install). They disagree about where the frontend is served from and which process runs.
`build.sh` also does `rm -rf static` on a directory that is tracked in git. Pick one topology
and delete the rest.

### 18. CORS will reject the deployed frontend

`main.py:57` — `allow_origins=["http://localhost:5173", "http://localhost:3000"]` with
`allow_credentials=True`. No production origin. (`vite.config.js` proxies `/api` to
`127.0.0.1:8000` and pins port 3000, so dev works — but that override means 5173, the Vite
default, is already the wrong entry of the two.)

### 19. `POST /api/upload` swallows every exception as a 422

`main.py:336` catches bare `Exception` and returns "Check the file has a roll-number column"
— including for `AttributeError`, DB failures, and OOM on a large upload. Real bugs get
reported back to the user as their own data-entry mistake. Catch the parse errors
specifically.

### 20. Repo bloat

Tracked binaries: `sahay.db` (14 MB), `model.joblib` (2.3 MB), `sahay-sih-submission.zip`
(152 KB — a build artifact of the repo itself). Clone cost is ~17 MB of files that
`run_all.sh` regenerates. Uncomment the `.gitignore` rules and untrack them.

---

## Checked and clear

- **No SQL injection.** The f-string queries in `service.py` (lines 736, 750, 762, 793, 811,
  1182, 1240) and `lifecycle.py` (243, 263) look alarming but are safe: `scope` is one of two
  string literals, `sets` is built from a hardcoded `(col, key)` allowlist, `which` is
  validated against `("ia1","ia2","ia3")` before interpolation, the `DELETE` table name comes
  from a literal tuple, and `LIMIT`/`OFFSET` are `int()`-cast. Values are parameterized
  throughout. This is careful work — worth keeping the allowlists as the interpolation
  boundary if this code is refactored.
- **No hardcoded secrets in source**, apart from the localhost fallback DSN at
  `database.py:48`.
- **No XSS sinks in the frontend** — no `innerHTML`, no `dangerouslySetInnerHTML`.
- Frontend dependencies are current (React 19, Vite 8); no known-vulnerable pins.

---

## Suggested order

1. Fix `requirements.txt` encoding, add `python-dotenv`, drop the `DB_PATH` checks — this is
   what makes the app boot again (#1, #2).
2. Add `.env` to `.gitignore`, uncomment the real rules, rewrite the UTF-16 lines (#6, #7).
3. Rotate the Supabase credentials, given #8.
4. Add one auth dependency and apply it to all routes; derive `actor` from the token (#4, #5).
5. Connection pool and rollback handling (#14, #15).
6. Password hashing and a forced first-login change (#9, #10).
7. Pick one deployment target; fix or delete `render.yaml`'s health check (#16, #17).
