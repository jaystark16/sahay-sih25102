"""
auth.py — lightweight auth layer for Sahay.

Passwords are stored as salted SHA-256 hashes.  No external deps beyond stdlib.
Default credentials are seeded from STAFF + every section mentor with the
same simple pattern so a demo can be reset without re-entering credentials.
"""

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

AUTH_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sahay.db")

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
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #

def _hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode()).hexdigest()


def _make_salt() -> str:
    return secrets.token_hex(16)


def _migrate_auth(con):
    """Add the email column to databases created before it existed."""
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    except sqlite3.Error:
        return
    if cols and "email" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN email TEXT")
        for r in con.execute("SELECT id FROM users").fetchall():
            con.execute("UPDATE users SET email=? WHERE id=?",
                        (default_email(r[0]), r[0]))
        con.commit()


def ensure_auth_schema(con):
    con.executescript(AUTH_SCHEMA)
    con.commit()
    _migrate_auth(con)


EMAIL_DOMAIN = "gmail.com"


def default_email(user_id: str) -> str:
    return f"{user_id.lower()}@{EMAIL_DOMAIN}"


def default_password(user_id: str, role: str = "mentor") -> str:
    """Predictable demo password so the reset button doesn't break logins.
    Real deployments would force a change on first login."""
    uid = user_id.lower()
    if uid.isalpha():                       # a named account, e.g. "mentor"
        return f"Sahay@{uid.capitalize()}2025"
    return f"Sahay@{uid[-4:].upper()}2025"


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #

def seed_users(con, mentors: dict):
    """Idempotent — only inserts if the user doesn't exist yet."""
    ensure_auth_schema(con)
    now = datetime.now().isoformat(timespec="seconds")
    for uid, info in mentors.items():
        existing = con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
        if existing:
            continue
        salt = _make_salt()
        pwd = default_password(uid, info["role"])
        pw_hash = _hash(pwd, salt)
        con.execute(
            "INSERT INTO users (id,email,name,role,dept,section,password_hash,salt,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, default_email(uid), info["name"], info["role"],
             info.get("scope", (None, None))[0],
             info.get("section"),
             pw_hash, salt, now)
        )
    con.commit()


# --------------------------------------------------------------------------- #
# Auth API
# --------------------------------------------------------------------------- #

def login(con, user_id: str, password: str):
    """Sign in with an email address. The account id is still accepted so
    existing links, scripts and the demo reset keep working.

    Returns a session token dict or raises ValueError.
    """
    ensure_auth_schema(con)
    ident = (user_id or "").lower().strip()
    row = con.execute(
        "SELECT id,email,name,role,dept,section,password_hash,salt FROM users "
        "WHERE email=? OR id=?", (ident, ident)
    ).fetchone()
    if not row:
        raise ValueError("Invalid email or password")
    if _hash(password, row["salt"]) != row["password_hash"]:
        raise ValueError("Invalid email or password")

    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=8)).isoformat(timespec="seconds")
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
        },
        "expires_at": expires,
    }


def verify_token(con, token: str):
    """Returns user dict or None if invalid/expired."""
    if not token:
        return None
    ensure_auth_schema(con)
    row = con.execute(
        "SELECT s.user_id, s.expires_at, u.email, u.name, u.role, u.dept, u.section "
        "FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token=?",
        (token,)
    ).fetchone()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now():
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
    }


def logout(con, token: str):
    con.execute("DELETE FROM sessions WHERE token=?", (token,))
    con.commit()


def list_users(con):
    ensure_auth_schema(con)
    rows = con.execute(
        "SELECT id, name, role, dept, section FROM users ORDER BY role, id"
    ).fetchall()
    return [dict(r) for r in rows]


def change_password(con, user_id: str, old_password: str, new_password: str):
    row = con.execute(
        "SELECT password_hash, salt FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not row or _hash(old_password, row["salt"]) != row["password_hash"]:
        raise ValueError("Current password incorrect")
    salt = _make_salt()
    con.execute(
        "UPDATE users SET password_hash=?, salt=? WHERE id=?",
        (_hash(new_password, salt), salt, user_id)
    )
    con.commit()
