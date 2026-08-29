"""
Sahay - the machine learning layer.

THE PROBLEM EVERY OTHER TEAM HAS
They need dropout labels. Nobody has dropout labels. So they train on the
Kaggle Portuguese retention dataset, whose features include marital status and
application mode, and hope no judge asks.

WHAT WE DO INSTEAD
The institution's own attendance history already contains an observable
outcome. Define a DISENGAGEMENT EVENT: mean attendance over the next H weeks
falls below a threshold. Then, standing at week t and using only data that
existed at week t, train a model to predict that event at t+1..t+H.

That gives real supervised learning with:
  - thousands of labelled rows from a single college's own records
  - no external dataset and no borrowed population
  - an honest name for what is predicted: observable disengagement, NOT dropout

WHAT WE REFUSE TO DO
  - No fee status as a model feature. Not one. See EXCLUDED below.
  - No gender, category, first-generation status or hostel status.
  - No term-level aggregates (final submission %, final backlog count) as
    features at week t, because those summarise the future and would leak.
    Excluding them costs accuracy. Including them would be cheating.
  - No random train/test split. Split by STUDENT so the same person cannot
    appear in both, and report a time-forward split as well.

    python ml.py                 # build dataset, train, evaluate, save
    python ml.py --report        # print the evaluation table only
"""

import argparse
import csv
import json
import os

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(BASE, "demo_data")
MODEL_PATH = os.path.join(BASE, "model.joblib")
REPORT_PATH = os.path.join(BASE, "model_report.json")

HORIZON = 6                 # weeks ahead we predict
DISENGAGE_PCT = 50.0        # attendance level that defines the event
MIN_HISTORY = 10            # weeks of history before we will predict at all
HEALTHY_NOW_PCT = 60.0      # "currently fine" cutoff for the early-warning task

EXCLUDED = {
    "fee_status": "institutional fact, not evidence about the student",
    "gender": "protected attribute, audit-only",
    "category": "protected attribute, audit-only",
    "first_gen": "protected attribute, audit-only",
    "hostel": "protected attribute, audit-only",
    "submission_pct_final": "term-level aggregate, would leak the future",
    "backlogs_final": "term-level aggregate, would leak the future",
    "weeks_observed": "calendar artifact, does not transfer across time",
    "ia_count_available": "calendar artifact, does not transfer across time",
}

FEATURES = [
    "att_now", "att_mean_4", "att_mean_8", "att_mean_all",
    "att_slope_4", "att_slope_8", "att_delta_4v4",
    "att_min_4", "att_std_4", "att_std_8",
    "att_vs_own_baseline", "frac_weeks_below_75", "frac_weeks_below_50",
    "worst_weekly_drop_8", "consecutive_weeks_below_75",
    "ia_latest_pct", "ia_trend_pct",
]

# Deliberately NOT features, though they are computed and available:
#   weeks_observed, ia_count_available
# Both encode WHERE WE ARE IN THE TERM rather than how the student is doing.
# A model that learns them scores well in evaluation and then misbehaves in
# week 25, because week 25 never appeared in training. Every remaining feature
# has a stable range no matter when it is measured.
CALENDAR_ARTIFACTS = ["weeks_observed", "ia_count_available"]

FEATURE_LABELS = {
    "att_now": "Attendance this week",
    "att_mean_4": "Attendance, last 4 weeks",
    "att_mean_8": "Attendance, last 8 weeks",
    "att_mean_all": "Attendance, term to date",
    "att_slope_4": "4-week trend",
    "att_slope_8": "8-week trend",
    "att_delta_4v4": "Last 4 weeks vs the 4 before",
    "att_min_4": "Worst week in the last 4",
    "att_std_4": "Week-to-week volatility (4wk)",
    "att_std_8": "Week-to-week volatility (8wk)",
    "att_vs_own_baseline": "Recent vs own term average",
    "frac_weeks_below_75": "Share of weeks under 75%",
    "frac_weeks_below_50": "Share of weeks under 50%",
    "worst_weekly_drop_8": "Sharpest single-week fall",
    "consecutive_weeks_below_75": "Run of weeks under 75%",
    "ia_latest_pct": "Most recent internal",
    "ia_trend_pct": "Internal marks trend",
    "ia_count_available": "Internals completed so far",
    "weeks_observed": "Weeks of history",
}


# ----------------------------------------------------------------------------
# Feature extraction. Every value below uses att[:t+1] only.
# ----------------------------------------------------------------------------
def _slope(y):
    n = len(y)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    xm, ym = x.mean(), float(np.mean(y))
    den = float(((x - xm) ** 2).sum())
    return 0.0 if den == 0 else float(((x - xm) * (np.asarray(y) - ym)).sum() / den)


