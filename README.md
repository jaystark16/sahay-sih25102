# Sahay — Student Early Warning & Support
SIH25102 · AI-based drop-out prediction and counseling system

## Run it

Needs a Postgres database. Supabase's free tier is fine.

```bash
cp .env.example .env             # then put your connection string in it
pip install -r requirements.txt

python preflight.py              # checks your machine, tells you what to fix
bash run_all.sh 5000             # builds everything and checks it

python auth.py list                          # the accounts that exist
python auth.py reset <email> <password>      # set one you can sign in with

python -m uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>. API docs at `/docs`.

`run_all.sh` takes a student count: `bash run_all.sh 20000` works too. Note
that it **drops every table** in the database `.env` points at.

Every endpoint except sign-in and the student's own `/public` view requires a
bearer token. Seeded accounts get a random password and must change it on first
use, so `auth.py reset` is how you get in the first time.

### The React frontend

```bash
cd frontend && npm install && npm run dev     # proxies /api to :8000
```

For a deployed build, set `VITE_API_BASE` to the API's origin, and set
`CORS_ORIGINS` on the API to the frontend's origin.

## Deploying

The frontend and the API deploy separately, because the API's dependencies
(scikit-learn, xgboost, scipy) total ~300 MB and will not fit in a serverless
function bundle.

| Piece | Where | Config |
|---|---|---|
| React frontend | Vercel (Hobby) | `vercel.json`; set `VITE_API_BASE` |
| FastAPI backend | Render (free web service) | `render.yaml` + `Dockerfile`; set `SUPABASE_DATABASE_URL` and `CORS_ORIGINS` |
| Postgres | Supabase (free) | — |

Render's free tier spins down after ~15 minutes idle, so the first request
after a quiet spell takes around 50 seconds.

## Files

| File | What it is |
|---|---|
| `static/index.html` | The whole frontend. No CDN, no build step, works offline. |
| `main.py` | FastAPI. Thin routing only. **This is what ships.** |
| `service.py` | Business logic + persistence. Snapshot scoring, caching. |
| `risk_engine.py` | Additive ledger, change detection, cohort anomalies, what-if. |
| `ml.py` | Self-supervised model, leak-free features, honest evaluation. |
| `outcomes.py` | Measures whether interventions worked, against a control group. |
| `lifecycle.py` | Admission, staged scoring, weekly data entry. |
| `ingest.py` | Messy spreadsheets: header detection, fuzzy columns, roll matching. |
| `generate_demo_data.py` | Synthetic cohort with latent engagement dynamics. |
| `preflight.py` | Environment check with plain-language fixes. |
| `verify.py` | The six claims, proven. Run before the demo. |
| `check.py` | Plain-Python checks of the service layer. |
| `check_json.py` | Asserts every response is valid JSON and every route needs auth. |
| `database.py` | Postgres connection pool and the sqlite-shaped wrapper over it. |
| `jsonsafe.py` | Serialisation guard: non-finite numbers become null, not a 500. |
| `auth.py` | bcrypt passwords, sessions, login throttling, and the account CLI. |

## Numbers to quote

| | |
|---|---|
| Worklist at 20,000 students | **4.5 ms** |
| Full rescore of 20,000 | 4.9 s |
| Effectiveness page (cached) | 0.3 ms |
| Database at 20,000 students | 52 MB |
| Model, early-warning AUC | 0.901 |
| Model vs the rules engine | **5.4× PR-AUC** |
| Model vs "rank by today's attendance" | 0.901 vs 0.897 — barely. Say so. |
| Matched effect estimate error | **0.36 attendance points** |

Bands scale to the signals that exist. A student whose marks are not recorded is
judged out of what can be measured, so a gap in the paperwork cannot hide someone
who needs help. The receipt still sums to the same number.

## The six claims `verify.py` proves

1. The score is arithmetic. Components sum exactly to the total.
2. Fee status alone can never produce a High-risk flag.
3. A section-wide drop is one HOD alert, not 38 individual cases.
4. Ranking is by change, so a sharp faller outranks a chronic case.
5. What-if is exact — an independent recompute gives the same number.
6. The matched estimator recovers intervention effects it was never told about.

## What it predicts

Observable disengagement: mean attendance over the next 6 weeks falling below 50%.
**Not dropout.** Dropout labels do not exist, so the system does not claim to predict it.

## What the model never sees

Fee status · gender · category · first-generation status · hostel status ·
term-level aggregates that would leak the future · calendar artifacts that do not
transfer across time. Enforced in `ml.py`, listed at `GET /api/model`, shown on the
Setup screen.

## Honest caveats to have ready

- Below roughly 2,000 students of history the model does **not** beat the baselines.
  It ships Rules Mode instead, which is the correct behaviour.
- The effectiveness page is observational, not a randomised trial. The control column
  exists because students helped at their worst often recover anyway.
- All demo data is synthetic and labelled as such in the interface.
