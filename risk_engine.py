"""
Sahay - the Transparent Risk Ledger.

Design rule, and the sentence to say on stage:
    We never produce a number a mentor cannot reconstruct by hand.

Every component is additive, capped, and carries the plain-English reason it
fired. The weights sum to exactly 100, so the score IS the explanation. No SHAP,
no waterfall plots, no "the model says".

Zero dependencies. Pure stdlib, so you can unit-test it in 5 seconds.
"""

import math
from copy import deepcopy

# ----------------------------------------------------------------------------
# Configuration. Everything a college would want to tune lives here, which is
# the PS's "configurability" requirement. Expose this as the Settings screen.
# ----------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "bands": {"medium_at": 30, "high_at": 60},

    "attendance_level": {"weight": 25, "threshold_pct": 75.0, "points_per_pct": 1.2},
    "attendance_decline": {"weight": 20, "recent_weeks": 4, "prior_weeks": 4,
                           "min_drop_pct": 5.0, "points_per_pct": 1.3},
    "assessment_level": {"weight": 15, "pass_pct": 40.0, "points_per_pct": 0.5},
    "assessment_trend": {"weight": 15, "min_drop_pct": 8.0, "points_per_pct": 0.5},
    "backlogs": {"weight": 15, "points_per_backlog": 5},
    "submission": {"weight": 5, "threshold_pct": 60.0, "points_per_pct": 0.15},
    "fee": {"weight": 5, "points_pending": 5, "points_part_paid": 3},

    # ---- Ethical guardrails. These are the answer to the fairness question. ----
    "guardrails": {
        # Fee status alone can never push a student into the High band.
        "fee_cannot_reach_high": True,
        # Below this confidence we surface the score as provisional, not actionable.
        "min_confidence_to_flag": 0.4,
    },

    "priority": {"delta_weight": 1.5, "score_weight": 0.5,
                 "contact_weight": 2.0, "max_weeks_since_contact": 8},

    "cohort": {"min_students_affected": 5, "min_mean_drop_pct": 10.0},
}

LOW, MEDIUM, HIGH = "Low", "Medium", "High"


def _band(score, cfg):
    b = cfg["bands"]
    if score >= b["high_at"]:
        return HIGH
    if score >= b["medium_at"]:
        return MEDIUM
    return LOW


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _finite(v):
    """True only for a real, usable number.

    NaN needs the same treatment as None here, and it is easy to miss because
    every comparison against NaN is False. A NaN attendance percentage slides
    straight past `if current >= threshold` into the scoring branch, where
    round(nan) raises ValueError -- and because scoring runs over the whole
    cohort, one bad cell took down the worklist for every mentor. Treating a
    non-finite value as "no data" is both correct and what the callers already
    expect from None.
    """
    if v is None or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except TypeError:
        return False


def _mean(xs):
    xs = [x for x in xs if _finite(x)]
    return sum(xs) / len(xs) if xs else None


# ----------------------------------------------------------------------------
# Components. Each returns (points, reason, evidence) or None if it can't fire.
# ----------------------------------------------------------------------------
def _c_attendance_level(f, cfg):
    c = cfg["attendance_level"]
    weekly = f.get("weekly_attendance") or []
    current = _mean(weekly[-c_recent(cfg):]) if weekly else f.get("attendance_pct")
    if not _finite(current):
        return None
    if current >= c["threshold_pct"]:
        return (0, f"Attendance is {current:.0f}% (meets the {c['threshold_pct']:.0f}% minimum threshold)",
                {"attendance_pct": round(current, 1)})
    gap = c["threshold_pct"] - current
    pts = _clamp(round(gap * c["points_per_pct"]), 0, c["weight"])
    return (pts, f"Attendance is {current:.0f}% ({gap:.0f}% below the {c['threshold_pct']:.0f}% requirement)",
            {"attendance_pct": round(current, 1), "gap_pct": round(gap, 1)})


def c_recent(cfg):
    return cfg["attendance_decline"]["recent_weeks"]


