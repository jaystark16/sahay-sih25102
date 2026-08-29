#!/usr/bin/env bash
# Sahay - build everything from scratch and check it.
#
#   bash run_all.sh          # 5,000 students
#   bash run_all.sh 20000    # any size
set -euo pipefail
cd "$(dirname "$0")"
N="${1:-5000}"

echo "1/5  Generating $N students"
rm -f sahay.db model.joblib model_report.json
python3 generate_demo_data.py --students "$N" --messy-sample 600

echo; echo "2/5  Training the model"
python3 ml.py

echo; echo "3/5  Building the database"
python3 service.py > /dev/null && echo "     done"

echo; echo "4/5  Checking everything"
python3 check.py

echo; echo "5/5  The six claims, for the demo"
python3 verify.py | grep -E "CLAIM|PASS|mean absolute|ALL CHECKS"

echo
echo "Ready. Start it with:  uvicorn main:app --reload"