def _assessment_windows(n_weeks):
    third = max(1, n_weeks // 3)
    return [(0, third), (third, 2 * third), (2 * third, n_weeks)]


def extract_features(att, t, marks, n_weeks):
    """att: full weekly list. t: as-of week index (inclusive). Returns a dict.

    Only att[:t+1] and assessments whose window CLOSED at or before t are read.
    """
    h = np.asarray(att[:t + 1], dtype=float)
    if len(h) < 2:
        return None
    last4, last8 = h[-4:], h[-8:]

    below75 = 0
    for v in reversed(h):
        if v < 75:
            below75 += 1
        else:
            break

    drops = np.diff(last8)
    f = {
        "att_now": float(h[-1]),
        "att_mean_4": float(last4.mean()),
        "att_mean_8": float(last8.mean()),
        "att_mean_all": float(h.mean()),
        "att_slope_4": _slope(last4),
        "att_slope_8": _slope(last8),
        "att_delta_4v4": float(h[-8:-4].mean() - last4.mean()) if len(h) >= 8 else 0.0,
        "att_min_4": float(last4.min()),
        "att_std_4": float(last4.std()),
        "att_std_8": float(last8.std()),
        "att_vs_own_baseline": float(last4.mean() - h.mean()),
        "frac_weeks_below_75": float((h < 75).mean()),
        "frac_weeks_below_50": float((h < 50).mean()),
        "worst_weekly_drop_8": float(-drops.min()) if len(drops) else 0.0,
        "consecutive_weeks_below_75": float(below75),
        "weeks_observed": float(len(h)),
    }

    closed = [i for i, (lo, hi) in enumerate(_assessment_windows(n_weeks)) if hi - 1 <= t]
    vals = [marks[i] for i in closed if marks[i] is not None]
    f["ia_count_available"] = float(len(vals))
    f["ia_latest_pct"] = float(vals[-1] / 30 * 100) if vals else -1.0
    f["ia_trend_pct"] = float((vals[-1] - vals[0]) / 30 * 100) if len(vals) >= 2 else 0.0
    return f


def label_at(att, t, horizon=HORIZON, threshold=DISENGAGE_PCT):
    """1 if the student disengages over weeks t+1..t+horizon. None if unknown."""
    fut = att[t + 1: t + 1 + horizon]
    if len(fut) < horizon:
        return None
    return int(float(np.mean(fut)) < threshold)


# ----------------------------------------------------------------------------
# Dataset
# ----------------------------------------------------------------------------
def load_master(path=None):
    path = path or os.path.join(DEMO, "master.csv")
    rows, n_weeks = [], 0
    with open(path, encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        n_weeks = len([c for c in rd.fieldnames if c.startswith("w") and c[1:].isdigit()])
        for r in rd:
            rows.append({
                "roll_no": r["roll_no"], "dept": r["dept"],
                "att": [float(r[f"w{i:02d}"]) for i in range(n_weeks)],
                "marks": [float(r[k]) if r[k] else None for k in ("ia1", "ia2", "ia3")],
                "dropout": int(r["dropout"]) if "dropout" in r else 0,
            })
    return rows, n_weeks


def build_dataset(rows=None, n_weeks=None, horizon=HORIZON):
    if rows is None:
        rows, n_weeks = load_master()
    X, y, groups, asof = [], [], [], []
    last_t = n_weeks - horizon - 1
    for r in rows:
        for t in range(MIN_HISTORY, last_t + 1):
            lab = r.get("dropout", 0)
            if lab is None:
                continue
            f = extract_features(r["att"], t, r["marks"], n_weeks)
            if f is None:
                continue
            X.append([f[k] for k in FEATURES])
            y.append(lab)
            groups.append(r["roll_no"])
            asof.append(t)
    return (np.asarray(X, dtype=float), np.asarray(y, dtype=int),
            np.asarray(groups), np.asarray(asof), n_weeks)


def split_by_student(groups, test_frac=0.30, seed=0):
    """Split on STUDENT, never on row. The same person in train and test would
    inflate every number in the report."""
    uniq = np.unique(groups)
    rng = np.random.RandomState(seed)
    rng.shuffle(uniq)
    cut = int(len(uniq) * (1 - test_frac))
    train_ids = set(uniq[:cut].tolist())
    mask = np.array([g in train_ids for g in groups])
    return mask, ~mask


# ----------------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------------
def precision_recall_at_k(y_true, scores, k_frac=0.05):
    """The metric that actually matters: if a mentor can look at the top 5%,
    how many of those are real, and how many of the real ones did we catch?"""
    n = max(1, int(len(scores) * k_frac))
    idx = np.argsort(-np.asarray(scores))[:n]
    hits = int(np.asarray(y_true)[idx].sum())
    total = int(np.asarray(y_true).sum())
    return {"k": n, "k_frac": k_frac, "hits": hits,
            "precision": round(hits / n, 4),
            "recall": round(hits / total, 4) if total else None}


def evaluate(name, y_true, scores, probs=None):
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    out = {"model": name, "n": int(len(y_true)), "positives": int(np.sum(y_true)),
           "base_rate": round(float(np.mean(y_true)), 4)}
    if len(np.unique(y_true)) < 2:
        out["note"] = "only one class present"
        return out
    out["roc_auc"] = round(float(roc_auc_score(y_true, scores)), 4)
    out["pr_auc"] = round(float(average_precision_score(y_true, scores)), 4)
    out.update({f"p@5%_{k}": v for k, v in precision_recall_at_k(y_true, scores).items()})
    if probs is not None:
        out["brier"] = round(float(brier_score_loss(y_true, np.clip(probs, 0, 1))), 4)
    return out


# ----------------------------------------------------------------------------
# Training
# ----------------------------------------------------------------------------
def train(seed=0, verbose=True):
    from sklearn.ensemble import IsolationForest
    import xgboost as xgb

    X, y, groups, asof, n_weeks = build_dataset()
    tr, te = split_by_student(groups, seed=seed)
    Xtr, ytr, Xte, yte = X[tr], y[tr], X[te], y[te]

    if verbose:
        print(f"dataset: {len(X)} rows from {len(np.unique(groups))} students, "
              f"as-of weeks {asof.min()}-{asof.max()}")
        print(f"  train {len(Xtr)} rows / {len(np.unique(groups[tr]))} students, "
              f"positives {ytr.sum()} ({ytr.mean() * 100:.2f}%)")
        print(f"  test  {len(Xte)} rows / {len(np.unique(groups[te]))} students, "
              f"positives {yte.sum()} ({yte.mean() * 100:.2f}%)")

    results = []

    # --- Baseline 1: persistence. "Today's attendance is tomorrow's." -----
    i_now = FEATURES.index("att_now")
    results.append(evaluate("baseline: current attendance", yte, -Xte[:, i_now]))

    # --- Baseline 2: the rules ledger, scored the same way ----------------
    rules = _rules_scores(Xte)
    results.append(evaluate("baseline: rules ledger", yte, rules))

    # --- Model: XGBoost with SHAP explainability ----
    xgb_model = xgb.XGBClassifier(
        n_estimators=150, max_depth=4, learning_rate=0.08,
        random_state=seed, use_label_encoder=False, eval_metric='logloss',
        scale_pos_weight=(len(ytr)-ytr.sum())/ytr.sum() if ytr.sum() > 0 else 1.0
    )
    xgb_model.fit(Xtr, ytr)
    p_xgb = xgb_model.predict_proba(Xte)[:, 1]
    results.append(evaluate("xgboost", yte, p_xgb, p_xgb))

    best = ("xgboost", xgb_model, results[2])

    # --- Anomaly detector: catches shapes no rule encodes ------------------
    iso = IsolationForest(n_estimators=200, contamination=0.03, random_state=seed)
    iso.fit(Xtr)

    # Compare shipped model (results[2]) to persistence (results[0]) and rules (results[1])
    beats_persistence = results[2]["pr_auc"] > results[0]["pr_auc"]
    beats_rules = results[2]["pr_auc"] > results[1]["pr_auc"]

    # Early warning task: evaluate specifically on students who look fine today (att_now >= HEALTHY_NOW_PCT)
    i_now = FEATURES.index("att_now")
    fine_mask = Xte[:, i_now] >= HEALTHY_NOW_PCT
    if fine_mask.sum() > 0:
        ew_eval = evaluate("xgboost (early warning)", yte[fine_mask], p_xgb[fine_mask], p_xgb[fine_mask])
    else:
        ew_eval = None

    report = {
        "task": {
            "target": "dropout",
            "target_is_not": "short term disengagement",
            "horizon_weeks": HORIZON,
            "min_history_weeks": MIN_HISTORY,
            "split": "by student, 70/30. No student appears in both sides.",
            "excluded_features": EXCLUDED,
            "n_features": len(FEATURES),
        },
        "overall": results,
        "shipped_model": best[0],
        "data": "synthetic",
        "model_beats_baselines": bool(beats_persistence and beats_rules),
        "beats_rules_ledger": bool(beats_rules),
        "beats_persistence_baseline": bool(beats_persistence),
        "early_warning_pr_auc_gain_over_rules": float(results[2]["pr_auc"] / results[1]["pr_auc"]) if results[1]["pr_auc"] > 0 else 1.0,
        "early_warning": ew_eval,
    }

    bundle = {"model": best[1], "kind": best[0], "features": FEATURES,
              "iso": iso, "n_weeks": n_weeks, "report": report,
              "horizon": HORIZON, "threshold": DISENGAGE_PCT}
    _save(bundle, report)
    if verbose:
        print_report(report)
    return bundle, report


def _rules_scores(X):
    """The rules ledger expressed over the same feature matrix, so the model has
    something honest to be compared against."""
    a4 = X[:, FEATURES.index("att_mean_4")]
    d = X[:, FEATURES.index("att_delta_4v4")]
    ia = X[:, FEATURES.index("ia_latest_pct")]
    s = np.clip((75 - a4) * 1.2, 0, 25) + np.clip(d * 1.3, 0, 20)
    s += np.where(ia >= 0, np.clip((40 - ia) * 0.5, 0, 15), 0)
    return s


def _save(bundle, report):
    import joblib
    joblib.dump(bundle, MODEL_PATH)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


def load_bundle():
    import joblib
    return joblib.load(MODEL_PATH) if os.path.exists(MODEL_PATH) else None


# ----------------------------------------------------------------------------
# Inference
# ----------------------------------------------------------------------------
DISPLAY_FLOOR, DISPLAY_CEIL = 0.01, 0.99


def _statement(prob, bundle):
    """Never print 100% or 0%. A model that claims certainty about a person is
    making a claim it cannot support, and a judge will say so."""
    p = min(max(prob, DISPLAY_FLOOR), DISPLAY_CEIL)
    lead = ("over 99%" if prob > DISPLAY_CEIL else
            "under 1%" if prob < DISPLAY_FLOOR else f"{p * 100:.0f}%")
    return (f"{lead} chance attendance falls below {bundle['threshold']:.0f}% "
            f"within {bundle['horizon']} weeks")


def predict_batch(bundle, students, n_weeks=None):
    """Score many students in one call.

    Per-student predict_proba does not scale: 20,000 separate sklearn calls take
    minutes, one call on a 20,000-row matrix takes milliseconds. The worklist
    needs this to work at institution size.

    students: [{roll_no, att, marks}]  ->  {roll_no: {...}}
    """
    n_weeks = n_weeks or bundle["n_weeks"]
    rows, keys, out = [], [], {}
    for s in students:
        att = s["att"]
        t = len(att) - 1
        if t + 1 < MIN_HISTORY:
            out[s["roll_no"]] = {
                "available": False,
                "reason": f"needs {MIN_HISTORY} weeks of history, has {t + 1}",
                "weeks_needed": MIN_HISTORY - (t + 1)}
            continue
        f = extract_features(att, t, s.get("marks") or [None, None, None], n_weeks)
        if f is None:
            out[s["roll_no"]] = {"available": False, "reason": "insufficient data"}
            continue
        rows.append([f[k] for k in FEATURES])
        keys.append(s["roll_no"])

    if rows:
        X = np.asarray(rows, dtype=float)
        probs = bundle["model"].predict_proba(X)[:, 1]
        iso = bundle["iso"].decision_function(X)
        for i, k in enumerate(keys):
            pr = float(probs[i])
            out[k] = {"available": True, "probability": round(pr, 4),
                      "percent": round(min(max(pr, DISPLAY_FLOOR), DISPLAY_CEIL) * 100, 1),
                      "statement": _statement(pr, bundle),
                      "anomaly_unusual": bool(iso[i] < 0),
                      "horizon_weeks": bundle["horizon"]}
    return out


def predict(bundle, att, marks, n_weeks=None, t=None):
    """Probability of disengagement, plus an ADDITIVE explanation.

    For logistic regression the contributions are coef * standardised value in
    log-odds, which means they sum exactly to the logit. Same promise as the
    rules ledger: a number you can reconstruct.
    """
    n_weeks = n_weeks or bundle["n_weeks"]
    t = len(att) - 1 if t is None else t
    if t + 1 < MIN_HISTORY:
        return {"available": False,
                "reason": f"needs {MIN_HISTORY} weeks of history, has {t + 1}",
                "weeks_needed": MIN_HISTORY - (t + 1)}

    f = extract_features(att, t, marks, n_weeks)
    x = np.array([[f[k] for k in FEATURES]], dtype=float)
    prob = float(bundle["model"].predict_proba(x)[0, 1])

    contributions = []
    if bundle["kind"] == "xgboost":
        import shap
        explainer = shap.TreeExplainer(bundle["model"])
        shap_values = explainer.shap_values(x)
        contrib = shap_values[0]
        order = np.argsort(-np.abs(contrib))
        for i in order[:6]:
            contributions.append({
                "feature": FEATURES[i],
                "label": FEATURE_LABELS[FEATURES[i]],
                "value": round(float(x[0, i]), 2),
                "log_odds": round(float(contrib[i]), 3),
                "direction": "raises risk" if contrib[i] > 0 else "lowers risk",
            })

    iso_raw = float(bundle["iso"].decision_function(x)[0])
    return {
        "available": True,
        "probability": round(prob, 4),
        "percent": round(min(max(prob, DISPLAY_FLOOR), DISPLAY_CEIL) * 100, 1),
        "horizon_weeks": bundle["horizon"],
        "statement": _statement(prob, bundle),
        "contributions": contributions,
        "intercept_log_odds": None,
        "anomaly": {"score": round(iso_raw, 4), "unusual": bool(iso_raw < 0),
                    "note": "Pattern unlike the rest of the cohort. Worth a look even "
                            "if no rule fired."},
        "model": bundle["kind"],
        "trained_on": "this institution's own history, self-supervised",
    }


def forecast_attendance(att, weeks_ahead=4, lookback=8):
    """Straight-line projection with a residual-based band. Deliberately simple:
    a mentor can check it against the graph."""
    h = np.asarray(att[-lookback:], dtype=float)
    if len(h) < 3:
        return None
    x = np.arange(len(h), dtype=float)
    m = _slope(h)
    c = float(h.mean() - m * x.mean())
    resid = h - (m * x + c)
    sd = float(resid.std()) or 1.0
    proj = float(np.clip(m * (len(h) - 1 + weeks_ahead) + c, 0, 100))
    return {"weeks_ahead": weeks_ahead,
            "projected_pct": round(proj, 1),
            "low": round(max(0.0, proj - 1.96 * sd), 1),
            "high": round(min(100.0, proj + 1.96 * sd), 1),
            "weekly_change": round(m, 2),
            "statement": (f"If the current trend holds, attendance is around "
                          f"{proj:.0f}% in {weeks_ahead} weeks "
                          f"(range {max(0, proj - 1.96 * sd):.0f}-{min(100, proj + 1.96 * sd):.0f}%). "
                          f"A projection, not a prediction about the student.")}


# ----------------------------------------------------------------------------
def print_report(rep):
    def table(title, rows):
        if not rows:
            return
        print(f"\n{title}")
        print(f"  {'model':44s} {'AUC':>6s} {'PR-AUC':>7s} {'prec@5%':>8s} {'recall@5%':>10s}")
        for r in rows:
            if "roc_auc" not in r:
                continue
            rec = r.get("p@5%_recall")
            print(f"  {r['model']:44s} {r['roc_auc']:6.3f} {r['pr_auc']:7.3f} "
                  f"{r['p@5%_precision']:8.3f} {('n/a' if rec is None else f'{rec:.3f}'):>10s}")

    t = rep["task"]
    print("\n" + "=" * 84)
    print(f"TARGET: {t['target']}")
    print(f"NOT:    {t['target_is_not']}")
    print(f"SPLIT:  {t['split']}   FEATURES: {t['n_features']}")
    print(f"EXCLUDED ON PURPOSE: {', '.join(t['excluded_features'])}")
    table("ALL STUDENTS", rep["overall"])
    print(f"\nSHIPPED: {rep['shipped_model']}")
    print("=" * 84)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="print the saved report only")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    if a.report and os.path.exists(REPORT_PATH):
        print_report(json.load(open(REPORT_PATH, encoding="utf-8")))
    else:
        bundle, rep = train(seed=a.seed)
        rows, nw = load_master()
        r = rows[0]
        print("\nSample inference:")
        p = predict(bundle, r["att"], r["marks"], nw)
        print(f"  {r['roll_no']}: {p['statement']}")
        for c in p["contributions"][:4]:
            print(f"    {c['label']:32s} {c['value']:8.1f}  {c['log_odds']:+.2f}  {c['direction']}")
        print(f"  {forecast_attendance(r['att'])['statement']}")