def _c_attendance_decline(f, cfg):
    """The signal that makes this an EARLY warning system.

    Compares the last N weeks against the N weeks before them. A mentor can
    verify this from the register in ten seconds, which is the point.
    """
    c = cfg["attendance_decline"]
    weekly = [w for w in (f.get("weekly_attendance") or []) if w is not None]
    need = c["recent_weeks"] + c["prior_weeks"]
    if len(weekly) < need:
        return None
    recent = _mean(weekly[-c["recent_weeks"]:])
    prior = _mean(weekly[-need:-c["recent_weeks"]])
    if not (_finite(recent) and _finite(prior)):
        return None
    drop = prior - recent
    if drop < c["min_drop_pct"]:
        verb = "improved" if drop < -c["min_drop_pct"] else "held steady"
        return (0, f"Attendance has {verb} over the last {c['recent_weeks']} weeks",
                {"recent_pct": round(recent, 1), "prior_pct": round(prior, 1),
                 "drop_pct": round(drop, 1)})
    pts = _clamp(round(drop * c["points_per_pct"]), 0, c["weight"])
    return (pts, f"Attendance dropped {drop:.0f}%: averaged {prior:.0f}% for "
                 f"{c['prior_weeks']} weeks, now {recent:.0f}%",
            {"recent_pct": round(recent, 1), "prior_pct": round(prior, 1),
             "drop_pct": round(drop, 1)})


def _ia_percents(f):
    out = []
    for k in ("ia1", "ia2", "ia3"):
        v, mx = f.get(k), f.get(f"{k}_max") or 30
        if _finite(v) and mx:
            out.append((k, round(v / mx * 100, 1)))
    return out


def _c_assessment_level(f, cfg):
    c = cfg["assessment_level"]
    ias = _ia_percents(f)
    if not ias:
        return None
    label, latest = ias[-1]
    if latest >= c["pass_pct"]:
        return (0, f"Latest internal ({label.upper()}) is {latest:.0f}% (above pass mark)",
                {"latest_pct": latest, "which": label})
    gap = c["pass_pct"] - latest
    pts = _clamp(round(gap * c["points_per_pct"]), 0, c["weight"])
    return (pts, f"Latest internal ({label.upper()}) is {latest:.0f}% "
                 f"({gap:.0f}% below {c['pass_pct']:.0f}% pass mark)",
            {"latest_pct": latest, "which": label})


def _c_assessment_trend(f, cfg):
    c = cfg["assessment_trend"]
    ias = _ia_percents(f)
    if len(ias) < 2:
        return None
    (first_l, first_v), (last_l, last_v) = ias[0], ias[-1]
    drop = first_v - last_v
    if drop < c["min_drop_pct"]:
        return (0, f"Internals stable or improving ({first_v:.0f}% to {last_v:.0f}%)",
                {"from_pct": first_v, "to_pct": last_v, "drop_pct": round(drop, 1)})
    pts = _clamp(round(drop * c["points_per_pct"]), 0, c["weight"])
    return (pts, f"Internal exam scores declined {drop:.0f}%: {first_l.upper()} {first_v:.0f}% to "
                 f"{last_l.upper()} {last_v:.0f}%",
            {"from_pct": first_v, "to_pct": last_v, "drop_pct": round(drop, 1)})


def _c_backlogs(f, cfg):
    c = cfg["backlogs"]
    n = f.get("backlogs")
    if not _finite(n):
        return None
    if n == 0:
        return (0, "No backlogs carried", {"backlogs": 0})
    pts = _clamp(n * c["points_per_backlog"], 0, c["weight"])
    return (pts, f"{n} backlog{'s' if n != 1 else ''} carried forward", {"backlogs": n})


def _c_submission(f, cfg):
    c = cfg["submission"]
    v = f.get("submission_pct")
    if not _finite(v):
        return None
    if v >= c["threshold_pct"]:
        return (0, f"Assignment submissions at {v:.0f}%", {"submission_pct": round(v, 1)})
    gap = c["threshold_pct"] - v
    pts = _clamp(round(gap * c["points_per_pct"]), 0, c["weight"])
    return (pts, f"Assignment submissions at {v:.0f}%, below the {c['threshold_pct']:.0f}% mark",
            {"submission_pct": round(v, 1)})


