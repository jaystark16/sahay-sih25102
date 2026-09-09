"""
Sahay - database access.

A thin wrapper that lets the rest of the codebase keep writing sqlite3-style
calls (`con.execute("... WHERE roll_no=?", (roll,))`) against Postgres, plus a
connection pool.

Why the pool matters more than it looks:

  * FastAPI runs `def` (non-async) routes in a threadpool, so a single shared
    psycopg2 connection is used by several requests at once. Connections are
    not safe for that -- you get interleaved transactions and
    "another command is already in progress".
  * Postgres aborts the whole transaction on any statement error, and every
    later statement on that connection then fails with InFailedSqlTransaction.
    With one shared connection, a single bad query bricked the entire API until
    the process restarted.
  * A pooled connection can be closed by the server -- Supabase's pooler drops
    idle ones -- so a long-lived global eventually turns every route into
    "connection already closed". Checkout has to verify liveness.

So: one pool, a connection checked out per request, rolled back on error,
queued rather than refused when busy, and probed on checkout only if it has
been idle long enough to have been dropped.
"""

import os
import threading
import time

import psycopg2
from psycopg2 import extras
from psycopg2 import pool as _pgpool
from psycopg2.extras import RealDictCursor

from dotenv import load_dotenv

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5432/postgres"

# Keep this comfortably under the database's own connection limit. Supabase's
# transaction pooler allows plenty, but each one costs memory server-side and
# a web dyno does not need many: requests are short and the queue below
# smooths out bursts.
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "8"))

# How long a request will wait for a free connection before giving up. Long
# enough to ride out a burst, short enough that a client gets an answer rather
# than a hung socket.
CHECKOUT_TIMEOUT = float(os.environ.get("DB_CHECKOUT_TIMEOUT", "20"))

# A pooled connection idle for longer than this is probed with SELECT 1
# before use. Supabase's pooler drops idle connections, but probing every
# checkout would add a network round trip to every single request.
IDLE_PROBE_AFTER = float(os.environ.get("DB_IDLE_PROBE_AFTER", "20"))

_pool = None
_pool_lock = threading.Lock()

# psycopg2's ThreadedConnectionPool raises PoolError the moment it is empty --
# it has no queue. FastAPI runs sync routes in a threadpool of ~40 workers, so
# without this semaphore a burst of concurrent requests turns straight into
# HTTP 500s instead of waiting a few milliseconds for a connection to come
# back. The semaphore makes callers queue, and bounds how many ever ask.
_slots = threading.BoundedSemaphore(POOL_MAX)


def dsn():
    load_dotenv()
    return os.environ.get("SUPABASE_DATABASE_URL", DEFAULT_DSN)


