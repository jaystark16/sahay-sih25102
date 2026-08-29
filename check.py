"""
Sahay - checks.

One file, plain Python, no extra dependencies. It calls the functions directly
and asserts on what comes back. No test server, no browser, no node.

    python check.py

Prints a line per check and exits non-zero if anything is wrong, so you can
trust the last line.
"""

import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import ingest
import lifecycle
import risk_engine as R
import service

fails = []


def ok(name, cond, note=""):
    print(f"  {'pass' if cond else 'FAIL'}  {name:52s} {note}")
    if not cond:
        fails.append(name)


def has(name, obj, *keys):
    """Every key must be present. Lists are checked on their first item."""
    missing = []
    for k in keys:
        cur = obj
        for part in k.split("."):
            if isinstance(cur, list):
                if not cur:
                    cur = None
                    break
                cur = cur[0]
            if not isinstance(cur, dict) or part not in cur:
                missing.append(k)
                cur = None
                break
            cur = cur[part]
    ok(name, not missing, "missing: " + ", ".join(missing) if missing else "")


# ---------------------------------------------------------------------------
if not os.path.exists(service.DB_PATH):
    print("No database. Run: python generate_demo_data.py && python service.py")
    sys.exit(1)

con = service.connect()
if service.snapshots_stale(con):
    service.refresh_scores(con)

wl = service.get_worklist(con, "rao", 5)
roll = wl["this_week"][0]["roll_no"]
sd = service.get_student(con, roll)
_, feats = service._features(con, roll)


# ---------------------------------------------------------------------------
print("\nTHE SCORE IS ARITHMETIC")
L = sd["ledger"]
total = sum(c["points"] for c in L["components"] if c["points"])
ok("components add up to the total", total == L["score"], f"{total} == {L['score']}")
ok("no component exceeds its own cap",
   all(c["points"] is None or c["points"] <= c["max_points"] for c in L["components"]))
ok("every component gives a reason in words",
   all(c["reason"] for c in L["components"]))
ok("score stays inside 0 to 100", 0 <= L["score"] <= 100, str(L["score"]))

thin = R.score_student({"weekly_attendance": [40] * 12, "backlogs": 3,
                        "submission_pct": 20, "fee_status": "Pending"})
full = R.score_student({"weekly_attendance": [40] * 12, "backlogs": 3,
                        "submission_pct": 20, "fee_status": "Pending",
                        "ia1": 10, "ia2": 8, "ia3": 6,
                        "ia1_max": 30, "ia2_max": 30, "ia3_max": 30})
ok("missing marks cannot hide a struggling student",
   thin["band"] == full["band"] == "High",
   f"{thin['score']}/{thin['max_available']} vs {full['score']}/100")
ok("a thin record explains its own scale", bool(thin["scale_note"]))
ok("a thin record is held out of the top band",
   R.score_student({"fee_status": "Pending"})["band"] != "High")


print("\nTHE FEE GUARDRAIL")
fee_only = R.score_student({"fee_status": "Pending"})
ok("fees alone never reach High", fee_only["band"] != "High",
   f"{fee_only['score']}/100 {fee_only['band']}")
ok("fees alone trigger a scholarship referral", fee_only["scholarship_referral"])
loaded = R.score_student({"fee_status": "Pending", "weekly_attendance": [40] * 12,
                          "backlogs": 3, "submission_pct": 20})
ok("a genuinely struggling student still reaches High", loaded["band"] == "High",
   f"{loaded['score']}/100")


print("\nCHANGE, NOT LEVEL")
falling = {"weekly_attendance": [90] * 8 + [88, 80, 70, 60]}
flat = {"weekly_attendance": [62] * 12}
df = R.risk_delta(falling)["delta"]
dl = R.risk_delta(flat)["delta"]
ok("a falling student registers a rise in risk", df > 0, f"{df:+d}")
ok("a steady student registers no change", abs(dl) < 5, f"{dl:+d}")
ok("the falling student outranks the flat one",
   R.priority_score(R.score_student(falling), df, 3) >
   R.priority_score(R.score_student(flat), dl, 3))