def _c_fee(f, cfg):
    """Fee is an institutional signal, never a verdict on the student.

    It contributes at most 5 of 100 points and, under the default guardrail,
    can never by itself put a student in the High band. It routes to a
    scholarship referral instead.
    """
    c = cfg["fee"]
    status = f.get("fee_status")
    if not status or status == "Unknown":
        return None
    if status == "Paid":
        return (0, "Fees up to date", {"fee_status": status})
    pts = c["points_pending"] if status == "Pending" else c["points_part_paid"]
    return (pts, f"Fee status: {status} (routes to a scholarship or instalment referral, "
                 f"not to an escalation)",
            {"fee_status": status})


COMPONENTS = [
    ("attendance_level", "Attendance level", _c_attendance_level),
    ("attendance_decline", "Attendance change", _c_attendance_decline),
    ("assessment_level", "Assessment level", _c_assessment_level),
    ("assessment_trend", "Assessment trend", _c_assessment_trend),
    ("backlogs", "Backlogs", _c_backlogs),
    ("submission", "Assignment consistency", _c_submission),
    ("fee", "Fee status", _c_fee),
]


# ----------------------------------------------------------------------------
# The ledger
# ----------------------------------------------------------------------------
def score_student(features, config=None):
    """Return the full ledger for one student.

    features may include:
      weekly_attendance : [float|None]  most recent last
      attendance_pct    : float         fallback if no weekly series
      ia1/ia2/ia3 + ia1_max/...        : float
      backlogs, submission_pct, fee_status
    """
    cfg = config or DEFAULT_CONFIG
    rows, total, present = [], 0, 0

    for key, label, fn in COMPONENTS:
        res = fn(features, cfg)
        if res is None:
            rows.append({"key": key, "label": label, "points": None,
                         "max_points": cfg[key]["weight"], "reason": "No data available",
                         "evidence": {}, "available": False})
            continue
        pts, reason, evidence = res
        present += 1
        total += pts
        rows.append({"key": key, "label": label, "points": pts,
                     "max_points": cfg[key]["weight"], "reason": reason,
                     "evidence": evidence, "available": True})

    total = int(_clamp(total, 0, 100))

    # Band thresholds scale to what could actually be measured.
    #
    # Without this, a student whose marks are simply not recorded can never
    # score above 70, because 30 points of the scale are unavailable to them.
    # They would sit in Medium while a student with identical attendance and
    # complete records sits in High. Missing paperwork would quietly hide the
    # students most likely to be missing from class, which is precisely
    # backwards. Scaling the thresholds asks "how bad is this out of what we can
    # see", and the receipt above is untouched: it still sums to `total`.
    max_available = sum(r["max_points"] for r in rows if r["available"]) or 100
    scaled = {"medium_at": cfg["bands"]["medium_at"] * max_available / 100.0,
              "high_at": cfg["bands"]["high_at"] * max_available / 100.0}
    band = _band(total, {"bands": scaled})

    # ---- Fee guardrail ----
    guardrails = []
    fee_pts = next((r["points"] for r in rows if r["key"] == "fee" and r["points"]), 0) or 0
    if cfg["guardrails"]["fee_cannot_reach_high"] and band == HIGH:
        if _band(total - fee_pts, {"bands": scaled}) != HIGH:
            band = MEDIUM
            guardrails.append({
                "rule": "fee_cannot_reach_high",
                "message": f"Capped at Medium: without the {fee_pts}-point fee signal this "
                           f"student scores {total - fee_pts}. Fee status alone never "
                           f"produces a High-risk flag.",
            })

    confidence = round(present / len(COMPONENTS), 2)
    provisional = confidence < cfg["guardrails"]["min_confidence_to_flag"]
    if provisional:
        # Too little evidence to put anyone in the top band. Scaling thresholds
        # to what is measurable is right, but on two signals out of seven it
        # would let a thin record shout as loudly as a complete one.
        if band == HIGH:
            band = MEDIUM
        guardrails.append({
            "rule": "min_confidence_to_flag",
            "message": f"Held at Medium: only {present} of {len(COMPONENTS)} kinds of "
                       f"information are recorded for this student. Shown for "
                       f"context rather than queued for action.",
        })

    driver = max((r for r in rows if r["available"] and r["points"]),
                 key=lambda r: r["points"], default=None)

    return {
        "score": total,
        "band": band,
        "max_available": max_available,
        "scale_note": (None if max_available == 100 else
                       f"{total} out of a possible {max_available}, because "
                       f"{len(COMPONENTS) - present} of {len(COMPONENTS)} kinds of "
                       f"information are not recorded for this student."),
        "confidence": confidence,
        "signals_present": present,
        "signals_total": len(COMPONENTS),
        "provisional": provisional,
        "components": rows,
        "guardrails": guardrails,
        "primary_driver": driver["key"] if driver else None,
        "primary_driver_label": driver["label"] if driver else None,
        "headline": driver["reason"] if driver else "No risk signals firing",
        "scholarship_referral": (features.get("fee_status") in ("Pending", "Part Paid")),
        "mode": "Rules Mode",   # flip to "Hybrid" only when real outcomes exist
    }


