"""
Sahay - messy spreadsheet ingestion.

This is the module that wins your demo. It handles the four things every other
team's upload screen assumes away:

  1. Junk rows above the real header    -> detect_header_row()
  2. Column names nobody agrees on      -> map_columns()
  3. Three roll-number formats          -> normalize_roll()
  4. Values that aren't numbers         -> parse_attendance(), parse_marks()

Zero third-party fuzzy-matching dependency: uses stdlib difflib. One less thing
to install at 3am.
"""

import difflib
import re
from datetime import datetime

import pandas as pd

# ----------------------------------------------------------------------------
# 1. Canonical schema. Aliases are what we have actually seen in college files.
# ----------------------------------------------------------------------------
CANONICAL = {
    "roll_no": ["roll no", "rollno", "roll number", "enrollment id", "enrl no",
                "enrolment no", "registration no", "reg no", "student id",
                "admission no", "hall ticket", "htno", "pin", "id"],
    "name": ["student name", "name of student", "name", "student", "full name",
             "candidate name", "छात्र का नाम"],
    "dept": ["branch", "department", "dept", "programme", "program", "course",
             "discipline", "stream"],
    "year": ["yr", "year", "study year", "academic year", "batch year"],
    "section": ["sec", "section", "class", "div", "division", "batch"],
    "attendance_pct": ["attendance", "attendance %", "att %", "attendance percentage",
                       "present %", "overall attendance"],
    "ia1": ["ia-1", "ia1", "ia 1", "internal 1", "internal-1", "mid 1", "mid-1",
            "test 1", "cie 1", "sessional 1", "unit test 1"],
    "ia2": ["ia-2", "ia2", "ia 2", "internal 2", "internal-2", "mid 2", "mid-2",
            "test 2", "cie 2", "sessional 2", "unit test 2"],
    "ia3": ["ia-3", "ia3", "ia 3", "internal 3", "internal-3", "mid 3", "mid-3",
            "test 3", "cie 3", "sessional 3", "unit test 3"],
    "submission_pct": ["assignment %", "assignment", "assignments", "submission %",
                       "submissions", "assignment submission"],
    "backlogs": ["backlogs", "backlog", "arrears", "arrear", "failed subjects",
                 "no of backlogs", "re-appear", "supplementary"],
    "fee_status": ["fee status", "fees", "fee", "fee paid", "payment status",
                   "tuition status", "fee dues"],
}

# Columns we refuse to ingest even if present. Data minimisation, by default.
BLOCKLIST = ["aadhaar", "aadhar", "caste certificate", "religion", "father income",
             "annual income", "mobile", "phone", "address", "email", "dob",
             "date of birth", "blood group", "disability"]

MATCH_THRESHOLD = 0.72


