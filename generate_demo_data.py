"""
Sahay - synthetic cohort generator (v2).

Two changes from v1:

1. SCALE. --students N builds any cohort size. Departments, years and sections
   all scale with it. Tested to 50,000.

2. REALISTIC DYNAMICS. v1 drew attendance from independent noise plus a few
   hand-scripted drops, which means week-to-week values carried no memory and a
   model could not learn anything real from them. v2 runs a latent engagement
   process per student:

       E(t+1) = E(t) + pull-toward-baseline + shock(t)

   where shocks arrive randomly, vary in size, and DECAY at a rate set by each
   student's 'fragility'. A resilient student absorbs a setback in two weeks. A
   fragile student's setback compounds into a spiral.

   This matters because it makes early warning genuinely learnable: a small dip
   in week 8 really does carry information about a collapse in week 16, for the
   students who are fragile. That is the signal a model is supposed to find, and
   v1 did not contain it.

Everything here is SYNTHETIC. Say so on stage and label it in the UI.

    python generate_demo_data.py                      # 480 students
    python generate_demo_data.py --students 20000     # any size
    python generate_demo_data.py --students 5000 --weeks 40 --seed 7
"""

import argparse
import csv
import os
import random
from datetime import date, timedelta

import numpy as np
import openpyxl
from openpyxl.styles import Font

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data")

# Large enough that a cohort of any size gets plausible names. Repeats are
# allowed on purpose: real colleges have three students called Priya Sharma,
# and the roll number is what tells them apart. The previous version appended a
# number when it ran out of combinations, which produced "Manoj Verma 2144" on
# screen and looked exactly like fake data.
FIRST_NAMES = [
    "Aarav", "Priya", "Rohit", "Ananya", "Vikram", "Sneha", "Karthik", "Divya",
    "Arjun", "Meera", "Rahul", "Kavya", "Suresh", "Pooja", "Nikhil", "Anjali",
    "Manoj", "Swathi", "Ravi", "Lakshmi", "Sandeep", "Harini", "Vishal", "Nandini",
    "Praveen", "Deepika", "Kiran", "Bhavana", "Naveen", "Sushma", "Ajay", "Ritika",
    "Sathish", "Madhuri", "Gopal", "Sirisha", "Yash", "Tejaswi", "Imran", "Farah",
    "Abhinav", "Keerthi", "Charan", "Varsha", "Dinesh", "Anusha", "Mahesh", "Pallavi",
    "Rakesh", "Shreya", "Aditya", "Ishita", "Vivek", "Neha", "Siddharth", "Aishwarya",
    "Gaurav", "Sanjana", "Hemant", "Trisha", "Prakash", "Rekha", "Anil", "Shalini",
    "Bharat", "Vaishnavi", "Chetan", "Ramya", "Girish", "Sowmya", "Jatin", "Prerana",
    "Lokesh", "Aparna", "Nagraj", "Bindu", "Omkar", "Chaitra", "Pranav", "Devika",
    "Raghav", "Gayatri", "Sagar", "Jyothi", "Tarun", "Kalyani", "Umesh", "Malini",
    "Varun", "Nithya", "Zubair", "Ayesha", "Fahad", "Rukhsar", "Irfan", "Sana",
    "Joseph", "Anita", "Thomas", "Grace", "Daniel", "Ruth", "Samuel", "Esther",
    "Amit", "Shweta", "Rohan", "Pooja", "Nitin", "Sneha", "Kunal", "Preeti",
]
LAST_NAMES = [
    "Sharma", "Reddy", "Nair", "Patel", "Rao", "Iyer", "Verma", "Naidu",
    "Kumar", "Gupta", "Menon", "Chowdary", "Singh", "Das", "Joshi", "Pillai",
    "Bose", "Shetty", "Kulkarni", "Mishra", "Banerjee", "Fernandes",
    "Agarwal", "Bhat", "Chauhan", "Desai", "Ghosh", "Hegde", "Jain", "Khanna",
    "Malhotra", "Nambiar", "Oberoi", "Prasad", "Raut", "Sinha", "Trivedi", "Varma",
    "Acharya", "Bhandari", "Chopra", "Dubey", "Gowda", "Kaur", "Lal", "Mehta",
    "Pandey", "Rathore", "Saxena", "Thakur", "Yadav", "Bajaj", "Dutta", "Kamath",
    "Mukherjee", "Panicker", "Sequeira", "Tiwari", "Vaidya", "Wadhwa",
]
DEPT_POOL = ["CSE", "ECE", "MEC", "CIV", "EEE", "IT", "CHE", "AER", "BIO", "MET"]
PER_SECTION = 40


