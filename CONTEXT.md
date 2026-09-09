# CONTEXT — what changed and why

A record of the work done on this repository on **8–9 September 2026**, written
so that anyone (including future-you) can pick it up without re-deriving the
reasoning. `AUDIT.md` is the original findings list; this is what was actually
done about it.

**Branch:** `master`. Everything below is pushed.

---

## The short version

The app did not start. `main.py` referenced `service.DB_PATH`, which the
SQLite→Postgres migration had deleted, so `import main` raised `AttributeError`
before a single route registered — uvicorn, Docker and Vercel all failed
identically. On top of that, 32 of 36 endpoints had no authentication at all,
including one that drops every table.

Both are fixed, along with 20 audit findings and three defects the audit missed.
The frontend was then rebuilt from a 1,587-line single file into a structured
application.

---

## 1. Why it would not start

| Problem | Fix |
|---|---|
| `main.py:44` used `service.DB_PATH`, deleted in the Postgres migration | Removed the check; added `service.db_is_empty()` for the question the callers actually had |
| `requirements.txt` had UTF-16LE bytes appended mid-file, so `psycopg2-binary` was uninstallable | Rewritten as UTF-8, fully pinned |
| `python-dotenv` imported but never declared | Added |
| `api/hello.py` was a `SyntaxError` | Deleted |

The UTF-16 corruption came from PowerShell's `>>` / `Out-File`, which write
UTF-16LE by default. The same bug had silently disabled `.gitignore`. **Never
write config files in this repo with shell redirection** — use an editor, or
`Set-Content -Encoding utf8`. `preflight.py` now checks for it.

---

## 2. Security

**32 of 36 endpoints were unauthenticated.** Open to the internet: `PUT
/api/config`, `POST /api/upload?commit=true`, full student PII via
`GET /api/student/{roll}`, the entire audit trail, and `POST /api/demo/reset`,
which drops every table.

All 36 are now guarded by three dependencies in `main.py`:

- `require_user` — any signed-in account
- `require_active_user` — signed in *and* past the forced password change
- `require_mentor` — staff-only actions

`/api/student/{roll}/public` stays deliberately open: it is the score-free view
intended for the student themselves.

**Other auth work:**

- `actor` was a **client-supplied query parameter** defaulting to `"admin"` on
  every mutation, so the audit log recorded whatever the caller typed. It is now
  injected from the verified session (`actor_id` dependency) and is gone from
  the public API surface.
- Passwords moved from a single round of salted SHA-256 to **bcrypt**, with
  transparent upgrade of existing hashes on next successful login.
- Seeded accounts previously got a password derived from the account id
  (`Sahay@<last4>2025`) — the algorithm was in the repo, so anyone who read
  `auth.py` could sign in as any account. Now random, with
  `must_change_password` enforced.
- Login is rate-limited (8 attempts / 5 min, then HTTP 429).
- Sessions rotate on login, are revoked on password change, and expired rows are
  swept rather than accumulating for ever.
- The React login page was **printing a working password on screen** as a
  click-to-fill hint. Removed.

---

## 3. Valid JSON — and a correction

The audit said NaN reaches the browser as an invalid `NaN` token. **That was
wrong.** Starlette renders every response with `allow_nan=False`, so a NaN is an
**HTTP 500**, not malformed JSON. Worse, not better — because NaN was being
*committed to the database*, the 500 was permanent.

How NaN got in:

1. `ingest.py` read CSVs with `keep_default_na=False`, then called bare
   `float(s)`. `float("NaN")` succeeds, so a cell containing the text `NaN` was
   stored.
2. `risk_engine.py` had five bare `round()` calls whose threshold guards were
   NaN-blind (every comparison with NaN is `False`, so control always reached
   the `round()`). `round(nan)` raises. Since scoring runs cohort-wide,
   **one bad cell killed the worklist for every user**.

Fixed at three layers: `math.isfinite` gates in the parsers, `allow_inf_nan=False`
on the pydantic float fields (NaN now returns a 422 naming the field), and
`jsonsafe.py` as a net beneath both.

**One error shape.** There were five, two of which reused the key `detail` with
incompatible types — which is why the UI rendered `[object Object]` on any
validation error. Now: `{"error": {code, message, hint?, fields?}}` everywhere,
via four `@app.exception_handler`s.

---

## 4. Connection handling