class PostgresWrapper:
    """sqlite3-shaped facade over one psycopg2 connection."""

    Error = psycopg2.Error
    OperationalError = psycopg2.OperationalError

    def __init__(self, conn, pool=None, holds_slot=False):
        self._conn = conn
        self._pool = pool
        self._cursors = []
        # True only for pooled connections, which must hand their semaphore
        # slot back exactly once. Standalone connections never took one.
        self._holds_slot = holds_slot

    # -- statement execution --------------------------------------------------
    def _cursor(self):
        cur = self._conn.cursor()
        # psycopg2 cursors hold their result set client-side until closed, and
        # pooled connections live for hours. Track them so release() can free
        # them, which bounds the leak to a single request.
        self._cursors.append(cur)
        return cur

    def execute(self, query, args=None):
        # The rest of the codebase writes sqlite3 '?' placeholders. Note this
        # is a blind replace, so it would also rewrite a '?' inside a string
        # literal or a jsonb operator (? ?| ?&) -- no such query exists today,
        # and one must not be added without changing this.
        cur = self._cursor()
        sql = query.replace("?", "%s")
        if args is not None:
            cur.execute(sql, args)
        else:
            cur.execute(sql)
        return cur

    def executemany(self, query, args_list):
        """Many rows, one statement -- but still one round trip per row.

        psycopg2's executemany loops internally, so against a remote database
        this costs one network round trip per row. Prefer executebatch() for
        anything above a few dozen rows.
        """
        cur = self._cursor()
        cur.executemany(query.replace("?", "%s"), args_list)
        return cur

    def executebatch(self, query, args_list, page_size=500):
        """Bulk insert/update, pipelined into a few round trips.

        This is the difference between a seed that finishes in seconds and one
        that appears to hang: reset_db() inserts 5,000 students and 130,000
        attendance rows, and at even 50ms of latency per statement a
        row-at-a-time loop is well over an hour. execute_batch collapses each
        page into a single statement, so the same work is a few hundred round
        trips instead of 135,000.
        """
        if not args_list:
            return None
        cur = self._cursor()
        extras.execute_batch(cur, query.replace("?", "%s"), args_list,
                             page_size=page_size)
        return cur

    def executescript(self, query):
        """Multiple statements in one string.

        psycopg2 accepts semicolon-separated statements, so this works for
        SCHEMA and the reset DROPs. Unlike sqlite3's executescript there is no
        implicit commit, and deliberately no '?' rewrite -- the scripts carry
        no placeholders.
        """
        cur = self._cursor()
        cur.execute(query)
        return cur

    # -- transaction control --------------------------------------------------
    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    # -- lifecycle ------------------------------------------------------------
    def _free_cursors(self):
        for cur in self._cursors:
            try:
                cur.close()
            except Exception:
                pass
        self._cursors = []

    def alive(self):
        """Is this connection usable?

        `closed` is free but only catches connections we closed ourselves -- a
        connection dropped server-side still reports closed == 0 and only fails
        on next use. Confirming that needs a real round trip, which against a
        remote database costs as much as the query the request came to run.

        So only pay for it when it might have happened: a connection that has
        been sitting in the pool longer than IDLE_PROBE_AFTER gets probed, and
        a freshly-used one is taken at its word.
        """
        if self._conn.closed:
            return False
        last_used = getattr(self._conn, "_sahay_last_used", None)
        if last_used is None:
            return True          # straight from psycopg2.connect(): alive
        if time.monotonic() - last_used < IDLE_PROBE_AFTER:
            return True
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return True
        except psycopg2.Error:
            return False

    def _drop_slot(self):
        if self._holds_slot:
            self._holds_slot = False
            _slots.release()

    def release(self):
        """Return the connection to the pool, leaving it reusable.

        The rollback is the important part: without it a connection whose
        transaction failed goes back into the pool still aborted, and poisons
        whichever request picks it up next.
        """
        self._free_cursors()
        try:
            self._conn.rollback()
        except psycopg2.Error:
            pass
        # Records when this connection went idle, so alive() knows whether it
        # has been sitting long enough to be worth probing. Only assignable
        # because the pool builds _TrackedConnection rather than psycopg2's
        # own C-level connection type.
        try:
            self._conn._sahay_last_used = time.monotonic()
        except AttributeError:                                  # pragma: no cover
            pass
        try:
            if self._pool is not None and not self._conn.closed:
                try:
                    self._pool.putconn(self._conn)
                    return
                except (psycopg2.Error, KeyError):
                    pass
            self._discard()
        finally:
            # Must happen however we got here, or the pool leaks slots and
            # eventually every request blocks for CHECKOUT_TIMEOUT.
            self._drop_slot()

    def _discard(self):
        """Throw this connection away, and tell the pool we did.

        Closing the socket is not enough. psycopg2's pool tracks checked-out
        connections in its own `_used` dict and only removes them on putconn,
        so a bare close() left the entry behind for ever: the pool went on
        believing the connection was in use, its capacity dropped by one, and
        after POOL_MAX such events every request failed with
        "PoolError: connection pool exhausted" -- the precise error the
        semaphore above was added to make impossible.

        putconn(close=True) both closes it and frees the pool's slot.
        """
        self._free_cursors()
        if self._pool is not None:
            try:
                self._pool.putconn(self._conn, close=True)
                return
            except (psycopg2.Error, KeyError):
                pass
        try:
            self._conn.close()
        except psycopg2.Error:
            pass

    def close(self):
        self._discard()
        self._drop_slot()


class _TrackedConnection(psycopg2.extensions.connection):
    """A connection that can carry its own last-used timestamp.

    psycopg2.extensions.connection is a C type with no __dict__, so
    `conn._sahay_last_used = ...` raises AttributeError. That assignment sat
    inside a bare `except Exception: pass`, so it failed silently on every
    release, the attribute was never set, and alive() always took the
    "no timestamp recorded, assume alive" branch. The whole IDLE_PROBE_AFTER
    mechanism was dead code, and stale connections went straight to routes,
    which is where "InterfaceError: connection already closed" came from.

    A Python subclass has a __dict__, so the timestamp actually sticks.
    """


def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _pgpool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=POOL_MAX,
                    dsn=dsn(),
                    connection_factory=_TrackedConnection,
                    cursor_factory=RealDictCursor,
                )
    return _pool


def checkout():
    """A pooled connection, verified live. Waits rather than failing.

    Two things this has to get right:

      * Queueing. The semaphore is acquired before touching the pool, so a
        burst of requests waits its turn instead of hitting PoolError.
      * Liveness. Supabase's pooler closes idle connections and psycopg2 hands
        them back anyway, so a dead one is discarded and retried rather than
        returned to the caller (which is how every route ended up reporting
        "connection already closed").
    """
    if not _slots.acquire(timeout=CHECKOUT_TIMEOUT):
        raise psycopg2.OperationalError(
            f"timed out after {CHECKOUT_TIMEOUT}s waiting for a database "
            f"connection (all {POOL_MAX} in use)")
    pool = get_pool()
    try:
        for _ in range(3):
            raw = pool.getconn()
            raw.autocommit = False
            con = PostgresWrapper(raw, pool, holds_slot=True)
            if con.alive():
                return con
            # Dead: drop it from the pool entirely rather than recycling it.
            # putconn(close=True) frees the pool's slot for a fresh connect.
            con._holds_slot = False      # the retry keeps our semaphore slot
            try:
                pool.putconn(raw, close=True)
            except (psycopg2.Error, KeyError):
                pass
        raise psycopg2.OperationalError(
            "could not obtain a live database connection after 3 attempts")
    except BaseException:
        _slots.release()
        raise


def connect():
    """A standalone (unpooled) connection.

    Used by the CLI entry points -- service.py, check.py, verify.py, ml.py --
    which are single-threaded and want a connection they fully own.
    """
    raw = psycopg2.connect(dsn(), connection_factory=_TrackedConnection,
                           cursor_factory=RealDictCursor)
    raw.autocommit = False
    return PostgresWrapper(raw, pool=None, holds_slot=False)


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
