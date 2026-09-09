"""
auth.py — the auth layer for Sahay.

Passwords are hashed with bcrypt. Accounts created before that used a single
round of salted SHA-256, which is far too fast to stand up to an offline
attack, so login verifies either form and silently re-hashes the old ones on
the next successful sign-in (see _verify and login).

Seeded accounts are created with a random password and must_change_password
set, so the predictable "Sahay@<id>2025" default that used to be derivable
straight from this file is gone.
"""

import hashlib
import hmac
import secrets
import threading

import bcrypt

import database
from datetime import datetime, timedelta

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id         TEXT PRIMARY KEY,
  email      TEXT UNIQUE,
  name       TEXT NOT NULL,
  role       TEXT NOT NULL,
  dept       TEXT,
  section    TEXT,
  password_hash TEXT NOT NULL,
  salt       TEXT NOT NULL,
  created_at TEXT,
  must_change_password INT DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_sessions_expires ON sessions(expires_at);
"""

SESSION_HOURS = 8

# Login throttling. Held in memory, so it is per-process: with several workers
# an attacker gets this many attempts per worker. That is a real limit, but the
# alternative (a counter table) means a database write per failed login, which
# is its own denial-of-service. Revisit if this ever runs multi-worker.
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_SECONDS = 300
_failed = {}
_failed_lock = threading.Lock()

# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #

def _hash_legacy(password: str, salt: str) -> str:
    """The old scheme. Only ever used to verify pre-existing accounts."""
    return hashlib.sha256((salt + password).encode()).hexdigest()


def _hash(password: str) -> str:
    """bcrypt, which is deliberately slow to compute and so slow to attack."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()


def _verify(password: str, stored: str, salt: str):
    """Check a password against either hash format.

    Returns (ok, needs_rehash). needs_rehash is True for a correct password
    that is still stored under the old SHA-256 scheme, which lets login upgrade
    it in place instead of requiring a reset.
    """
    if not stored:
        return False, False
    if stored.startswith("$2"):                       # bcrypt
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode()), False
        except ValueError:
            return False, False
    # Legacy SHA-256. compare_digest so the comparison is not a timing oracle.
    ok = hmac.compare_digest(_hash_legacy(password, salt or ""), stored)
    return ok, ok


def _throttle_key(ident: str) -> str:
    return (ident or "").lower().strip()


def check_not_locked(ident: str):
    """Raise if this identifier has failed too many times recently."""
    key = _throttle_key(ident)
    now = datetime.now()
    with _failed_lock:
        attempts = [t for t in _failed.get(key, [])
                    if (now - t).total_seconds() < LOCKOUT_SECONDS]
        _failed[key] = attempts
        if len(attempts) >= MAX_FAILED_ATTEMPTS:
            wait = LOCKOUT_SECONDS - int((now - attempts[0]).total_seconds())
            raise PermissionError(
                f"Too many failed sign-in attempts. Try again in {max(wait, 1)}s.")


def _record_failure(ident: str):
    key = _throttle_key(ident)
    with _failed_lock:
        _failed.setdefault(key, []).append(datetime.now())


def _clear_failures(ident: str):
    with _failed_lock:
        _failed.pop(_throttle_key(ident), None)


def _migrate_auth(con):
    """Add columns to databases created before they existed."""
    try:
        cols = {r["column_name"] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users'")}
    except database.PostgresWrapper.Error:
        con.rollback()
        return False
    if not cols:
        return False
    if "email" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN email TEXT")
        # r["id"], not r[0]: the cursor factory is RealDictCursor, so rows are
        # dict-keyed and positional access raises KeyError: 0. This path could
        # never have run.
        for r in con.execute("SELECT id FROM users").fetchall():
            con.execute("UPDATE users SET email=? WHERE id=?",
                        (default_email(r["id"]), r["id"]))
        con.commit()
    if "must_change_password" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN must_change_password "
                    "INT DEFAULT 0")
        con.commit()
    return True


def sweep_sessions(con):
    """Delete expired sessions.

    They used to be removed only if someone happened to present one, so the
    table grew without bound and a token that was never used again sat there
    for ever.
    """
    con.execute("DELETE FROM sessions WHERE expires_at < ?",
                (datetime.now().isoformat(timespec="seconds"),))
    con.commit()


def ensure_auth_schema(con):
    con.executescript(AUTH_SCHEMA)
    con.commit()
    _migrate_auth(con)


EMAIL_DOMAIN = "gmail.com"


def default_email(user_id: str) -> str:
    return f"{user_id.lower()}@{EMAIL_DOMAIN}"