One module-global psycopg2 connection was shared across FastAPI's threadpool,
never rolled back, and stayed dead once Supabase's pooler dropped it. I
reproduced this live: every route after the first failure returned
`connection already closed`.

Replaced with a pooled connection per request (`database.checkout()` /
`main.get_con()`), rolled back on release. Two bugs in my own first attempt,
both caught by testing:

- `ThreadedConnectionPool` **raises** when exhausted rather than queueing — 8 of
  24 concurrent requests became HTTP 500s. Added a semaphore so callers wait.
- Health-checking every checkout added a full network round trip to every
  request. Now only probes connections idle longer than `DB_IDLE_PROBE_AFTER`.

The API also now **starts even when the database is unreachable**, logging one
clear warning. Previously the lifespan raised, the process exited, and on a host
that restarts crashed services that is a crash loop with the reason scrolling
past between restarts.

---

## 5. Three defects the audit missed

Found while fixing the rest, all confirmed against the live database:

- **`GET /api/analytics/roster` was dead.** `round(double precision, integer)`
  does not exist in Postgres. Also, an output alias is not allowed inside an
  `ORDER BY` expression. Both worked in SQLite.
- **Student search silently matched nothing.** `LIKE` is case-insensitive in
  SQLite, case-sensitive in Postgres. Now `ILIKE`. No error — just wrong data,
  which is the worse failure.
- **`reset_db()` could never finish.** It issued ~5,600 sequential round trips
  in one transaction; the Reset Demo button hung until Supabase cancelled the
  statement. Batched with `execute_batch`: **190 seconds**.

Also fixed: intervention IDs derived from `COUNT(*)` collided after a delete;
`PUT /api/config` accepted any object and could brick every scoring endpoint
with a `KeyError`; `/api/summary` 500'd on a NULL band where `/api/dashboard`
survived; `uploads.report` stored `json.dumps(...)[:4000]`, i.e. invalid JSON by
construction.

---

## 6. The frontend rebuild

`frontend/src/App.jsx` was **1,587 lines** — 28 components, 175 inline `style={{}}`
blocks, its own fetch calls, its own formatting rules.

```
frontend/src/
  app/          App (providers + routing), AppShell (layout, nav)
  auth/         AuthContext — session, one place
  services/     apiClient.js  — the only place fetch is called
                api.js        — every endpoint + its documented response shape
  hooks/        useApi (loading/error/abort), useAction, useDebounced
  components/
    ui/         Button, Badge, Card, Modal, DataTable, Toast, States, Icons
    risk/       RiskBadge, RiskLedger, ScoreSummary, ModelOpinion, DeltaChip
  features/     student/WhatIfPanel, student/InterventionPanel
  pages/        Login, Worklist, Students, StudentDetail, Analytics, Model, Admin
  lib/          format.js (value states), constants.js
  styles/       components.css
```

**Two data-integrity bugs, both of which showed the mentor something untrue:**

1. **A failed request rendered as good news.** Five call sites did
   `.then(r => r.json()).then(setData)` with no status check — an error body
   parsed cleanly, the expected key came back `undefined`, and the dashboard
   showed *"0 students flagged for attention this week"* during an outage.
   Now one client that throws, and error is checked **before** empty.
2. **Missing rendered as zero.** A student with no recorded attendance looked
   identical to one at 0%. `lib/format.js` returns an explicit absent marker;
   the student page marks unavailable signals as such, because a missing signal
   tells the mentor how much of the picture the score is based on.

**Fields the backend returned and the old UI ignored, now shown:** `confidence`,
`max_available`, `provisional`, `guardrails`, `primary_driver`,
`explained_by_cohort`, `anomaly`, stage explanations, forecast bounds, measured
intervention change, and the model's disagreement with the rules.

**Not invented:** `/api/students` returns no attendance or CGPA, so the directory
does not show them.

`index.css` (235 design tokens, including a careful note on colourblind ΔE
separation for the risk colours) was **kept** — it was already good work.
`App.css` was Vite template leftover and was deleted, along with three unused
template assets.

---

## 7. One frontend, not two

`static/index.html` was a second, hand-written UI served at `/`. It had **zero**
token handling, so once every endpoint required authentication it could only
render errors. Retired; `GET /` now returns service info. The React app is the
only frontend.

---

## 8. Deployment

