"""
Sahay - measuring whether an intervention actually worked.

WHY THIS MODULE EXISTS
Until now the mentor told the system whether an action helped. That makes the
effectiveness screen self-reported, and self-reported numbers are the first
thing a sharp judge attacks. The system already stores attendance week by week,
so it can work the answer out itself.

THE TRAP THIS MODULE AVOIDS
Interventions happen when a student is at their worst. Attendance is
mean-reverting, so many of those students improve whether or not anyone helps.
A naive "62% improved" takes credit for that. It is the single most common way
education analytics oversells itself.

So we also compute a MATCHED COMPARISON: for each helped student, find students
who were at a similar attendance level in the same week and received nothing,
and measure how much THEY changed. The difference between the two is an estimate
of the effect that regression to the mean cannot explain.

It is not a randomised trial and this module never claims it is. It is an
observational estimate with a stated method, which is a great deal more than
counting improvements.
"""

from datetime import date, datetime

TERM_START = date(2025, 7, 7)
BASELINE_WEEKS = 4        # weeks ending at the intervention
FOLLOWUP_WEEKS = 4        # weeks starting after the follow-up date
IMPROVED_PTS = 5.0        # attendance points that count as a real move
MIN_CONTROLS = 10         # below this a bucket is too thin to compare against
WASHOUT_WEEKS = 3         # a control must have had no intervention nearby

OUTCOMES = ("improved", "unchanged", "worsened", "not_measurable")


def week_index(d, term_start=TERM_START):
    if not d:
        return None
    try:
        dd = datetime.fromisoformat(str(d)[:10]).date()
    except ValueError:
        return None
    return (dd - term_start).days // 7


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def attendance_matrix(con):
    """{roll_no: [pct by week index]} loaded once. The whole module works off it."""
    m = {}
    for r in con.execute("SELECT roll_no, week_index, pct FROM attendance "
                         "ORDER BY roll_no, week_index"):
        m.setdefault(r["roll_no"], {})[r["week_index"]] = r["pct"]
    out = {}
    for roll, weeks in m.items():
        hi = max(weeks)
        out[roll] = [weeks.get(i) for i in range(hi + 1)]
    return out


def window(series, lo, hi):
    """Mean of series[lo:hi], or None if that window does not exist."""
    if lo < 0 or hi > len(series) or hi <= lo:
        return None
    return _mean(series[lo:hi])


def measure_one(series, w):
    """Attendance before and after an intervention placed at week w."""
    before = window(series, w - BASELINE_WEEKS + 1, w + 1)
    after = window(series, w + 1, w + 1 + FOLLOWUP_WEEKS)
    if before is None or after is None:
        return None
    change = after - before
    verdict = ("improved" if change >= IMPROVED_PTS else
               "worsened" if change <= -IMPROVED_PTS else "unchanged")
    return {"baseline": round(before, 1), "after": round(after, 1),
            "change": round(change, 1), "outcome": verdict}


# ----------------------------------------------------------------------------
def measure_all(con, audit_fn=None, actor="system"):
    """Fill in measured outcomes for every intervention that has enough data."""
    att = attendance_matrix(con)
    rows = con.execute("SELECT id, roll_no, created_at FROM interventions").fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    done = {"improved": 0, "unchanged": 0, "worsened": 0, "not_measurable": 0}
    updates = []

    for r in rows:
        w = week_index(r["created_at"])
        series = att.get(r["roll_no"])
        m = measure_one(series, w) if (series and w is not None) else None
        if not m:
            done["not_measurable"] += 1
            updates.append((None, None, None, "not_measurable", w, now, r["id"]))
            continue
        done[m["outcome"]] += 1
        updates.append((m["baseline"], m["after"], m["change"], m["outcome"],
                        w, now, r["id"]))

    con.executemany(
        "UPDATE interventions SET baseline_value=?, measured_value=?, "
        "measured_change=?, measured_outcome=?, baseline_week=?, measured_at=? "
        "WHERE id=?", updates)
    if audit_fn:
        audit_fn(con, actor, "outcomes_measured", "interventions",
                 f"{len(updates)} measured from attendance data")
    con.commit()
    return {"measured": len(updates), "breakdown": done,
            "method": f"attendance over the {FOLLOWUP_WEEKS} weeks after, "
                      f"against the {BASELINE_WEEKS} weeks before"}


