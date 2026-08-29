# Sahay — Student Early Warning & Support
SIH25102 · AI-based drop-out prediction and counseling system

## Run it

```bash
pip install fastapi "uvicorn[standard]" python-multipart pandas openpyxl scikit-learn joblib

python preflight.py              # checks your machine, tells you what to fix
bash run_all.sh 5000             # builds everything and checks it
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>. API docs at `/docs`.

`run_all.sh` takes a student count: `bash run_all.sh 20000` works too.

## Files

| File | What it is |
|---|---|
| `static/index.html` | The whole frontend. No CDN, no build step, works offline. |
| `main.py` | FastAPI. Thin routing only. **This is what ships.** |
| `service.py` | Business logic + SQLite. Snapshot scoring, caching. |
| `risk_engine.py` | Additive ledger, change detection, cohort anomalies, what-if. |
| `ml.py` | Self-supervised model, leak-free features, honest evaluation. |
| `outcomes.py` | Measures whether interventions worked, against a control group. |
| `lifecycle.py` | Admission, staged scoring, weekly data entry. |
| `ingest.py` | Messy spreadsheets: header detection, fuzzy columns, roll matching. |
| `generate_demo_data.py` | Synthetic cohort with latent engagement dynamics. |
| `preflight.py` | Environment check with plain-language fixes. |
| `verify.py` | The six claims, proven. Run before the demo. |
| `check.py` | Plain-Python checks. No extra dependencies. |

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