print("\nSECTION-WIDE PROBLEMS")
alerts = wl["cohort_alerts"]
ok("a section-wide drop is detected", len(alerts) > 0,
   alerts[0]["cohort"] if alerts else "none found")
if alerts:
    a = alerts[0]
    ok("it routes to the HOD, not the mentor", a["route_to"] == "Head of Department")
    ok("its students are kept off the mentor's list", wl["total_routed"] > 0,
       f"{wl['total_routed']} routed")
    same = [w for w in wl["this_week"]
            if (w["dept"], w["year"], w["section"]) == (a["dept"], a["year"], a["section"])]
    ok("one section cannot fill the whole worklist", len(same) <= R.MAX_PER_COHORT,
       f"{len(same)} of {len(wl['this_week'])} slots")
ok("the worklist respects the capacity given", len(wl["this_week"]) <= 5,
   f"{len(wl['this_week'])} items")


print("\nWHAT-IF IS EXACT")
mods = {"attendance_pct": 80, "submission_pct": 90, "backlogs": 0}
w = R.what_if(feats, mods)
fresh = R.score_student(R.apply_changes(feats, mods))
ok("what-if matches an independent recompute", w["after"]["score"] == fresh["score"],
   f"{w['after']['score']} == {fresh['score']}")
ok("the recomputed lines still sum to the total",
   sum(c["points"] for c in fresh["components"] if c["points"]) == fresh["score"])
ok("it does not alter the real student record",
   R.score_student(feats)["score"] == w["before"]["score"])
ok("improving things lowers the score", R.what_if(feats, {"attendance_pct": 95})["after"]["score"]
   <= R.what_if(feats, {"attendance_pct": 40})["after"]["score"])
m = R.minimum_change_to(feats, "Low", "attendance_pct")
ok("it can say what would be needed to reach Low", bool(m and m.get("statement")),
   (m or {}).get("statement", "")[:46])


print("\nOUTCOMES ARE MEASURED, NOT REPORTED")
eff = service.get_effectiveness(con)
ok("outcomes come from attendance data", eff["measured_total"] > 0,
   f"{eff['measured_total']} measured")
rows = eff["measured"]["rows"]
ok("every playbook has a control comparison",
   all(r["control_mean_change"] is not None for r in rows))
ok("the naive rate is higher than the real effect",
   all((r["improved_rate"] or 0) > (r["estimated_effect"] or 0) for r in rows),
   "which is exactly why the control column exists")

truth_path = os.path.join(BASE, "demo_data", "true_effects.json")
if os.path.exists(truth_path):
    import json
    truth = json.load(open(truth_path, encoding="utf-8"))["effects_in_attendance_points"]
    # Only 88% of students are reached and the effect decays weekly, so the
    # recoverable share of a planted effect is about 0.76.
    errs = [abs(r["estimated_effect"] - truth[r["playbook"]] * 0.757)
            for r in rows if r["playbook"] in truth and r["estimated_effect"] is not None]
    if errs:
        mae = sum(errs) / len(errs)
        ok("it recovers effects it was never told about", mae < 2.0,
           f"average error {mae:.2f} attendance points")
    # Correlation, not exact order. The two weakest playbooks differ by about
    # 1.5 attendance points, which no sample of this size can separate reliably.
    # Demanding an exact ordering would be asserting more than the data supports.
    pairs = [(r["estimated_effect"], truth[r["playbook"]]) for r in rows
             if r["playbook"] in truth and r["estimated_effect"] is not None]
    if len(pairs) >= 3:
        est_rank = {v: i for i, v in enumerate(sorted(p[0] for p in pairs))}
        true_rank = {v: i for i, v in enumerate(sorted(p[1] for p in pairs))}
        n_ = len(pairs)
        d2 = sum((est_rank[a] - true_rank[b]) ** 2 for a, b in pairs)
        rho = 1 - (6 * d2) / (n_ * (n_ * n_ - 1))
        ok("estimated effects track the real ones", rho >= 0.75,
           f"rank correlation {rho:.2f}")
        ok("it identifies the most effective action",
           max(pairs, key=lambda p: p[0])[1] == max(truth.values()),
           f"best: {rows[0]['playbook'][:30]}")


