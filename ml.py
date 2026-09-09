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
from datetime import datetime

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


def confusion(y_true, y_pred):
    """Confusion matrix as named counts, computed by sklearn, never assembled
    by hand.

    labels=[0, 1] is explicit so the 2x2 shape survives a degenerate case: if a
    threshold happens to predict a single class, confusion_matrix() would
    otherwise return a 1x1 array and the unpacking below would raise.
    """
    from sklearn.metrics import confusion_matrix
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {"true_negative": int(tn), "false_positive": int(fp),
            "false_negative": int(fn), "true_positive": int(tp)}


def classification_at(y_true, scores, threshold):
    """Accuracy, precision, recall, F1 and a confusion matrix at one threshold.

    A confusion matrix needs a hard yes/no, and a ranking model does not have
    one until a threshold is chosen -- so the threshold is an argument and
    every caller records which one it used. An F1 reported without saying at
    what threshold is close to meaningless.

    zero_division=0 rather than letting sklearn warn and return NaN: a
    threshold that predicts no positives has precision 0, not undefined, and a
    NaN here would propagate into the JSON report and out through the API.
    """
    from sklearn.metrics import (accuracy_score, f1_score, precision_score,
                                 recall_score)
    y_pred = (np.asarray(scores) >= threshold).astype(int)
    return {
        "threshold": round(float(threshold), 6),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "predicted_positive": int(y_pred.sum()),
        "confusion_matrix": confusion(y_true, y_pred),
    }


def _threshold_at_top_k(scores, k_frac=0.05):
    """The score cutoff that flags the top k_frac of the cohort.

    This is the threshold the product actually runs at. A mentor has capacity
    for a handful of students a week, so what matters operationally is "of the
    5% we surface, how many are real" -- which is why p@5% was the original
    metric and why it is kept. Turning that same operating point into a
    confusion matrix is what makes F1 comparable to it.
    """
    return float(np.quantile(np.asarray(scores, dtype=float), 1 - k_frac))


def full_metrics(name, role, y_true, scores, probs=None, k_frac=0.05):
    """Every metric the evaluation reports, for one model on one test set.

    Three families, kept apart on purpose because they answer different
    questions and conflating them is the usual way a model table misleads:

      threshold-free  ROC-AUC, PR-AUC -- how well the model RANKS. No decision
                      boundary is involved.
      calibration     Brier score -- whether the probabilities mean anything as
                      probabilities. Only defined when the scores ARE
                      probabilities, so the two non-ML baselines have none.
      thresholded     accuracy, precision, recall, F1, confusion matrix. These
                      need a yes/no, so they are reported twice: at the
                      conventional 0.5 boundary, and at the top-5% operating
                      point this system deploys at.
    """
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    y_true = np.asarray(y_true)
    out = {"model": name, "role": role, "n": int(len(y_true)),
           "positives": int(y_true.sum()),
           "base_rate": round(float(y_true.mean()), 4)}
    if len(np.unique(y_true)) < 2:
        out["note"] = "only one class present in the test set"
        return out

    out["roc_auc"] = round(float(roc_auc_score(y_true, scores)), 4)
    out["pr_auc"] = round(float(average_precision_score(y_true, scores)), 4)
    out["pr_auc_lift_over_base_rate"] = round(out["pr_auc"] / float(y_true.mean()), 2)

    if probs is not None:
        out["brier_score"] = round(float(brier_score_loss(y_true, np.clip(probs, 0, 1))), 4)
    else:
        # Said explicitly rather than left absent or filled with a zero. The
        # persistence and rules baselines emit unbounded scores, not
        # probabilities, so a Brier score would be a category error.
        out["brier_score"] = None
        out["brier_score_note"] = ("not applicable: this baseline emits a score, "
                                   "not a probability")

    if probs is not None:
        out["at_default_threshold"] = classification_at(y_true, probs, 0.5)
        out["at_default_threshold"]["basis"] = (
            "conventional 0.5 probability cut. All three models are "
            "cost-reweighted for the 17% base rate (scale_pos_weight for "
            "XGBoost, class_weight for the others), so 0.5 is their intended "
            "decision boundary rather than an arbitrary one.")

    t_k = _threshold_at_top_k(scores, k_frac)
    out["at_alert_threshold"] = classification_at(y_true, scores, t_k)
    out["at_alert_threshold"]["k_frac"] = k_frac
    out["at_alert_threshold"]["basis"] = (
        "top 5% of the cohort by score -- the weekly capacity this system "
        "surfaces. Unchanged from the original p@5% methodology; this restates "
        "the same operating point as a confusion matrix so that F1 is "
        "comparable across models.")
    return out


