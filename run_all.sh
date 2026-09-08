#!/usr/bin/env bash
# Sahay - build everything from scratch and check it.
#
#   bash run_all.sh          # 5,000 students
#   bash run_all.sh 20000    # any size
#
# Needs SUPABASE_DATABASE_URL set (see .env.example). Step 3 rebuilds the
# database this points at, dropping every table first -- so do not run it
# against anything you want to keep.
set -euo pipefail
cd "$(dirname "$0")"
N="${1:-5000}"

# `python3` is not on PATH on Windows, where the launcher is `python`.
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python

echo "1/6  Generating $N students"
rm -f model.joblib model_report.json
"$PY" generate_demo_data.py --students "$N" --messy-sample 600

echo; echo "2/6  Training the model"
"$PY" ml.py

echo; echo "3/6  Building the database"
"$PY" service.py > /dev/null && echo "     done"

echo; echo "4/6  Checking everything"
"$PY" check.py

echo; echo "5/6  The six claims, for the demo"
"$PY" verify.py | grep -E "CLAIM|PASS|mean absolute|ALL CHECKS"

echo; echo "6/6  The JSON and auth contract"
"$PY" check_json.py | tail -n 20

echo
echo "Ready. Set a password and start it:"
echo "  $PY auth.py list"
echo "  $PY auth.py reset <email> <password>"
echo "  $PY -m uvicorn main:app --reload"