def score_as_of(features, weeks_ago, config=None):
    """Re-score using only data up to N weeks ago. Powers the delta."""
    f = deepcopy(features)
    weekly = f.get("weekly_attendance") or []
    if weeks_ago and len(weekly) > weeks_ago:
        f["weekly_attendance"] = weekly[:-weeks_ago]
    return score_student(f, config)


def risk_delta(features, weeks_ago=4, config=None):
    now = score_student(features, config)["score"]
    then = score_as_of(features, weeks_ago, config)["score"]
    return {"now": now, "then": then, "delta": now - then, "weeks": weeks_ago}


# ----------------------------------------------------------------------------
# The worklist. Rank by CHANGE, cap at mentor capacity.
# ----------------------------------------------------------------------------
def priority_score(ledger, delta, weeks_since_contact, config=None):
    cfg = config or DEFAULT_CONFIG
    p = cfg["priority"]
    contact = min(weeks_since_contact or 0, p["max_weeks_since_contact"])
    return round(max(0, delta) * p["delta_weight"]
                 + ledger["score"] * p["score_weight"]
                 + contact * p["contact_weight"], 1)




MAX_PER_COHORT = 2


def build_worklist(students, capacity=5, config=None, cohort_alerts=None):
    """students: [{roll_no, name, dept, year, section, features, weeks_since_contact}]

    Returns exactly `capacity` items, plus a watch list and a routed list.

    Two decisions matter here, and both are the difference between a tool a
    mentor uses and a dashboard a mentor closes:

    1. The list is CAPPED at the mentor's stated capacity.
    2. Students whose decline is fully explained by a section-wide anomaly are
       ROUTED OUT for department review rather than filling the mentor's five
       slots. A
       student who fell substantially further than their section still appears,
       because that excess is individual.
    """
    cfg = config or DEFAULT_CONFIG
    cohort_map = {(a["dept"], a["year"], a["section"]): a
                  for a in (cohort_alerts or [])}
    scored, routed, healthy = [], [], []

    for s in students:
        ledger = score_student(s["features"], cfg)
        d = risk_delta(s["features"], config=cfg)
        if ledger["provisional"]:
            continue

        item = {
            "roll_no": s["roll_no"], "name": s.get("name"),
            "section": s.get("section"), "dept": s.get("dept"), "year": s.get("year"),
            "score": ledger["score"], "band": ledger["band"],
            "delta": d["delta"], "confidence": ledger["confidence"],
            "headline": ledger["headline"],
            "primary_driver": ledger["primary_driver"],
            "guardrails": ledger["guardrails"],
            "priority": priority_score(ledger, d["delta"],
                                       s.get("weeks_since_contact", 0), cfg),
            "explained_by_cohort": None,
        }

        # A student below the action threshold is still SCORED -- they are just
        # not work for this week. They belong in the directory and in the band
        # counts, not in the mentor's five slots.
        if ledger["score"] < cfg["bands"]["medium_at"] and d["delta"] < 10:
            healthy.append(item)
            continue

        alert = cohort_map.get((s.get("dept"), s.get("year"), s.get("section")))
        if alert and ledger["primary_driver"] in ("attendance_decline", "attendance_level"):
            dec = next((c for c in ledger["components"]
                        if c["key"] == "attendance_decline"), None)
            own_drop = (dec or {}).get("evidence", {}).get("drop_pct") or 0
            # Route out unless the student fell more than one standard deviation
            # beyond their own section. A fixed multiplier is arbitrary; the
            # section's own spread is the right yardstick for "worse than peers".
            if own_drop <= alert["excess_threshold_pct"]:
                item["explained_by_cohort"] = {
                    "cohort": alert["cohort"],
                    "own_drop_pct": round(own_drop, 1),
                    "cohort_mean_drop_pct": alert["mean_drop_pct"],
                    "cohort_excess_threshold_pct": alert["excess_threshold_pct"],
                    "message": (f"Fell {own_drop:.0f} points against a {alert['cohort']} "
                                f"section average of {alert['mean_drop_pct']:.0f} "
                                f"(+/-{alert['drop_std_pct']:.0f}). Within the section's own "
                                f"spread, so handled as one cohort issue for "
                                f"department review rather than as an individual case."),
                }
                routed.append(item)
                continue
        scored.append(item)

    scored.sort(key=lambda x: (-x["priority"], -x["delta"], -x["score"]))
    routed.sort(key=lambda x: -x["priority"])

    # Diversity cap. Even after routing out the students a section-wide drop
    # explains, its tail can still fill the mentor's whole list. At most
    # MAX_PER_COHORT slots go to any one alerted section: if a section has
    # collapsed, the mentor should be escalating it as one issue, not picking off
    # its students one at a time. The rest keep their rank in the watch list.
    top, spill, used = [], [], {}
    alerted = set(cohort_map.keys())
    for item in scored:
        key = (item["dept"], item["year"], item["section"])
        if len(top) < capacity and (key not in alerted
                                    or used.get(key, 0) < MAX_PER_COHORT):
            top.append(item)
            used[key] = used.get(key, 0) + 1
        else:
            spill.append(item)

    return {"this_week": top,
            "watch": spill,
            "routed_to_cohort": routed,
            "healthy": healthy,
            "capacity": capacity,
            "total_flagged": len(scored),
            "total_routed": len(routed)}