def raises_early(fn):
    try:
        fn()
        return False
    except Exception:
        return True


print("\nA STUDENT WHO JUST JOINED")
name = f"Check Student {int(time.time())}"
new = service.admit_student(con, name=name, dept="CSE", year=1)
nd = service.get_student(con, new["roll_no"])
ok("a record exists from day one", nd is not None, new["roll_no"])
ok("no risk score is invented", nd["ledger"] is None, nd["stage"]["stage"])
ok("it explains why there is no score", bool(nd["hybrid"]["model_note"]))
ok("stages advance with weeks of data",
   lifecycle.stage_for(0) == "onboarding" and lifecycle.stage_for(6) == "rules_only"
   and lifecycle.stage_for(12) == "full")
# A student's term starts on their admission date, so attendance must be dated
# on or after it. Using an earlier date is correctly refused.
from datetime import date as _date
service.add_attendance(con, new["roll_no"], _date.today().isoformat(), 30, 40)
ok("attendance can be recorded straight away",
   lifecycle.weeks_of_data(con, new["roll_no"]) == 1)
ok("attendance dated before the student joined is refused",
   raises_early(lambda: service.add_attendance(con, new["roll_no"], "2020-01-06", 30, 40)))


print("\nMESSY SPREADSHEETS")
ok("three roll formats reconcile to one",
   ingest.normalize_roll("22CSE005") == ingest.normalize_roll(" 22cse005 ")
   == ingest.normalize_roll("GU/22/CSE/005") == "22CSE005")
ok("34/40 is read as 85%", ingest.parse_attendance("34/40") == 85.0)
ok("'85%' and 0.85 both read as 85%",
   ingest.parse_attendance("85%") == ingest.parse_attendance(0.85) == 85.0)
ok("'AB' is absence, not a zero", ingest.parse_marks("AB")[1] is True)
ok("blank marks stay blank", ingest.parse_marks("") == (None, False))
ok("fee wording is normalised",
   ingest.parse_fee_status("PAID") == ingest.parse_fee_status("paid") == "Paid")

f = os.path.join(BASE, "demo_data", "03_fees_and_backlogs.xlsx")
if os.path.exists(f):
    rep, recs = ingest.ingest_file(f)
    ok("every row is matched to a student", rep["rows_unmatched"] == 0,
       f"{rep['rows_keyed']} of {rep['rows_total']}")
    ok("phone numbers are refused", "Mobile No" in rep["blocked_columns"],
       str(rep["blocked_columns"]))
att = os.path.join(BASE, "demo_data", "01_attendance_register.xlsx")
if os.path.exists(att):
    rep2, _ = ingest.ingest_file(att)
    ok("title rows above the header are skipped",
       rep2["header_row_detected"] > 0, f"header on row {rep2['header_row_detected'] + 1}")
    ok("date columns are read as weekly attendance",
       rep2["weekly_columns_found"] > 10, f"{rep2['weekly_columns_found']} weeks")


print("\nWHAT THE SCREENS NEED")
has("worklist has what the page reads", wl,
    "this_week.name", "this_week.roll_no", "this_week.score", "this_week.band",
    "this_week.delta", "this_week.headline", "watch_page.total", "scores_computed_at")
has("student page has what it reads", sd,
    "student.name", "stage.label", "stage.explain", "ledger.score", "ledger.band",
    "ledger.components.label", "ledger.components.reason", "hybrid.rules_score",
    "attendance.pct", "suggested_playbook.title", "suggested_playbook.draft")
has("what-if has what it reads", service.what_if(con, roll, {}),
    "levers.current", "levers.fields", "before.score", "after.score", "statement")
has("effectiveness has what it reads", eff,
    "measured.rows.playbook", "measured.rows.estimated_effect",
    "measured.rows.control_mean_change", "measured.method", "measured_total")