# ----------------------------------------------------------------------------
# CGPA
# ----------------------------------------------------------------------------
# Backlogs are the dominant signal for past academic standing, so the band is
# chosen by backlog count and engagement only positions the student inside it.
CGPA_BANDS = (
    (0, 8.0, 9.0),    # nothing carried forward
    (2, 7.0, 8.0),    # one or two
    (99, 5.0, 6.0),   # more than two
)


def cgpa_for(backlogs, mean_eng, rng=np.random):
    lo, hi = next((lo, hi) for cap, lo, hi in CGPA_BANDS if backlogs <= cap)
    # Engagement places the student in the band; the jitter stops the whole
    # cohort landing on identical values.
    pos = float(np.clip(mean_eng + rng.normal(0, 0.12), 0.0, 1.0))
    return round(float(np.clip(lo + pos * (hi - lo), lo, hi)), 2)


# ----------------------------------------------------------------------------
# Latent engagement process
# ----------------------------------------------------------------------------
class Student:
    __slots__ = ("roll_no", "name", "dept", "year", "section", "gender", "category",
                 "first_gen", "hostel", "fragility", "baseline",
                 "att", "marks", "backlogs", "submission_pct", "fee_status", "dropout",
                 "cgpa")

    def __init__(self, rng, roll_no, name, dept, year, section):
        self.roll_no, self.name = roll_no, name
        self.dept, self.year, self.section = dept, year, section
        self.gender = rng.choice(["M", "F"])
        self.category = rng.choices(["GEN", "OBC", "SC", "ST"], weights=[40, 35, 17, 8])[0]
        self.first_gen = rng.random() < 0.28
        self.hostel = rng.random() < 0.45
        # Fragility: how long a setback lingers. This one parameter is what
        # makes early warning learnable rather than a coin flip.
        self.fragility = float(np.clip(np.random.beta(2.2, 5.0), 0.02, 0.95))
        self.baseline = float(np.clip(np.random.normal(0.84, 0.10), 0.35, 0.99))