# ----------------------------------------------------------------------------
# Cohort anomalies. Not every risk signal is the student's fault.
# ----------------------------------------------------------------------------
def detect_cohort_anomalies(students, config=None):
    """Group by (dept, year, section) and find section-wide attendance drops.

    A section-wide drop is a timetable, faculty or hostel problem. Routing it to
    a mentor as 30 individual alerts is the wrong answer; escalating it for
    department review as one alert is the right one.
    """
    cfg = config or DEFAULT_CONFIG
    c, groups = cfg["cohort"], {}
    dc = cfg["attendance_decline"]
    need = dc["recent_weeks"] + dc["prior_weeks"]

    for s in students:
        weekly = [w for w in (s["features"].get("weekly_attendance") or []) if w is not None]
        if len(weekly) < need:
            continue
        recent = _mean(weekly[-dc["recent_weeks"]:])
        prior = _mean(weekly[-need:-dc["recent_weeks"]])
        key = (s.get("dept"), s.get("year"), s.get("section"))
        groups.setdefault(key, []).append({"roll_no": s["roll_no"], "drop": prior - recent,
                                           "recent": recent, "prior": prior})

    alerts = []
    for (dept, year, section), members in groups.items():
        drops = [m["drop"] for m in members]
        mean_drop = _mean(drops)
        affected = [m for m in members if m["drop"] >= c["min_mean_drop_pct"]]
        if mean_drop is None:
            continue
        if mean_drop >= c["min_mean_drop_pct"] and len(affected) >= c["min_students_affected"]:
            sd = (sum((d - mean_drop) ** 2 for d in drops) / len(drops)) ** 0.5
            alerts.append({
                "excess_threshold_pct": round(mean_drop + sd, 1),
                "drop_std_pct": round(sd, 1),
                "cohort": f"{dept}-{year}{section}",
                "dept": dept, "year": year, "section": section,
                "students_in_cohort": len(members),
                "students_affected": len(affected),
                "mean_drop_pct": round(mean_drop, 1),
                "mean_recent_pct": round(_mean([m['recent'] for m in members]), 1),
                "mean_prior_pct": round(_mean([m['prior'] for m in members]), 1),
                # "Head of Department", not "Department review": this is
                # rendered into the sentence "Send this to the {route_to}."
                # (static/index.html), which needs a role rather than an
                # activity, and check.py asserts the same. The three had drifted
                # apart, so the UI read "Send this to the Department review."
                "route_to": "Head of Department",
                "message": (f"Attendance down {mean_drop:.0f} points "
                            f"across {len(affected)} of {len(members)} students. "
                            f"This pattern is section-wide, so treat it as a timetable, "
                            f"faculty-slot or hostel issue before treating it as "
                            f"{len(affected)} individual cases."),
            })
    alerts.sort(key=lambda a: -a["mean_drop_pct"])
    return alerts