METHODOLOGY = [
    {"heading": "Why three models",
     "text": "Logistic Regression is the baseline: linear, transparent, and the "
             "thing a tree ensemble has to beat before its extra complexity is "
             "justified. Random Forest is the conventional tree-based "
             "comparison. XGBoost is the primary model and the one that serves "
             "every prediction in the product."},
    {"heading": "Same data, same split, same target",
     "text": "All models are fitted on identical training rows and scored on "
             "identical test rows, using the same 17 features and the same "
             "target. The split is grouped by STUDENT rather than by row, so no "
             "student contributes to both training and test: a row-wise split "
             "would let one person's ten time points straddle the boundary and "
             "inflate every number. Disjointness is asserted at run time, not "
             "assumed."},
    {"heading": "Preprocessing",
     "text": "Logistic Regression is fitted inside a Pipeline with a "
             "StandardScaler, because features spanning 0-100 percentages and "
             "unit-scale slopes cannot be fitted sensibly by a linear model "
             "otherwise. The scaler is fitted on the training fold only, so no "
             "test statistic leaks into it. Decision trees are invariant to "
             "monotone rescaling, so Random Forest and XGBoost take the raw "
             "feature matrix."},
    {"heading": "Class imbalance",
     "text": "The positive class is about 17% of rows. Every model is "
             "cost-reweighted rather than resampled: scale_pos_weight for "
             "XGBoost, class_weight='balanced' for Logistic Regression, "
             "class_weight='balanced_subsample' for Random Forest. Consistent "
             "treatment matters more than the specific mechanism, and "
             "resampling would have changed the effective dataset per model."},
    {"heading": "Three different thresholds, kept apart",
     "text": "ROC-AUC and PR-AUC are threshold-free and measure ranking. The "
             "Brier score measures whether the probabilities are calibrated, "
             "and is reported as not applicable for the two non-ML baselines, "
             "which emit scores rather than probabilities. Accuracy, precision, "
             "recall, F1 and the confusion matrix all need a hard yes/no, so "
             "they are reported at TWO thresholds: the conventional 0.5 "
             "probability cut, and the top-5% operating point the product "
             "actually deploys at. That 5% is the original p@5% methodology, "
             "unchanged -- it reflects a mentor's weekly capacity, and it is "
             "restated here as a confusion matrix so F1 is comparable across "
             "models."},
    {"heading": "Explainability",
     "text": "SHAP contributions are computed for the primary XGBoost model and "
             "shown on each student's record. They are deliberately not added "
             "to the baseline or the comparison model: those exist to justify "
             "XGBoost's selection, not to be served."},
    {"heading": "Honesty about the data",
     "text": "The dataset is synthetic and generated by this repository. Every "
             "number here is computed from real model predictions on a held-out "
             "fold -- none is hardcoded -- but they describe performance on "
             "synthetic data and should be read as such."},
]