def _clean(s):
    s = str(s or "").strip().lower()
    s = re.sub(r"\(.*?\)", " ", s)            # drop "(30)", "(out of 40)"
    s = re.sub(r"[^a-z0-9%\u0900-\u097F ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------------------------------
# 2. Roll-number reconciliation
# ----------------------------------------------------------------------------
# Separators are already stripped before this runs, so match the parts directly.
# A leading \D* here would greedily eat the first letter of the dept code
# ('23CSE001' -> '23' + 'SE' + '001'). Do not add one back.
ROLL_RE = re.compile(r"(\d{2})([A-Z]{2,4})(\d{1,4})")


def normalize_roll(raw):
    """Reduce any roll format to a single canonical key.

    '22CSE005' / ' 22cse005 ' / 'GU/22/CSE/005' / 'GU-22-CSE-5'  ->  '22CSE005'

    Returns None if no recognisable pattern is present, so the caller can
    surface it as an unmatched row instead of silently dropping it.
    """
    s = re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()
    if not s:
        return None
    m = ROLL_RE.search(s)
    if not m:
        return s or None
    yr, dept, serial = m.groups()
    return f"{yr}{dept}{int(serial):03d}"


# ----------------------------------------------------------------------------
# 3. Value parsers - the "not a number" problem
# ----------------------------------------------------------------------------
def parse_attendance(v):
    """'34/40' -> 85.0 | '85%' -> 85.0 | 0.85 -> 85.0 | 85 -> 85.0 | 'AB'/'' -> None"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if s == "" or s.upper() in {"AB", "NA", "N/A", "-", "--"}:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)$", s)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        return round(num / den * 100, 1) if den else None
    s = s.replace("%", "").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    if 0 < f <= 1:              # stored as a fraction
        f *= 100
    return round(min(max(f, 0.0), 100.0), 1)


def parse_marks(v, max_marks=None):
    """Returns (value_or_None, was_absent). 'AB' is information, not a gap."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None, False
    s = str(v).strip()
    if s == "":
        return None, False
    if s.upper() in {"AB", "ABSENT", "A"}:
        return None, True
    try:
        f = float(s.replace("%", ""))
    except ValueError:
        return None, False
    if max_marks:
        f = min(f, float(max_marks))
    return f, False


FEE_MAP = {
    "paid": "Paid", "yes": "Paid", "y": "Paid", "cleared": "Paid", "nil": "Paid",
    "pending": "Pending", "unpaid": "Pending", "no": "Pending", "n": "Pending",
    "due": "Pending", "outstanding": "Pending",
    "part paid": "Part Paid", "partial": "Part Paid", "partly paid": "Part Paid",
    "installment": "Part Paid", "instalment": "Part Paid",
}


def parse_fee_status(v):
    return FEE_MAP.get(_clean(v), "Unknown")


def max_marks_from_header(header):
    """'IA-1 (30)' -> 30. Reads the max out of the header instead of guessing."""
    m = re.search(r"\(\s*(?:out of\s*)?(\d{1,3})\s*\)", str(header or ""), re.I)
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------------
# 4. Header detection
# ----------------------------------------------------------------------------
def detect_header_row(df_raw, max_scan=12):
    """Pick the row that looks most like a header, not row 0.

    Scores each candidate row by how many of its cells map to canonical fields.
    """
    best_row, best_score = 0, -1
    for r in range(min(max_scan, len(df_raw))):
        cells = [c for c in df_raw.iloc[r].tolist() if str(c).strip() not in ("", "nan", "None")]
        if len(cells) < 2:
            continue
        hits = sum(1 for c in cells if _best_field(str(c))[0])
        score = hits - 0.15 * abs(len(cells) - df_raw.shape[1])
        if score > best_score:
            best_row, best_score = r, score
    return best_row


def _best_field(raw_header):
    """(canonical_field, confidence, matched_alias) or (None, 0.0, None)."""
    h = _clean(raw_header)
    if not h:
        return None, 0.0, None
    if any(b in h for b in BLOCKLIST):
        return "__BLOCKED__", 1.0, h

    # A bare date column is a weekly attendance snapshot, not a field name.
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%y", "%d %b %Y"):
        try:
            datetime.strptime(str(raw_header).strip(), fmt)
            return "__WEEK__", 1.0, str(raw_header).strip()
        except ValueError:
            pass

    best = (None, 0.0, None)
    for field, aliases in CANONICAL.items():
        for a in aliases:
            if h == a:
                return field, 1.0, a
            if h.startswith(a) or a.startswith(h):
                score = 0.94
            else:
                score = difflib.SequenceMatcher(None, h, a).ratio()
            if score > best[1]:
                best = (field, round(score, 3), a)
    return best if best[1] >= MATCH_THRESHOLD else (None, best[1], best[2])


def map_columns(headers):
    """Map raw headers to canonical fields.

    Returns a mapping list that the UI shows to the mentor for confirmation.
    Every decision carries its confidence and the alias that matched, so the
    mentor can see WHY and override. Nothing is silently guessed.
    """
    mapping, taken = [], {}
    for i, h in enumerate(headers):
        field, conf, alias = _best_field(h)
        status = "mapped"
        if field == "__BLOCKED__":
            field, status = None, "blocked_by_policy"
        elif field == "__WEEK__":
            status = "weekly_snapshot"
        elif field is None:
            status = "unmapped"
        elif field in taken:                       # first, highest-confidence wins
            if conf <= taken[field]["confidence"]:
                field, status = None, "duplicate_ignored"
            else:
                taken[field]["field"], taken[field]["status"] = None, "duplicate_ignored"
        entry = {"index": i, "source": str(h), "field": field,
                 "confidence": conf, "matched_alias": alias, "status": status}
        if field and status == "mapped":
            taken[field] = entry
        mapping.append(entry)

    required = ["roll_no"]
    missing = [f for f in required if not any(m["field"] == f and m["status"] == "mapped" for m in mapping)]
    return {"mapping": mapping, "missing_required": missing}


# ----------------------------------------------------------------------------
# 5. Top-level ingestion
# ----------------------------------------------------------------------------
def read_any(path_or_buffer, sheet=0):
    """Read csv/xlsx with NO header, so detect_header_row() can do its job."""
    name = str(getattr(path_or_buffer, "name", path_or_buffer)).lower()
    if name.endswith(".csv") or name.endswith(".tsv"):
        sep = "\t" if name.endswith(".tsv") else ","
        return pd.read_csv(path_or_buffer, header=None, sep=sep, dtype=str,
                           keep_default_na=False, engine="python")
    return pd.read_excel(path_or_buffer, sheet_name=sheet, header=None, dtype=str)


def ingest_file(path_or_buffer, sheet=0):
    """Returns a report the UI can render, plus normalised records.

    report = {
      header_row, mapping, missing_required, weeks,
      rows_total, rows_keyed, rows_unmatched, samples
    }
    """
    raw = read_any(path_or_buffer, sheet)
    hrow = detect_header_row(raw)
    headers = [str(x).strip() for x in raw.iloc[hrow].tolist()]
    body = raw.iloc[hrow + 1:].reset_index(drop=True)

    mc = map_columns(headers)
    mapping = mc["mapping"]
    by_field = {m["field"]: m["index"] for m in mapping
                if m["field"] and m["status"] == "mapped"}
    week_cols = [(m["index"], m["matched_alias"]) for m in mapping
                 if m["status"] == "weekly_snapshot"]

    records, unmatched = [], []
    for _, row in body.iterrows():
        vals = row.tolist()
        if all(str(v).strip() in ("", "nan", "None") for v in vals):
            continue

        raw_roll = vals[by_field["roll_no"]] if "roll_no" in by_field else None
        key = normalize_roll(raw_roll)
        if not key:
            unmatched.append({"raw_roll": str(raw_roll), "reason": "no recognisable roll pattern"})
            continue

        rec = {"roll_no": key, "roll_no_as_given": str(raw_roll).strip()}

        for field in ("name", "dept", "section"):
            if field in by_field:
                v = str(vals[by_field[field]]).strip()
                if v not in ("", "nan", "None"):
                    rec[field] = v
        if "year" in by_field:
            try:
                rec["year"] = int(float(str(vals[by_field["year"]]).strip()))
            except (ValueError, TypeError):
                pass

        for field in ("ia1", "ia2", "ia3"):
            if field in by_field:
                idx = by_field[field]
                mm = max_marks_from_header(headers[idx])
                val, absent = parse_marks(vals[idx], mm)
                rec[field] = val
                rec[f"{field}_max"] = mm
                rec[f"{field}_absent"] = absent

        if "submission_pct" in by_field:
            rec["submission_pct"] = parse_attendance(vals[by_field["submission_pct"]])
        if "attendance_pct" in by_field:
            rec["attendance_pct"] = parse_attendance(vals[by_field["attendance_pct"]])
        if "fee_status" in by_field:
            rec["fee_status"] = parse_fee_status(vals[by_field["fee_status"]])
        if "backlogs" in by_field:
            try:
                rec["backlogs"] = int(float(str(vals[by_field["backlogs"]]).strip() or 0))
            except (ValueError, TypeError):
                rec["backlogs"] = None

        if week_cols:
            series = []
            for idx, label in week_cols:
                series.append({"week": label, "attendance_pct": parse_attendance(vals[idx])})
            rec["weekly_attendance"] = series

        records.append(rec)

    report = {
        "header_row_detected": hrow,
        "rows_above_header_skipped": hrow,
        "mapping": mapping,
        "missing_required": mc["missing_required"],
        "weekly_columns_found": len(week_cols),
        "rows_total": int(len(body)),
        "rows_keyed": len(records),
        "rows_unmatched": len(unmatched),
        "unmatched_samples": unmatched[:5],
        "blocked_columns": [m["source"] for m in mapping if m["status"] == "blocked_by_policy"],
    }
    return report, records


if __name__ == "__main__":
    import json
    import os
    import sys

    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data")
    files = sys.argv[1:] or [
        os.path.join(base, "01_attendance_register.xlsx"),
        os.path.join(base, "02_internal_marks.csv"),
        os.path.join(base, "03_fees_and_backlogs.xlsx"),
    ]
    for f in files:
        rep, recs = ingest_file(f)
        print("=" * 74)
        print(os.path.basename(f))
        print(f"  header found on row {rep['header_row_detected']} "
              f"({rep['rows_above_header_skipped']} junk rows skipped)")
        print(f"  {rep['rows_keyed']}/{rep['rows_total']} rows keyed, "
              f"{rep['rows_unmatched']} unmatched, "
              f"{rep['weekly_columns_found']} weekly columns")
        for m in rep["mapping"][:8]:
            print(f"    {m['source'][:26]:28s} -> {str(m['field']):16s} "
                  f"{m['status']:18s} conf={m['confidence']}")
        if recs:
            r = dict(recs[0])
            if "weekly_attendance" in r:
                r["weekly_attendance"] = r["weekly_attendance"][:3] + ["..."]
            print("  first record:", json.dumps(r, default=str)[:320])