# ----------------------------------------------------------------------------
# What-if. Exact, not estimated.
# ----------------------------------------------------------------------------
# Every other team's what-if re-queries a black box and gets an approximation.
# Ours re-runs the same arithmetic on modified inputs, so the answer is exactly
# what the ledger would say if those numbers were real. That is a consequence of
# choosing an additive score, and it is worth saying out loud.

# ----------------------------------------------------------------------------
# Playbook routing. AI drafts, the mentor approves.
# ----------------------------------------------------------------------------
PLAYBOOKS = {
    "attendance_decline": {
        "trigger": "sudden_disengagement",
        "title": "15-minute check-in call",
        "draft": ("Attendance dropped sharply and recently, which usually means something "
                  "changed in the student's circumstances rather than in their ability. "
                  "Open with an unprompted, non-disciplinary check-in. Ask what changed in "
                  "the last month before discussing attendance rules."),
        "followup_days": 21, "measure": "attendance_pct",
    },
    "attendance_level": {
        "trigger": "chronic_low_attendance",
        "title": "Weekly attendance contract",
        "draft": ("Attendance has been persistently low rather than newly falling. A single "
                  "conversation is unlikely to shift it. Agree a written weekly target with "
                  "the student and a specific person who checks in each Friday."),
        "followup_days": 28, "measure": "attendance_pct",
    },
    "assessment_trend": {
        "trigger": "assessment_decline",
        "title": "Subject remedial slot",
        "draft": ("Internal marks are trending down across assessments. Identify the two "
                  "weakest subjects from the internals and book the student into the "
                  "existing remedial slot rather than creating a new plan."),
        "followup_days": 21, "measure": "latest_ia_pct",
    },
    "assessment_level": {
        "trigger": "assessment_decline",
        "title": "Subject remedial slot",
        "draft": ("Latest internal is below the pass mark. Book the remedial slot and confirm "
                  "the student knows the re-assessment dates."),
        "followup_days": 21, "measure": "latest_ia_pct",
    },
    "backlogs": {
        "trigger": "backlog_burden",
        "title": "Backlog clearing plan and peer mentor",
        "draft": ("Carried backlogs compound each semester. Map which backlogs can be "
                  "cleared this cycle, and pair the student with a senior who has cleared "
                  "the same subject."),
        "followup_days": 42, "measure": "backlogs",
    },
    "submission": {
        "trigger": "low_submission",
        "title": "Assignment catch-up plan",
        "draft": ("Submissions are inconsistent while attendance holds, which often points "
                  "to workload or planning rather than disengagement. Agree a catch-up "
                  "order for outstanding assignments."),
        "followup_days": 14, "measure": "submission_pct",
    },
    "fee": {
        "trigger": "fee_pressure",
        "title": "Scholarship or instalment referral",
        "draft": ("Fee status is pending. Refer to the scholarship or instalment desk. Do "
                  "not raise this as an academic concern and do not discuss it in front of "
                  "other students."),
        "followup_days": 14, "measure": "fee_status",
    },
}


