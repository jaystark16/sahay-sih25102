"""
Vercel serverless entry point.

Vercel's Python runtime looks for a module-level `app` and, when that is an
ASGI application, serves it directly. Everything real lives in main.py; this
file only makes it importable from api/ and fixes sys.path, because the
function's working directory is the repository root but api/ is not on the
import path by default.

Why the model libraries are absent here: scikit-learn, xgboost, shap and scipy
total ~220 MB against a 250 MB unzipped cap for a Python function. They are in
requirements-ml.txt, which is not installed on the server. service.py imports
`ml` inside a try/except and falls back to the predictions stored in
risk_snapshots.model_pct, so the deployed API still serves real model output --
it just does not recompute it. Run `python service.py` locally after training
to refresh those stored values.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app          # noqa: E402  (path setup must come first)

__all__ = ["app"]