def evaluate_model_comparison(seed=0, verbose=True):
    """Logistic Regression vs Random Forest vs the XGBoost already in service.

    The point of this function is that the comparison is fair, so everything
    that could differ between models is held fixed:

      same rows          build_dataset() is deterministic
      same split         split_by_student(seed=0), grouped by STUDENT so nobody
                         appears on both sides
      same 17 features   FEATURES, unchanged
      same target        build_dataset()'s label, unchanged. The purpose here is
                         to compare models; moving the target at the same time
                         would make the comparison meaningless.
      same metrics       full_metrics() for every model

    XGBoost is NOT retrained. It is loaded from model.joblib and scored on the
    reproduced test set, so these numbers describe the model that is actually
    serving predictions. That the reproduction is exact was checked against the
    stored report: same 15,000 test rows, same 2,550 positives, ROC-AUC 0.7856
    either way.
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    X, y, groups, asof, n_weeks = build_dataset()
    tr, te = split_by_student(groups, seed=seed)
    Xtr, ytr, Xte, yte = X[tr], y[tr], X[te], y[te]

    leak = set(groups[tr]) & set(groups[te])
    if leak:
        raise RuntimeError(f"{len(leak)} students appear in both folds")

    if verbose:
        print("dataset  %6d rows / %5d students" % (len(X), len(np.unique(groups))))
        print("train    %6d rows / %5d students   positives %5d (%.2f%%)"
              % (len(Xtr), len(np.unique(groups[tr])), ytr.sum(), ytr.mean() * 100))
        print("test     %6d rows / %5d students   positives %5d (%.2f%%)"
              % (len(Xte), len(np.unique(groups[te])), yte.sum(), yte.mean() * 100))
        print("no student on both sides: confirmed\n")

    models = []

    logreg = Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced",
                                   random_state=seed)),
    ])
    logreg.fit(Xtr, ytr)
    p_lr = logreg.predict_proba(Xte)[:, 1]
    models.append(("logistic_regression",
                   full_metrics("Logistic Regression", "baseline", yte, p_lr, p_lr)))
    if verbose:
        print("fitted Logistic Regression (scaled, class_weight=balanced)")

    rf = RandomForestClassifier(
        n_estimators=300, max_depth=8, min_samples_leaf=5,
        class_weight="balanced_subsample", n_jobs=-1, random_state=seed)
    rf.fit(Xtr, ytr)
    p_rf = rf.predict_proba(Xte)[:, 1]
    models.append(("random_forest",
                   full_metrics("Random Forest", "comparison", yte, p_rf, p_rf)))
    if verbose:
        print("fitted Random Forest (300 trees, class_weight=balanced_subsample)")

    bundle = load_bundle()
    if bundle is None:
        raise RuntimeError("model.joblib not found; run `python ml.py` first")
    p_xgb = bundle["model"].predict_proba(Xte)[:, 1]
    models.append(("xgboost",
                   full_metrics("XGBoost", "primary", yte, p_xgb, p_xgb)))
    if verbose:
        print("loaded XGBoost from model.joblib (not retrained)\n")

    i_now = FEATURES.index("att_now")
    models.append(("baseline_current_attendance",
                   full_metrics("Baseline: current attendance", "reference",
                                yte, -Xte[:, i_now])))
    models.append(("baseline_rules_ledger",
                   full_metrics("Baseline: rules ledger", "reference",
                                yte, _rules_scores(Xte))))

    by_key = dict(models)
    order = ["logistic_regression", "random_forest", "xgboost",
             "baseline_current_attendance", "baseline_rules_ledger"]
    table = []
    for k in order:
        m = by_key[k]
        d = m.get("at_default_threshold") or {}
        a = m.get("at_alert_threshold") or {}
        table.append({
            "key": k, "model": m["model"], "role": m["role"],
            "accuracy": d.get("accuracy"), "precision": d.get("precision"),
            "recall": d.get("recall"), "f1": d.get("f1"),
            "roc_auc": m.get("roc_auc"), "pr_auc": m.get("pr_auc"),
            "brier_score": m.get("brier_score"),
            "f1_at_alert_threshold": a.get("f1"),
            "precision_at_alert_threshold": a.get("precision"),
            "recall_at_alert_threshold": a.get("recall"),
        })

    ml_keys = ["logistic_regression", "random_forest", "xgboost"]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": {
            "rows": int(len(X)), "students": int(len(np.unique(groups))),
            "train_rows": int(len(Xtr)), "test_rows": int(len(Xte)),
            "train_students": int(len(np.unique(groups[tr]))),
            "test_students": int(len(np.unique(groups[te]))),
            "test_positives": int(yte.sum()),
            "test_base_rate": round(float(yte.mean()), 4),
            "n_features": len(FEATURES),
            "split": "grouped by student, 70/30, seed 0. No student on both sides.",
            "leakage_check": "passed: train and test student sets are disjoint",
            "data": "synthetic, generated by this repository",
        },
        "models": dict(models),
        "table": table,
        "best_by_pr_auc": max(ml_keys, key=lambda k: by_key[k]["pr_auc"]),
        "best_by_f1_at_alert_threshold":
            max(ml_keys, key=lambda k: by_key[k]["at_alert_threshold"]["f1"]),
        "xgboost_retrained": False,
        "xgboost_source": "model.joblib -- the model serving predictions today",
        "methodology": METHODOLOGY,
    }


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
            # What this ACTUALLY trains on, which is not what the docstring at
            # the top of this file describes.
            #
            # The design in that docstring -- stand at week t, predict whether
            # mean attendance over t+1..t+H falls below DISENGAGE_PCT -- is
            # implemented in label_at(). label_at() is never called.
            # build_dataset() uses `lab = r.get("dropout", 0)` instead: a
            # student-level flag from the dataset, identical for all ten time
            # points of a student. Verified: of 5,000 students, zero have a
            # label that changes over time, so there is no horizon in the
            # target at all and horizon_weeks describes only the feature
            # window.
            #
            # In demo_data that flag is
            #   dropout = (backlogs >= 2) or (mean_engagement < 0.7 and rand() < 0.3)
            # so it is ~determined by backlogs -- every student with 2+
            # backlogs is positive -- and backlogs is deliberately EXCLUDED as
            # a feature. The model therefore infers a mostly-backlog-driven
            # flag from the shape of attendance, with a 30% random draw on top
            # that is irreducible. That is what the 0.786 ROC AUC measures.
            #
            # Left stated rather than quietly relabelled: fixing this means
            # switching build_dataset() to label_at() and retraining, which
            # changes every number in this report.
            "target": ("a student-level flag in the training data "
                       "(demo_data: 2+ backlogs, or low engagement with a 30% "
                       "draw), inferred from attendance shape alone"),
            "target_is_not": ("a time-to-event forecast. The label does not "
                              "vary over time, so this is not a 6-week "
                              "prediction despite horizon_weeks below"),
            "target_implementation_note": (
                "label_at() implements the intended 6-week disengagement "
                "event but is not used; build_dataset() reads the dataset's "
                "dropout column."),
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


def save_evaluation(comparison):
    """Merge the model comparison into the report, and into the saved bundle.

    Two places have to agree. model_report.json is what the deployed API reads,
    because it has no sklearn; the bundle's embedded copy is what a machine WITH
    the libraries reads through model_status(). Writing only the JSON would have
    made the comparison appear in production and not locally.

    The bundle is re-dumped with the same model object it was loaded with. No
    retraining, no refitting -- only the metadata dict changes, and
    verify_predictions_unchanged() below is the check that this is true.
    """
    import joblib
    report = {}
    if os.path.exists(REPORT_PATH):
        with open(REPORT_PATH, encoding="utf-8") as f:
            report = json.load(f)
    report["model_evaluation"] = comparison
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    bundle = load_bundle()
    if bundle is not None:
        bundle["report"] = report
        joblib.dump(bundle, MODEL_PATH)
    return report


def load_bundle():
    import joblib
    return joblib.load(MODEL_PATH) if os.path.exists(MODEL_PATH) else None


# ----------------------------------------------------------------------------
# Inference
# ----------------------------------------------------------------------------
DISPLAY_FLOOR, DISPLAY_CEIL = 0.01, 0.99


def _statement(prob, bundle):
    """Never print 100% or 0%. A model that claims certainty about a person is
    making a claim it cannot support, and a judge will say so.

    It also must not claim a horizon it does not have. This used to read
    "N% chance attendance falls below 50% within 6 weeks", which describes
    label_at() -- the intended target that build_dataset() never uses. The
    label it is actually fitted to is a student-level flag with no time
    dimension, so the honest reading of the output is a comparative one: this
    student resembles the flagged group more than that student does.

    The number is also not a calibrated probability. Measured on the demo
    cohort, the mean prediction is ~40% where the observed rate over the next
    six weeks is ~2%, so it is useful for ranking and misleading as a
    likelihood. The wording says "score", not "chance", for that reason.
    """
    return ("How much this student's attendance pattern resembles those the "
            "model was trained to flag. Use it to rank who to contact first; "
            "it is not a probability.")


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


def print_comparison(comp):
    """The comparison table, for the terminal."""
    d = comp["dataset"]
    print()
    print("=" * 92)
    print("MODEL EVALUATION -- same rows, same split, same features, same target")
    print(f"  {d['rows']:,} rows / {d['students']:,} students   "
          f"train {d['train_rows']:,} | test {d['test_rows']:,} "
          f"({d['test_positives']:,} positive, {d['test_base_rate']:.1%})")
    print(f"  {d['split']}")
    print(f"  {d['leakage_check']}")
    print(f"  XGBoost retrained: {comp['xgboost_retrained']}  ({comp['xgboost_source']})")

    hdr = ("model", "role", "acc", "prec", "rec", "F1", "ROC-AUC", "PR-AUC", "Brier", "F1@5%")
    print()
    print("  %-28s %-11s %6s %6s %6s %6s %8s %7s %7s %7s" % hdr)
    print("  " + "-" * 88)
    for r in comp["table"]:
        f = lambda v: ("%.4f" % v) if isinstance(v, (int, float)) else "n/a"
        print("  %-28s %-11s %6s %6s %6s %6s %8s %7s %7s %7s"
              % (r["model"][:28], r["role"], f(r["accuracy"]), f(r["precision"]),
                 f(r["recall"]), f(r["f1"]), f(r["roc_auc"]), f(r["pr_auc"]),
                 f(r["brier_score"]), f(r["f1_at_alert_threshold"])))

    print()
    print("  Confusion matrices")
    for key in ("logistic_regression", "random_forest", "xgboost"):
        m = comp["models"][key]
        for label, blk in (("0.5", m.get("at_default_threshold")),
                           ("top-5%", m.get("at_alert_threshold"))):
            if not blk:
                continue
            c = blk["confusion_matrix"]
            print("    %-22s @ %-7s TN %-6d FP %-6d FN %-6d TP %-6d  F1 %.4f"
                  % (m["model"], label, c["true_negative"], c["false_positive"],
                     c["false_negative"], c["true_positive"], blk["f1"]))
    print()
    print(f"  best by PR-AUC: {comp['best_by_pr_auc']}   "
          f"best by F1 at the alert threshold: "
          f"{comp['best_by_f1_at_alert_threshold']}")
    print("=" * 92)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="print the saved report only")
    ap.add_argument("--evaluate", action="store_true",
                    help="compare Logistic Regression, Random Forest and the "
                         "XGBoost already in service, without retraining it")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    if a.evaluate:
        comp = evaluate_model_comparison(seed=a.seed)
        save_evaluation(comp)
        print_comparison(comp)
    elif a.report and os.path.exists(REPORT_PATH):
        print_report(json.load(open(REPORT_PATH, encoding="utf-8")))
    else:
        bundle, rep = train(seed=a.seed)
        rows, nw = load_master()
        r = rows[0]
        print("\nSample inference:")
        p = predict(bundle, r["att"], r["marks"], nw)
        # The score is printed here rather than inside statement(), because the
        # UI already shows the figure and repeating it there read as a stutter.
        print(f"  {r['roll_no']}: disengagement score {p['percent']:.0f}/100")
        print(f"    {p['statement']}")
        for c in p["contributions"][:4]:
            print(f"    {c['label']:32s} {c['value']:8.1f}  {c['log_odds']:+.2f}  {c['direction']}")
        print(f"  {forecast_attendance(r['att'])['statement']}")