def _assessment_windows(n_weeks):
    third = max(1, n_weeks // 3)
    return [(0, third), (third, 2 * third), (2 * third, n_weeks)]


def run_trajectory(s, n_weeks, cohort_shock=None):
    E = s.baseline
    carry = 0.0
    decay = 0.35 + 0.59 * s.fragility          # 0.35 bounces back, 0.94 compounds
    shock_rate = 0.035 + 0.10 * s.fragility
    series = []

    for w in range(n_weeks):
        if np.random.random() < shock_rate:                       # illness, family, work
            carry -= abs(np.random.normal(0.10, 0.07)) * (0.6 + s.fragility)
        if np.random.random() < 0.030:                            # help arrived
            carry += abs(np.random.normal(0.07, 0.04))
        carry *= decay
        E = float(np.clip(E + 0.22 * (s.baseline - E) + carry + np.random.normal(0, 0.022),
                          0.02, 1.0))

        e_obs = E
        if cohort_shock and w >= cohort_shock["week"]:
            e_obs = max(0.02, E - cohort_shock["size"])           # section-wide, not personal
        if w in (int(n_weeks * 0.42), int(n_weeks * 0.44)):
            e_obs -= np.random.uniform(0.02, 0.07)                # festival weeks
        series.append(float(np.clip(e_obs, 0.02, 1.0)))

    s.att = [float(np.clip(e * 100 + np.random.normal(0, 3.0), 2, 100)) for e in series]

    marks = []
    for lo, hi in _assessment_windows(n_weeks):
        eng = float(np.mean(series[lo:hi]))
        v = eng * 27 + np.random.normal(0, 3.0) - (3.0 if s.fragility > 0.7 else 0.0)
        marks.append("AB" if np.random.random() < 0.035 else int(np.clip(round(v), 0, 30)))
    s.marks = marks

    mean_eng = float(np.mean(series))
    s.submission_pct = float(np.clip(mean_eng * 100 + np.random.normal(0, 11), 5, 100))
    s.backlogs = int(np.clip(np.random.poisson(max(0.05, (1 - mean_eng) * 3.2)), 0, 8))
    # Fee status is drawn INDEPENDENTLY of engagement, on purpose. It is an
    # institutional fact, not evidence about the student, and the fairness audit
    # needs to be able to demonstrate that.
    s.fee_status = str(np.random.choice(["Paid", "Pending", "Part Paid"], p=[0.78, 0.14, 0.08]))
    # CGPA banded by backlogs carried forward. Position within the band is
    # nudged by engagement, so a student at the top of the 7-8 band reads
    # differently from one at the bottom, but the band itself is decided by
    # backlogs alone.
    s.cgpa = cgpa_for(s.backlogs, mean_eng)
    s.dropout = int(s.backlogs >= 2 or (mean_eng < 0.7 and np.random.random() < 0.3))
    return s


# ----------------------------------------------------------------------------
# Cohort construction
# ----------------------------------------------------------------------------
def build_cohort(n_students, n_weeks, rng, per_section=None):
    per_section = per_section or PER_SECTION
    combos = [(d, y, s) for d in DEPT_POOL for y in (2, 3, 4) for s in ("A", "B", "C")]
    needed = max(1, -(-n_students // per_section))
    while len(combos) < needed:                       # scale past the base pool
        extra = len(combos) // 90 + 1
        combos += [(f"{d}{extra}", y, s) for d in DEPT_POOL for y in (2, 3, 4)
                   for s in ("A", "B", "C")]
    combos = combos[:needed]

    anomaly = combos[min(3, len(combos) - 1)]
    # Must land inside the engine's 4-vs-4 comparison window. A step change
    # seven weeks back is correctly invisible to a CHANGE detector.
    anomaly_week = int(n_weeks * 0.88)

    students, serial = [], {}
    for (dept, year, sec) in combos:
        if len(students) >= n_students:
            break
        for _ in range(min(per_section, n_students - len(students))):
            nm = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            k = (dept, year)
            serial[k] = serial.get(k, 0) + 1
            st = Student(rng, f"{25 - year}{dept}{serial[k]:04d}", nm, dept, year, sec)
            cs = {"week": anomaly_week, "size": 0.20} if (dept, year, sec) == anomaly else None
            students.append(run_trajectory(st, n_weeks, cs))
    return students, sorted({c[0] for c in combos}), anomaly, anomaly_week


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------
def write_master(students, n_weeks, week_starts):
    cols = (["roll_no", "name", "dept", "year", "section", "gender", "category",
             "first_gen", "hostel", "ia1", "ia2", "ia3", "backlogs",
             "submission_pct", "fee_status", "cgpa", "dropout"]
            + [f"w{i:02d}" for i in range(n_weeks)])
    with open(os.path.join(OUT, "master.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for s in students:
            m = [(None if v == "AB" else v) for v in s.marks]
            w.writerow([s.roll_no, s.name, s.dept, s.year, s.section, s.gender,
                        s.category, int(s.first_gen), int(s.hostel),
                        m[0], m[1], m[2], s.backlogs, round(s.submission_pct, 1),
                        s.fee_status.title(), s.cgpa, s.dropout]
                       + [round(a, 1) for a in s.att])
    with open(os.path.join(OUT, "week_starts.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["week_index", "week_start"])
        for i, d in enumerate(week_starts):
            w.writerow([i, d.isoformat()])


def write_messy_files(students, week_starts, sample):
    """Three files, three formats, three roll-number conventions.

    Only a sample goes into the spreadsheets: a 50,000-row workbook is not what
    you upload live on stage. The full cohort stays in master.csv.
    """
    subset = students[:sample]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)
    ws.cell(row=1, column=1, value="GITAM UNIVERSITY - DEEMED TO BE UNIVERSITY").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value="Consolidated Attendance Register | Odd Semester 2025-26")
    ws.cell(row=3, column=1, value="(generated from Biometric + Manual registers)")
    header = ["Roll No", "Student Name", "Branch", "Yr", "Sec"] + \
             [d.strftime("%d-%m-%Y") for d in week_starts]
    for c, h in enumerate(header, start=1):
        ws.cell(row=5, column=c, value=h).font = Font(bold=True)
    for r, s in enumerate(subset, start=6):
        ws.cell(row=r, column=1, value=s.roll_no)
        ws.cell(row=r, column=2, value=s.name)
        ws.cell(row=r, column=3, value=s.dept)
        ws.cell(row=r, column=4, value=s.year)
        ws.cell(row=r, column=5, value=s.section)
        for c, pct in enumerate(s.att, start=6):
            held = random.choice([38, 40, 42])
            ws.cell(row=r, column=c, value=f"{int(round(pct / 100 * held))}/{held}")
    wb.save(os.path.join(OUT, "01_attendance_register.xlsx"))

    with open(os.path.join(OUT, "02_internal_marks.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["EnrollmentID", "NAME OF STUDENT", "IA-1 (30)", "IA-2 (30)",
                    "IA-3 (30)", "Assignment %"])
        for s in subset:
            roll = s.roll_no.lower()
            if random.random() < 0.15:
                roll = " " + roll + " "
            m = list(s.marks)
            if random.random() < 0.05:
                m[2] = ""
            w.writerow([roll, s.name, m[0], m[1], m[2], round(s.submission_pct)])

    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.title = "Fee & Backlog"
    ws2.append(["Enrl. No.", "Student", "Fee Status", "Backlogs", "Remarks", "Mobile No"])
    for c in range(1, 7):
        ws2.cell(row=1, column=c).font = Font(bold=True)
    for s in subset:
        r = s.roll_no
        status = random.choice([s.fee_status, s.fee_status.upper(), s.fee_status.lower()])
        # A real PII column, included on purpose. The ingest blocklist must
        # refuse it, and you want that refusal visible on stage.
        ws2.append([f"GU/{r[:2]}/{r[2:5]}/{r[5:]}", s.name, status, s.backlogs,
                    random.choice(["", "", "", "instalment approved", "contact office"]),
                    f"9{random.randint(100000000, 999999999)}"])
    wb2.save(os.path.join(OUT, "03_fees_and_backlogs.xlsx"))


# Each playbook is given a TRUE effect in attendance points, applied to the
# simulated data. This is ground truth we plant on purpose: the analysis in
# outcomes.py has to recover it without being told. If the matched estimate
# comes back near these numbers, the measurement works. If it does not, the
# measurement is broken and we would rather find that out here than on stage.
PLAYBOOKS = {
    "sudden_disengagement":   ("15-minute check-in call",            9.0),
    "chronic_low_attendance": ("Weekly attendance contract",         3.5),
    "assessment_decline":     ("Subject remedial slot",              6.0),
    "fee_pressure":           ("Scholarship / instalment referral", 12.0),
    "backlog_burden":         ("Backlog clearing plan + peer mentor", 4.5),
    "low_submission":         ("Assignment catch-up plan",           2.0),
}
EFFECT_DECAY = 0.90       # help fades, it does not vanish
REACH_RATE = 0.88         # some students are never actually reached


def plan_and_apply_interventions(students, week_starts, n_weeks, rng):
    """Decide who was helped, apply the real effect to their attendance, and
    return the intervention records.

    Two things here mirror reality and both matter:

    1. SELECTION BIAS. Mentors help students who are struggling, not a random
       sample. So selection is weighted toward low attendance in that week.
       This is exactly what makes a naive improvement rate misleading, because
       those students would partly recover anyway.

    2. A REAL EFFECT. Helped students genuinely improve, by a playbook-specific
       amount that decays over following weeks.

    Together these produce data where the naive number is inflated and only a
    matched comparison gets close to the truth.
    """
    from outcomes import BASELINE_WEEKS, FOLLOWUP_WEEKS

    mentors = ["Dr. S. Rao", "Prof. M. Iyer", "Dr. K. Fernandes", "Prof. A. Bose"]
    lo = BASELINE_WEEKS
    hi = n_weeks - FOLLOWUP_WEEKS - 2
    if hi <= lo:
        return []

    target = min(max(60, len(students) // 3), 900)
    rows, used = [], set()
    triggers = list(PLAYBOOKS.keys())
    attempts = 0

    while len(rows) < target and attempts < target * 60:
        attempts += 1
        w = rng.randint(lo, hi)
        s = students[rng.randrange(len(students))]
        if s.roll_no in used:
            continue
        level = float(np.mean(s.att[max(0, w - BASELINE_WEEKS + 1):w + 1]))
        # Weighted toward students who are struggling in that week.
        if rng.random() > min(0.97, max(0.02, (95.0 - level) / 60.0)):
            continue
        used.add(s.roll_no)

        trigger = rng.choice(triggers)
        playbook, effect = PLAYBOOKS[trigger]
        reached = rng.random() < REACH_RATE
        if reached:
            for k in range(w + 1, n_weeks):
                s.att[k] = float(np.clip(
                    s.att[k] + effect * (EFFECT_DECAY ** (k - w - 1))
                    + np.random.normal(0, 1.2), 2, 100))

        created = week_starts[w]
        followup = created + timedelta(days=21)
        rows.append({
            "id": f"IV{len(rows) + 1:05d}", "roll_no": s.roll_no,
            "mentor": rng.choice(mentors), "trigger": trigger,
            "playbook": playbook, "approved_by_mentor": 1,
            "created_at": created.isoformat(), "followup_at": followup.isoformat(),
            "status": "closed" if followup <= week_starts[-1] else "open",
            # What the mentor ticked. Optimistic, and deliberately not the same
            # thing as what the attendance data will show.
            "outcome": (("improved" if rng.random() < 0.55 + (0.02 * effect)
                         else rng.choice(["unchanged", "unchanged", "worsened"]))
                        if (followup <= week_starts[-1] and reached)
                        else ("unreachable" if not reached else "")),
        })

    with open(os.path.join(OUT, "interventions_seed.csv"), "w", newline="",
              encoding="utf-8") as f:
        w_ = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w_.writeheader()
        w_.writerows(rows)

    # Ground truth, written out so the analysis can be checked against it.
    import json
    with open(os.path.join(OUT, "true_effects.json"), "w", encoding="utf-8") as f:
        json.dump({"effects_in_attendance_points":
                   {v[0]: v[1] for v in PLAYBOOKS.values()},
                   "reach_rate": REACH_RATE, "decay_per_week": EFFECT_DECAY,
                   "note": "Planted on purpose. outcomes.matched_effect() must "
                           "recover these without being told."}, f, indent=2)
    return rows


def main():
    ap = argparse.ArgumentParser(description="Generate a synthetic student cohort.")
    ap.add_argument("--students", type=int, default=480, help="any size; tested to 50000")
    ap.add_argument("--weeks", type=int, default=26)
    ap.add_argument("--seed", type=int, default=25102)
    ap.add_argument("--messy-sample", type=int, default=600)
    ap.add_argument("--per-section", type=int, default=PER_SECTION,
                    help="students per section; lower it for small demo cohorts "
                         "so more than one section exists")
    a = ap.parse_args()

    random.seed(a.seed)
    np.random.seed(a.seed)
    rng = random.Random(a.seed)
    os.makedirs(OUT, exist_ok=True)

    week_starts = [date(2025, 7, 7) + timedelta(weeks=w) for w in range(a.weeks)]
    students, depts, anomaly, anomaly_week = build_cohort(a.students, a.weeks, rng,
                                                          a.per_section)
    rolls = [s.roll_no for s in students]
    assert len(rolls) == len(set(rolls)), "roll numbers must be unique"

    # Order matters: applying intervention effects modifies attendance, so this
    # has to happen before the attendance files are written.
    ivs = plan_and_apply_interventions(students, week_starts, a.weeks, rng)
    write_master(students, a.weeks, week_starts)
    write_messy_files(students, week_starts, min(a.messy_sample, len(students)))

    finals = np.array([np.mean(s.att[-4:]) for s in students])
    print(f"Generated {len(students)} students, {len(depts)} departments, {a.weeks} weeks -> {OUT}")
    print(f"  final 4-week attendance: mean {finals.mean():.1f}%  "
          f"below 50%: {(finals < 50).mean() * 100:.1f}%  below 75%: {(finals < 75).mean() * 100:.1f}%")
    print(f"  cohort anomaly: {anomaly[0]}-{anomaly[1]}{anomaly[2]} from week {anomaly_week}")
    print(f"  spreadsheets hold {min(a.messy_sample, len(students))} rows; "
          f"full cohort in master.csv")
    print(f"  {len(ivs)} interventions planted, with known effects in "
          f"demo_data/true_effects.json")


if __name__ == "__main__":
    main()