# ----------------------------------------------------------------------------
def _control_pools(con, att, weeks_needed):
    """For each intervention week, the change seen by comparable students who
    received nothing, grouped by attendance band."""
    treated_weeks = {}
    for r in con.execute("SELECT roll_no, created_at FROM interventions"):
        w = week_index(r["created_at"])
        if w is not None:
            treated_weeks.setdefault(r["roll_no"], set()).add(w)

    pools = {}
    for w in weeks_needed:
        buckets = {}
        for roll, series in att.items():
            near = treated_weeks.get(roll)
            if near and any(abs(t - w) <= WASHOUT_WEEKS for t in near):
                continue                          # not a clean control
            m = measure_one(series, w)
            if not m:
                continue
            buckets.setdefault(int(m["baseline"] // 10), []).append(m["change"])
        pools[w] = buckets
    return pools


def matched_effect(con):
    """Estimate the effect of each playbook against comparable untreated students.

    For every helped student we find students at a similar attendance level in
    the same week who received no intervention, and subtract their average
    change from the helped student's. What remains is the part regression to the
    mean does not explain.
    """
    att = attendance_matrix(con)
    rows = con.execute(
        "SELECT id, roll_no, playbook, trigger, baseline_week, baseline_value, "
        "measured_change, measured_outcome FROM interventions "
        "WHERE measured_outcome IS NOT NULL AND measured_outcome != 'not_measurable'"
    ).fetchall()
    if not rows:
        return {"rows": [], "note": "No interventions have enough data to measure yet."}

    pools = _control_pools(con, att, {r["baseline_week"] for r in rows})
    agg = {}
    for r in rows:
        b = pools.get(r["baseline_week"], {}).get(int(r["baseline_value"] // 10))
        a = agg.setdefault(r["playbook"], {
            "playbook": r["playbook"], "trigger": r["trigger"], "n": 0,
            "improved": 0, "unchanged": 0, "worsened": 0,
            "treated_changes": [], "control_changes": [], "matched_n": 0})
        a["n"] += 1
        a[r["measured_outcome"]] += 1
        a["treated_changes"].append(r["measured_change"])
        if b and len(b) >= MIN_CONTROLS:
            a["control_changes"].append(_mean(b))
            a["matched_n"] += 1

    out = []
    for a in agg.values():
        t = _mean(a["treated_changes"])
        c = _mean(a["control_changes"])
        out.append({
            "playbook": a["playbook"], "trigger": a["trigger"], "n": a["n"],
            "improved": a["improved"], "unchanged": a["unchanged"],
            "worsened": a["worsened"],
            "improved_rate": round(a["improved"] / a["n"] * 100, 1) if a["n"] else None,
            "mean_change": round(t, 1) if t is not None else None,
            "control_mean_change": round(c, 1) if c is not None else None,
            "estimated_effect": (round(t - c, 1) if (t is not None and c is not None)
                                 else None),
            "matched_n": a["matched_n"],
        })
    out.sort(key=lambda r: (r["estimated_effect"] is None, -(r["estimated_effect"] or 0)))
    return {
        "rows": out,
        "method": (f"Attendance over the {FOLLOWUP_WEEKS} weeks after each action, "
                   f"against the {BASELINE_WEEKS} weeks before. Compared with "
                   f"students at a similar attendance level in the same week who "
                   f"received nothing (at least {MIN_CONTROLS} per comparison, and "
                   f"no intervention within {WASHOUT_WEEKS} weeks)."),
        "caution": ("Students are not assigned to actions at random, so this is an "
                    "estimate and not proof. The comparison column exists because "
                    "students helped at their worst moment often recover on their "
                    "own, and a plain improvement rate takes credit for that."),
    }