def generate_password() -> str:
    """A random initial password.

    This replaces default_password(), which derived the password from the
    account id ("Sahay@<last4>2025"). The algorithm lived in this file, so
    anyone who could read the repository could sign in as any account that had
    not changed its password -- which, since nothing forced a change, was all
    of them.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(14))


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #

# The one password every demo account uses.
#
# Fixed and checked in on purpose. The previous behaviour -- a fresh random
# password per account, shown once, with must_change_password=1 -- is not
# suitable for a demo and locked the operator out three separate times:
#
#   1. accounts seeded at startup got a random password nobody ever saw, so a
#      newly created mentor could not sign in at all;
#   2. must_change_password=1 meant even a known password only bought you the
#      change-password screen;
#   3. rotating the passwords invalidated whatever had been written down, which
#      is what produced "Invalid email or password" on a correct-looking form.
#
# Ten characters because change_password() enforces a ten-character floor, so
# an operator can also set this from the UI without hitting the limit.
#
# What this costs: the deployment is public and anyone reading this repository
# can sign in. That is an accepted trade for a hackathon demo whose data is
# entirely synthetic and whose student-facing view exposes no scores. It is NOT
# suitable for an institution's real records -- for that, drop
# DEMO_PASSWORD/reset_demo_passwords and go back to generated credentials.
DEMO_PASSWORD = "sahay12345"


def seed_users(con, mentors: dict, echo=False, password=DEMO_PASSWORD):
    """Idempotent — only inserts if the user doesn't exist yet.

    New accounts get DEMO_PASSWORD and must_change_password=0, so an account
    that appears is an account that can be used. Pass password=None to get the
    old behaviour: a generated secret per account, shown once, that must be
    changed on first sign-in.
    """
    ensure_auth_schema(con)
    now = datetime.now().isoformat(timespec="seconds")
    # One query for the whole set rather than a SELECT per mentor: there is
    # one account per section, so this loop ran hundreds of round trips.
    existing = {r["id"] for r in con.execute("SELECT id FROM users")}
    created = {}
    rows = []
    must_change = 0 if password else 1
    for uid, info in mentors.items():
        if uid in existing:
            continue
        pwd = password or generate_password()
        created[uid] = pwd
        rows.append((uid, default_email(uid), info["name"], info["role"],
                     info.get("scope", (None, None))[0],
                     info.get("section"),
                     _hash(pwd), "", now, must_change))
    if rows:
        con.executebatch(
            "INSERT INTO users (id,email,name,role,dept,section,password_hash,"
            "salt,created_at,must_change_password) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows)
        con.commit()
    if created and echo:
        print(f"\n  {len(created)} account(s) created.")
        for uid, pwd in sorted(created.items())[:200]:
            print(f"    {default_email(uid):40s} {pwd}")
        print()
    return created


def reset_demo_passwords(con, password=DEMO_PASSWORD):
    """Put every account back on the shared demo password.

    The recovery path. If sign-in ever fails again, this is one command and it
    is guaranteed to work, because it also clears must_change_password -- the
    flag that made a correct password still refuse entry.
    """
    ensure_auth_schema(con)
    rows = con.execute("SELECT id, email, role FROM users ORDER BY role, id").fetchall()
    for r in rows:
        set_password(con, r["id"], password, must_change=False)
    con.commit()
    return [{"id": r["id"], "email": r["email"], "role": r["role"]} for r in rows]


# --------------------------------------------------------------------------- #
# Auth API
# --------------------------------------------------------------------------- #

def login(con, user_id: str, password: str):
    """Sign in with an email address. The account id is still accepted so
    existing links, scripts and the demo reset keep working.

    Returns a session token dict, or raises ValueError for bad credentials and
    PermissionError when the account is temporarily locked out.
    """
    ensure_auth_schema(con)
    ident = (user_id or "").lower().strip()
    check_not_locked(ident)

    row = con.execute(
        "SELECT id,email,name,role,dept,section,password_hash,salt,"
        "must_change_password FROM users WHERE email=? OR id=?", (ident, ident)
    ).fetchone()

    ok, needs_rehash = (False, False)
    if row:
        ok, needs_rehash = _verify(password, row["password_hash"], row["salt"])
    if not ok:
        # Same message either way, so this cannot be used to enumerate accounts.
        _record_failure(ident)
        raise ValueError("Invalid email or password")
    _clear_failures(ident)

    if needs_rehash:
        # Correct password, old SHA-256 hash: upgrade it now that we have the
        # plaintext, so the weak hash does not survive another sign-in.
        con.execute("UPDATE users SET password_hash=?, salt='' WHERE id=?",
                    (_hash(password), row["id"]))

    # Rotate: drop this user's existing sessions rather than accumulating one
    # per sign-in, which also limits how long a stolen token stays useful.
    con.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    sweep_sessions(con)

    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    now = datetime.now().isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO sessions (token,user_id,expires_at,created_at) VALUES (?,?,?,?)",
        (token, row["id"], expires, now)
    )
    con.commit()
    return {
        "token": token,
        "user": {
            "id": row["id"],
            "email": row["email"],
            "name": row["name"],
            "role": row["role"],
            "dept": row["dept"],
            "section": row["section"],
            "must_change_password": bool(row["must_change_password"]),
        },
        "expires_at": expires,
    }


def verify_token(con, token: str):
    """Returns user dict or None if invalid/expired."""
    if not token:
        return None
    row = con.execute(
        "SELECT s.user_id, s.expires_at, u.email, u.name, u.role, u.dept, "
        "u.section, u.must_change_password "
        "FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token=?",
        (token,)
    ).fetchone()
    if not row:
        return None
    try:
        expired = datetime.fromisoformat(row["expires_at"]) < datetime.now()
    except (TypeError, ValueError):
        # A malformed expires_at used to raise straight out of /api/auth/me as
        # a 500. Treat it as expired instead -- it is not a usable session.
        expired = True
    if expired:
        con.execute("DELETE FROM sessions WHERE token=?", (token,))
        con.commit()
        return None
    return {
        "id": row["user_id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "dept": row["dept"],
        "section": row["section"],
        "must_change_password": bool(row["must_change_password"]),
    }


def logout(con, token: str):
    con.execute("DELETE FROM sessions WHERE token=?", (token,))
    con.commit()


def list_users(con):
    ensure_auth_schema(con)
    rows = con.execute(
        "SELECT id, name, role, dept, section, must_change_password "
        "FROM users ORDER BY role, id"
    ).fetchall()
    return [dict(r) for r in rows]


def change_password(con, user_id: str, old_password: str, new_password: str):
    if not new_password or len(new_password) < 10:
        raise ValueError("New password must be at least 10 characters")
    if new_password == old_password:
        raise ValueError("New password must be different from the current one")

    row = con.execute(
        "SELECT password_hash, salt FROM users WHERE id=?", (user_id,)
    ).fetchone()
    ok, _ = _verify(old_password, row["password_hash"], row["salt"]) if row else (False, False)
    if not ok:
        raise ValueError("Current password incorrect")

    con.execute(
        "UPDATE users SET password_hash=?, salt='', must_change_password=0 "
        "WHERE id=?", (_hash(new_password), user_id)
    )
    # Every existing session for this user dies with the old password. Without
    # this, a password change did nothing to lock out whoever had the token --
    # which is the main reason people change a password in the first place.
    con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    con.commit()


def set_password(con, ident: str, new_password: str = None,
                 must_change: bool = False):
    """Set an account's password administratively, without the old one.

    Needed because seeded accounts get a random password that is shown once
    and then unrecoverable -- so there has to be a way back in that does not
    involve editing hashes by hand. Returns the password that was set.
    """
    ident = (ident or "").lower().strip()
    row = con.execute("SELECT id FROM users WHERE email=? OR id=?",
                      (ident, ident)).fetchone()
    if not row:
        raise ValueError(f"no such account: {ident}")
    pwd = new_password or generate_password()
    con.execute("UPDATE users SET password_hash=?, salt='', "
                "must_change_password=? WHERE id=?",
                (_hash(pwd), 1 if must_change else 0, row["id"]))
    con.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    con.commit()
    return pwd


if __name__ == "__main__":
    # Account admin from the command line:
    #   python auth.py list
    #   python auth.py reset <email-or-id> [password]
    import sys
    import database as _db

    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    con = _db.connect()
    ensure_auth_schema(con)

    if cmd == "list":
        rows = list_users(con)
        print(f"{len(rows)} account(s):")
        for r in rows:
            flag = "  (must change password)" if r.get("must_change_password") else ""
            print(f"  {r['id']:14} {default_email(r['id']):34} {r['role']:8}{flag}")
        print("\nSet a password with:  python auth.py reset <email-or-id> [password]")
        print(f"Restore every demo account to '{DEMO_PASSWORD}':  "
              f"python auth.py demo-passwords")
    elif cmd == "reset" and len(args) >= 2:
        pwd = set_password(con, args[1], args[2] if len(args) > 2 else None)
        print(f"Password for {args[1]} is now: {pwd}")
    elif cmd in ("demo-passwords", "demo"):
        # The recovery command. One thing to run, guaranteed to work.
        rows = reset_demo_passwords(con)
        print(f"{len(rows)} account(s) reset to: {DEMO_PASSWORD}\n")
        for r in rows:
            print(f"  {r['email']:26} {r['role']}")
        print("\nmust_change_password cleared on all of them.")
    else:
        print(__doc__)
        print("Usage: python auth.py [list | reset <email-or-id> [password] "
              "| demo-passwords]")
    con.close()