def suggest_playbook(ledger):
    """Route from the dominant driver. Returns a draft for mentor approval."""
    pb = PLAYBOOKS.get(ledger["primary_driver"])
    if not pb:
        return None
    return {**pb,
            "because": ledger["headline"],
            "requires_mentor_approval": True,
            "approved": False}


# ----------------------------------------------------------------------------
# What-if. Exact, because the ledger is additive.
# ----------------------------------------------------------------------------
# Most systems answer "what if attendance improved?" by re-querying a black box
# and reporting a number nobody can check. Here the score IS the arithmetic, so
# changing an input and re-adding the column gives an answer that is exactly
# right rather than approximately right. Say "exact" on stage and mean it.

WHAT_IF_FIELDS = {
    "attendance_pct": {"label": "Attendance", "unit": "%", "min": 0, "max": 100, "step": 1},
    "latest_ia_pct": {"label": "Next internal", "unit": "%", "min": 0, "max": 100, "step": 1},
    "submission_pct": {"label": "Assignments submitted", "unit": "%", "min": 0, "max": 100, "step": 1},
    "backlogs": {"label": "Backlogs cleared to", "unit": "", "min": 0, "max": 8, "step": 1},
    "fee_status": {"label": "Fee status", "unit": "", "options": ["Paid", "Part Paid", "Pending"]},
}


def current_levers(features, config=None):
    """Where each what-if control should start, i.e. the student's real values.

    Returned alongside WHAT_IF_FIELDS so the interface can place its sliders on
    today's numbers rather than at an arbitrary midpoint.
    """
    cfg = config or DEFAULT_CONFIG
    weekly = [w for w in (features.get("weekly_attendance") or []) if w is not None]
    n = cfg["attendance_decline"]["recent_weeks"]
    ias = _ia_percents(features)
    return {
        "attendance_pct": round(_mean(weekly[-n:]), 1) if weekly else features.get("attendance_pct"),
        "latest_ia_pct": ias[-1][1] if ias else None,
        "submission_pct": features.get("submission_pct"),
        "backlogs": features.get("backlogs"),
        "fee_status": features.get("fee_status"),
    }


def apply_changes(features, changes, config=None):
    """Return a copy of `features` with the requested changes applied.

    Setting attendance rewrites the most recent weeks rather than a single
    aggregate, so the decline component responds too. Improving attendance
    should reduce BOTH the level penalty and the decline penalty, and it does.
    """
    cfg = config or DEFAULT_CONFIG
    f = deepcopy(features)

    if "attendance_pct" in changes:
        v = float(changes["attendance_pct"])
        n = cfg["attendance_decline"]["recent_weeks"]
        weekly = list(f.get("weekly_attendance") or [])
        if weekly:
            f["weekly_attendance"] = weekly[:-n] + [v] * min(n, len(weekly))
        f["attendance_pct"] = v

    if "latest_ia_pct" in changes:
        pct = float(changes["latest_ia_pct"])
        for k in ("ia3", "ia2", "ia1"):
            if f.get(k) is not None:
                f[k] = pct / 100 * (f.get(f"{k}_max") or 30)
                break

    for k in ("submission_pct", "backlogs"):
        if k in changes:
            f[k] = float(changes[k]) if k == "submission_pct" else int(changes[k])
    if "fee_status" in changes:
        f["fee_status"] = changes["fee_status"]
    return f


