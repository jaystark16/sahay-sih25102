"""
Sahay - JSON safety.

One place for the "can this actually be serialised" question, used both when
writing JSON into the database and when writing it out to a client.

The problem this solves is narrow but was doing real damage. Python's
json.dumps emits bare NaN and Infinity tokens by default, and json.loads reads
them back without complaint, so a non-finite number could round-trip through a
TEXT column and surface much later. Starlette, meanwhile, renders every response
with allow_nan=False:

    json.dumps(content, ensure_ascii=False, allow_nan=False, ...)

so the same value at the HTTP boundary is a ValueError, i.e. an opaque HTTP 500
on an endpoint that worked yesterday. Since some of those values had been
committed to the database, the 500 was permanent.

The fix is to decide once, here, that non-finite means "no data" -- the same
thing the rest of the codebase means by None -- and to convert rather than
raise, so one bad cell degrades a single field instead of taking down a
whole-cohort recompute.

Stdlib only, so it stays importable from anywhere.
"""

import json
import math
from datetime import date, datetime
from decimal import Decimal

__all__ = ["sanitize", "dumps"]


def sanitize(obj):
    """Recursively replace anything json.dumps(allow_nan=False) would reject.

    - NaN, Infinity, -Infinity  -> None
    - Decimal                   -> float (or None if the Decimal is NaN/Inf)
    - datetime / date           -> ISO 8601 string
    - set / tuple               -> list
    - dict keys                 -> str, since JSON object keys must be strings
    - anything with .item()     -> its Python scalar (numpy float32/int64/bool_)

    Everything else is returned unchanged; this is a normaliser, not a
    validator, and it is deliberately cheap enough to run on every response.
    """
    # bool must be tested before int/float: it is a subclass of int.
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj

    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None

    if isinstance(obj, Decimal):
        f = float(obj)
        return f if math.isfinite(f) else None

    # pandas' missing-value sentinels, matched by name so this module keeps its
    # stdlib-only import list. Both need catching before the branches below:
    # pd.NA has no usable .item() and is not equal to itself in a way Python
    # can test, and pd.NaT subclasses datetime, so it would otherwise be
    # serialised as the string "NaT" rather than null.
    if type(obj).__name__ in ("NAType", "NaTType"):
        return None

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()

    if isinstance(obj, dict):
        return {(k if isinstance(k, str) else str(k)): sanitize(v)
                for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [sanitize(v) for v in obj]

    # numpy scalars and arrays. .tolist() rather than .item() because item()
    # raises on any array with more than one element, which would silently turn
    # a whole series into null; .tolist() handles the scalar and the array case
    # with the same call, returning plain Python either way.
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        try:
            return sanitize(tolist())
        except (ValueError, TypeError):
            return None

    if obj is not obj:          # last-ditch NaN check for exotic float types
        return None

    return obj


def dumps(obj, **kw):
    """json.dumps for anything we persist, with NaN made impossible.

    Sanitising first means allow_nan=False can never actually fire, which is
    the point: we want the value fixed at the boundary, not an exception
    thrown from inside a cohort-wide recompute.
    """
    kw.setdefault("allow_nan", False)
    return json.dumps(sanitize(obj), **kw)