has("fairness has what it reads", service.get_fairness(con), "dimensions", "note", "method")
has("summary has what it reads", service.get_summary(con, "rao"),
    "total_students", "bands", "data_source")
has("directory has what it reads", service.list_students(con, None, "", 1, 25),
    "total", "pages", "students.roll_no", "students.name")
mi = service.model_status(con)
if mi["trained"]:
    has("model card has what it reads", mi,
        "target", "target_is_not", "excluded_features", "beats_rules_ledger",
        "beats_persistence_baseline", "early_warning")


print("\nNOTHING PRIVATE LEAKS")
ok("gender is not on the mentor's screen", "gender" not in sd["student"])
ok("category is not on the mentor's screen", "category" not in sd["student"])
pub = service.get_student_public_view(con, roll)
# Check the actual fields, not a substring. The view's own disclaimer contains
# the word "score", which a naive search matches.
banned = {"score", "band", "risk", "risk_score", "ledger", "confidence",
          "probability", "priority", "delta"}
leaked = banned & set(pub.keys())
ok("the student is sent no score or band", not leaked,
   ("leaked: " + ", ".join(sorted(leaked))) if leaked else "")
ok("no risk wording reaches the student",
   not any(w in str(pub.get(k, "")) for k in ("suggestions", "support_available")
           for w in ("High risk", "at-risk", "at risk", "dropout", "drop out")))
ok("the student sees things they can do", len(pub["suggestions"]) > 0,
   f"{len(pub['suggestions'])} suggestions")
if mi["trained"]:
    ok("the model never sees fee status", "fee_status" in mi["excluded_features"])
    ok("the model never sees protected attributes",
       all(k in mi["excluded_features"] for k in ("gender", "category")))


print("\nBAD INPUT IS REJECTED")


def raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


ok("a missing student returns nothing",
   service.get_student(con, "NOSUCH0000") is None)
ok("a nameless admission is refused",
   raises(lambda: service.admit_student(con, name="", dept="CSE", year=1)))
ok("a duplicate roll number is refused",
   raises(lambda: service.admit_student(con, name="Dup", dept="CSE", year=1,
                                        roll_no=roll)))
ok("attendance above classes held is refused",
   raises(lambda: service.add_attendance(con, roll, "2025-07-07", 50, 40)))
ok("zero classes held is refused",
   raises(lambda: service.add_attendance(con, roll, "2025-07-07", 0, 0)))
ok("an unknown outcome is refused",
   raises(lambda: service.close_intervention(con, "IV00001", "banana", "rao")))
ok("an unknown feedback verdict is refused",
   raises(lambda: service.record_feedback(con, roll, "rao", "banana")))
ok("marks above the maximum are refused",
   raises(lambda: service.add_assessment(con, roll, "ia1", 99, 30)))
ok("an empty student still scores without crashing",
   R.score_student({})["score"] == 0)
ok("a one-week history does not crash",
   R.score_student({"weekly_attendance": [50]})["score"] >= 0)


print("\nSPEED")
for label, fn, limit in [
        ("worklist", lambda: service.get_worklist(con, "admin", 5), 0.5),
        ("summary", lambda: service.get_summary(con, "admin"), 0.5),
        ("one student", lambda: service.get_student(con, roll), 0.2),
        ("what-if", lambda: service.what_if(con, roll, {"attendance_pct": 80}), 0.5),
        ("effectiveness", lambda: service.get_effectiveness(con), 0.5),
        ("directory", lambda: service.list_students(con, None, "", 1, 50), 0.2)]:
    t = time.time()
    fn()
    el = time.time() - t
    ok(f"{label} responds quickly", el < limit, f"{el * 1000:.1f} ms")


# ---------------------------------------------------------------------------
n = con.execute("SELECT COUNT(*) c FROM students").fetchone()["c"]
print(f"\nChecked against {n:,} students.")
if fails:
    print(f"\n{len(fails)} PROBLEM(S):")
    for f_ in fails:
        print(f"  - {f_}")
    sys.exit(1)
print("Everything passed.")