def what_if(features, changes, config=None):
    """Before and after, line by line. Every number here is exact."""
    cfg = config or DEFAULT_CONFIG
    before = score_student(features, cfg)
    after = score_student(apply_changes(features, changes, cfg), cfg)

    b = {c["key"]: c for c in before["components"]}
    lines = []
    for c in after["components"]:
        was, now = b[c["key"]]["points"], c["points"]
        if was == now:
            continue
        lines.append({"key": c["key"], "label": c["label"],
                      "was": was, "now": now,
                      "change": (now or 0) - (was or 0),
                      "reason": c["reason"]})

    moved = before["band"] != after["band"]
    return {
        "changes_applied": changes,
        "before": {"score": before["score"], "band": before["band"]},
        "after": {"score": after["score"], "band": after["band"]},
        "score_change": after["score"] - before["score"],
        "band_moved": moved,
        "lines": lines,
        "exact": True,
        "statement": (
            f"Risk would go from {before['score']} to {after['score']} out of 100"
            + (f", moving from {before['band']} to {after['band']}." if moved else
               f", staying in {after['band']}.")),
        "caveat": ("This is arithmetic on the same rules, not a prediction that the "
                   "change will happen or that it would cause the student to stay."),
    }


def minimum_change_to(features, target_band="Low", field="attendance_pct", config=None):
    """Smallest value of `field` that reaches `target_band`, or None if it cannot.

    Scans rather than solves algebraically, because components are capped and
    piecewise. A scan over a bounded range is exact and takes under a millisecond.
    """
    cfg = config or DEFAULT_CONFIG
    order = {LOW: 0, MEDIUM: 1, HIGH: 2}
    if order[score_student(features, cfg)["band"]] <= order[target_band]:
        return {"already_there": True}

    spec = WHAT_IF_FIELDS.get(field)
    if not spec or "options" in spec:
        return None
    lo, hi, step = spec["min"], spec["max"], spec["step"]
    rng = range(int(lo), int(hi) + 1, int(step))
    if field == "backlogs":
        rng = reversed(list(rng))          # fewer backlogs is the improvement

    for v in rng:
        s = score_student(apply_changes(features, {field: v}, cfg), cfg)
        if order[s["band"]] <= order[target_band]:
            return {"already_there": False, "field": field, "label": spec["label"],
                    "value": v, "unit": spec["unit"],
                    "resulting_score": s["score"], "resulting_band": s["band"],
                    "statement": (f"{spec['label']} would need to reach "
                                  f"{v}{spec['unit']} to move this student to "
                                  f"{target_band}.")}
    return {"already_there": False, "field": field, "label": spec["label"],
            "value": None,
            "statement": (f"Changing {spec['label'].lower()} alone cannot reach "
                          f"{target_band}. More than one thing needs to move.")}


# ----------------------------------------------------------------------------
# Effectiveness. The screen nobody else has.
# ----------------------------------------------------------------------------
def intervention_effectiveness(interventions):
    """Aggregate closed interventions into win rates per playbook.

    interventions: [{trigger, playbook, status, outcome}]
    Only closed ones count. 'unreachable' is reported separately, because a
    student you could not contact is a different failure from an action that
    did not work.
    """
    agg = {}
    for iv in interventions:
        if iv.get("status") != "closed":
            continue
        k = (iv.get("trigger"), iv.get("playbook"))
        a = agg.setdefault(k, {"trigger": k[0], "playbook": k[1], "total": 0,
                               "improved": 0, "unchanged": 0, "worsened": 0,
                               "unreachable": 0})
        a["total"] += 1
        o = iv.get("outcome")
        if o in a:
            a[o] += 1

    rows = []
    for a in agg.values():
        reached = a["total"] - a["unreachable"]
        a["reached"] = reached
        a["improvement_rate"] = round(a["improved"] / reached * 100, 1) if reached else None
        a["contact_rate"] = round(reached / a["total"] * 100, 1) if a["total"] else None
        rows.append(a)
    rows.sort(key=lambda r: (r["improvement_rate"] is None, -(r["improvement_rate"] or 0)))
    return {"rows": rows,
            "note": "Simulated pilot data. Replace with live outcomes after deployment.",
            "closed_total": sum(r["total"] for r in rows)}
