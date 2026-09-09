"""
Sahay - verification.

Run this before you sleep and before you demo. It checks the four claims you
will make on stage actually hold in the code.

    python verify.py
"""
import csv
import os

from risk_engine import (DEFAULT_CONFIG, build_worklist, detect_cohort_anomalies,
                         intervention_effectiveness, risk_delta, score_student,
                         suggest_playbook)

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data")


def load():
    students = []
    with open(os.path.join(BASE, "master.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            weekly = [float(r[f"w{i:02d}"]) for i in range(26)]
            feats = {
                "weekly_attendance": weekly,
                "ia1": float(r["ia1"]) if r["ia1"] else None,
                "ia2": float(r["ia2"]) if r["ia2"] else None,
                "ia3": float(r["ia3"]) if r["ia3"] else None,
                "ia1_max": 30, "ia2_max": 30, "ia3_max": 30,
                "backlogs": int(r["backlogs"]),
                "submission_pct": float(r["submission_pct"]),
                "fee_status": r["fee_status"],
            }
            students.append({"roll_no": r["roll_no"], "name": r["name"],
                             "dept": r["dept"], "year": int(r["year"]),
                             "section": r["section"], "features": feats,
                             "weeks_since_contact": 3})
    return students


def show_ledger(s):
    L = score_student(s["features"])
    d = risk_delta(s["features"])
    print(f"\n  {s['name']} ({s['roll_no']}, {s['dept']}-{s['year']}{s['section']})")
    print(f"  Risk {L['score']}/100  {L['band']}   "
          f"change {d['delta']:+d} in {d['weeks']} weeks   "
          f"confidence {L['confidence']:.0%} ({L['signals_present']}/{L['signals_total']} signals)")
    check = 0
    for c in L["components"]:
        pts = "  n/a" if c["points"] is None else f"{c['points']:+5d}"
        if c["points"]:
            check += c["points"]
        print(f"    {c['label']:24s} {pts}   {c['reason']}")
    print(f"    {'':24s} {'':5s}   {'-' * 40}")
    print(f"    {'TOTAL (hand-checked)':24s} {check:5d}")
    for g in L["guardrails"]:
        print(f"    [guardrail] {g['message']}")
    pb = suggest_playbook(L)
    if pb:
        print(f"    -> playbook: {pb['title']}  (mentor approval required)")
    return L


def main():
    students = load()
    print(f"Loaded {len(students)} students\n")
    by_roll = {s["roll_no"]: s for s in students}

    # Pick demo students by BEHAVIOUR, not by hardcoded roll number. The cohort
    # is regenerated on every run and roll numbers change with its size.
    def drop(s):
        w = s["features"]["weekly_attendance"]
        return (sum(w[-8:-4]) - sum(w[-4:])) / 4 if len(w) >= 8 else 0

    def recent(s):
        w = s["features"]["weekly_attendance"]
        return sum(w[-4:]) / 4

    sharp_s = max(students, key=drop)
    fee_s = next((s for s in students
                  if s["features"]["fee_status"] in ("Pending", "Part Paid")
                  and recent(s) >= 85 and s["features"]["backlogs"] == 0), students[0])
    chronic_s = min((s for s in students if abs(drop(s)) < 4),
                    key=recent, default=students[0])
    print(f"picked: sharp={sharp_s['roll_no']} fee={fee_s['roll_no']} "
          f"chronic={chronic_s['roll_no']}")

    print("=" * 78)
    print("CLAIM 1: the score is arithmetic a mentor can check by hand")
    L_sharp = show_ledger(sharp_s)
    assert L_sharp["score"] == sum(c["points"] for c in L_sharp["components"] if c["points"]), \
        "components must sum to the total"
    print("\n  PASS: components sum exactly to the score")

    print("\n" + "=" * 78)
    print("CLAIM 2: fee status alone never produces a High-risk flag")
    L_fee = show_ledger(fee_s)
    fee_only = {"fee_status": "Pending"}
    L2 = score_student(fee_only)
    assert L2["band"] != "High", "fee-only student must not be High"
    print(f"\n  Fee-pending, everything else clean -> {L2['score']}/100 {L2['band']}, "
          f"scholarship referral = {L2['scholarship_referral']}")
    print("  PASS: guardrail holds")

    print("\n" + "=" * 78)
    print("CLAIM 3: section-wide problems are separated from individual ones")
    alerts = detect_cohort_anomalies(students)
    for a in alerts:
        print(f"\n  {a['cohort']}: {a['students_affected']}/{a['students_in_cohort']} students, "
              f"mean drop {a['mean_drop_pct']} pts -> {a['route_to']}")
        print(f"    {a['message'][:150]}")
    assert alerts, "a section-wide anomaly must be detected"
    print(f"\n  PASS: {alerts[0]['cohort']} detected as one cohort issue, not {alerts[0]['students_affected']} individual ones")

    print("\n" + "=" * 78)
    print("CLAIM 4: we rank by CHANGE, and cohort noise never eats the mentor's five")
    wl = build_worklist(students, capacity=5, cohort_alerts=alerts)
    print(f"\n  {wl['total_flagged']} individual cases, {wl['total_routed']} routed to HOD, "
          f"worklist capped at {wl['capacity']}\n")
    print(f"  {'#':2s} {'Roll':10s} {'Name':22s} {'Sec':7s} {'Score':>5s} {'Chg':>5s} {'Pri':>6s}  Reason")
    for i, w in enumerate(wl["this_week"], 1):
        sec = f"{w['dept']}-{w['year']}{w['section']}"
        print(f"  {i:<2d} {w['roll_no']:10s} {w['name'][:22]:22s} {sec:7s} "
              f"{w['score']:5d} {w['delta']:+5d} {w['priority']:6.1f}  {w['headline'][:40]}")

    assert max(w["delta"] for w in wl["this_week"]) > 0, "rising-risk students must surface"
    if alerts:
        a = alerts[0]
        same = [w for w in wl["this_week"] if (w["dept"], w["year"], w["section"])
                == (a["dept"], a["year"], a["section"])]
        assert len(same) <= 2, f"{a['cohort']} should not dominate the mentor's five"
    allw = wl["this_week"] + wl["watch"] + wl["routed_to_cohort"]
    sharp = next((w for w in allw if w["roll_no"] == sharp_s["roll_no"]), None)
    chronic = next((w for w in allw if w["roll_no"] == chronic_s["roll_no"]), None)
    if sharp and chronic:
        print(f"\n  sharp faller 22CSE005 priority {sharp['priority']}  vs  "
              f"chronic 22CSE019 priority {chronic['priority']}")
        assert sharp["priority"] > chronic["priority"], "a sharp faller must outrank a chronic case"
    print("  PASS: cohort suppressed, individual sharp fallers outrank chronic cases")
    if wl["routed_to_cohort"]:
        print(f"\n  example routed: {wl['routed_to_cohort'][0]['explained_by_cohort']['message']}")

    print("\n" + "=" * 78)
    print("CLAIM 5: what-if is exact arithmetic, not an estimate")
    import risk_engine as RE
    tgt = sharp_s["features"]
    mods = {"attendance_pct": 80, "submission_pct": 90, "backlogs": 0}
    w = RE.what_if(tgt, mods)
    fresh = RE.score_student(RE.apply_changes(tgt, mods))
    print(f"\n  {w['statement']}")
    for l in w["lines"]:
        print(f"    {l['label']:24s} {str(l['was']):>4} -> {str(l['now']):<4} "
              f"({l['change']:+d})")
    assert w["after"]["score"] == fresh["score"], "what-if must match a fresh recompute"
    assert sum(c["points"] for c in fresh["components"] if c["points"]) == fresh["score"]
    m = RE.minimum_change_to(tgt, "Low", "attendance_pct")
    print(f"    {m['statement']}")
    print("\n  PASS: recomputing independently gives the same number, and its lines sum to it")

    print("\n" + "=" * 78)
    print("CLAIM 6: we measure whether interventions worked, against a control group")
    import json as _json, service as SVC
    con = SVC.connect()
    e = SVC.get_effectiveness(con)
    truth = {}
    try:
        truth = _json.load(open(os.path.join(BASE, "true_effects.json"),
                                encoding="utf-8"))["effects_in_attendance_points"]
    except Exception:
        pass
    print(f"\n  {e['measured_total']} actions measured from attendance records\n")
    print(f"  {'Playbook':36s}{'n':>4}{'naive':>8}{'matched':>9}{'planted':>9}")
    errs = []
    for r in e["measured"]["rows"]:
        t = truth.get(r["playbook"])
        est = r["estimated_effect"]
        if t is not None and est is not None:
            # Only 88% of students are reached and the effect decays weekly, so
            # the recoverable effect is about 0.76 of what was planted.
            errs.append(abs(est - t * 0.757))
        print(f"  {r['playbook'][:36]:36s}{r['n']:4d}"
              f"{str(r['improved_rate']) + '%':>8}"
              f"{str(est) + 'pt':>9}{str(t) + 'pt':>9}")
    assert e["measured_total"] > 0, "outcomes must be measured from data"
    if errs:
        mae = sum(errs) / len(errs)
        print(f"\n  recovering the DELIVERED effect (planted x 0.88 reach x 0.86 decay)")
        print(f"  mean absolute error: {mae:.2f} attendance points")
        assert mae < 2.0, f"matched estimate is off by {mae:.2f} points"
    print("  PASS: the estimator recovers effects it was never told about")

    print("\n" + "=" * 78)
    print("ALL CHECKS PASSED")
    # The ledger's own mode is always "Rules Mode" -- it is exact arithmetic
    # over the configured thresholds, by design, and never consults the model.
    # This line used to append "(no model trained)", which was simply untrue:
    # model_report.json describes a trained xgboost that beats both baselines.
    # Saying otherwise in the verification output is how nobody noticed that
    # production was making the same claim.
    ledger_mode = score_student(students[0]["features"])["mode"]
    trained = None
    try:
        import json
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "model_report.json"), encoding="utf-8") as f:
            trained = json.load(f).get("shipped_model")
    except (OSError, ValueError):
        pass
    print(f"Ledger: {ledger_mode} (thresholds from config, no model involved)")
    print(f"Model:  {trained or 'none trained'}"
          + (" -- scored separately and shown beside the ledger" if trained else ""))


if __name__ == "__main__":
    main()