Settled on **all-Vercel** (frontend + Python function on one project) with
Supabase for Postgres. Render was attempted first but gates Blueprints behind
credit-card verification, so it was abandoned. `Dockerfile` and `render.yaml`
remain for anyone who wants to run the API as a container.

**Vercel's Root Directory must be the repository root**, not `frontend`, or the
Python function at `api/index.py` is invisible.

### The 250 MB problem

A Vercel Python function caps at 250 MB unzipped. scikit-learn, xgboost, shap
and scipy come to ~220 MB. Solved by splitting *where* they run, not by dropping
the model:

- `requirements.txt` — runtime only. Deployed function imports at **92 MB**.
- `requirements-ml.txt` — the four model packages, local only.
- `service.py` imports `ml` in a `try/except`. Every call site already guarded
  on `if b:`.
- `refresh_scores` already wrote each student's probability into
  `risk_snapshots.model_pct`, so the deployed API serves that stored value.
- `/api/model` reads `model_report.json` directly — plain JSON — so it still
  reports the real trained model.

**Genuinely lost on the server:** live attendance forecast, and the model's delta
inside what-if. The rules-based what-if is unaffected and still exact.

To refresh stored predictions:

```bash
pip install -r requirements.txt -r requirements-ml.txt
python ml.py          # train
python service.py     # score -> writes model_pct to Supabase
```

---

## 9. Repo hygiene

- `.gitignore` had **every real rule commented out**, and its remaining entries
  were UTF-16, so it matched nothing. `.env` was one `git add .` from
  publication. Rewritten.
- Untracked `sahay.db` (14 MB, containing password hashes and session tokens)
  and the submission zip.
- **`demo_data/` and `model.joblib` stay tracked** — the Dockerfile does
  `COPY . .` and the container needs them. Ignoring them silently downgrades the
  deployment. (I got this wrong first and corrected it.)
- Added `.dockerignore` so `.env` is never baked into an image.
- `.env.example` documents the percent-encoding trap and the UTF-16 trap.

---

## 10. Things I got wrong

Recorded because the reasoning matters more than the fix:

- **`vercel.json` with `cd frontend`** — I assumed Vercel built from the repo
  root. It did not; the project's Root Directory was `frontend`, so the build
  broke. My bug.
- **A BOM fix in `database.py`** — I claimed `python-dotenv` would choke on a
  byte-order mark and "verified" it by only testing the fixed path. dotenv
  strips BOMs itself (`stream.read().removeprefix("﻿")`). Reverted.
- **"~4 characters missing from your password"** — arithmetic that stopped being
  valid the moment the Supabase password was rotated. Withdrawn.
- **Sent you to Render** on a "5 minute" errand that dead-ended at a card form.
- **A forced password change with no screen to do it in.** The API refused every
  route until `must_change_password` cleared, but the UI had no form — you
  signed in successfully and hit a wall on every page. Fixed with a dedicated
  screen.

The common thread: inferring how the environment worked instead of checking.

---

## 11. Verification

Run these; all four pass against the live database:

```bash
python preflight.py     # environment, DSN structure, connectivity
python check.py         # asserts on service-layer return shapes
python verify.py        # the six product claims
python check_json.py    # every body is valid JSON; every route needs auth
```

Also confirmed: the ledger sums **exactly** to the score on real high-risk
students (the property the product claims); every field the UI renders is
present in the API; the full path browser → Vite proxy → FastAPI → Supabase.

`check.py`'s SPEED budgets are expressed as *compute + N database round trips*
rather than flat wall-clock, because a flat limit against a remote database
measures your distance to Mumbai rather than anything about the code. It can
still fail on a latency spike — re-run before believing it.

---

## 12. Still open

- **`sahay.db` is in commit `5fec554`** with old password hashes. The Supabase
  credential has been rotated, which defuses the important part; purging the
  blob from history is a separate, destructive decision.
- **Tokens are in `localStorage`**, readable by XSS. An httpOnly cookie is
  stronger but needs `SameSite=None` + CSRF handling if the frontend and API are
  ever split across hosts. Deliberate trade, recorded in `AuthContext.jsx`.
- **Bundle is 652 KB** (192 KB gzipped), mostly Recharts. Code-splitting would
  help.
- **`check.py` seeds test students** each run (`Check Student <timestamp>`).
  I cleaned 9 of them out of the demo data; be aware it happens.
