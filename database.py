"""SQLite storage for study sessions.

Flask keeps one database connection per request (see ``get_db`` /
``close_db``).  This module owns everything that touches the database:

* ``init_db``               -- create or upgrade the schema
* ``insert_session``        -- add one row, return the new row's id
* ``update_session``        -- change one row's subject / minutes / date
* ``delete_session``        -- remove one row by id
* ``get_session``           -- one row by id, or None
* ``subject_totals``        -- minutes per subject, grouped case-insensitively
* ``fetch_sessions``        -- read rows back (newest first, with filters)
* ``count_sessions``        -- how many rows a filtered query would return
* ``sessions_on_or_after``  -- the rows charts.py needs for the weekly chart

Every query uses ``?`` placeholders (parameterized SQL): the values are
passed as a separate tuple, never spliced into the SQL string.  That is what
makes SQL injection impossible -- a subject like "Robert'); DROP TABLE
sessions;--" is treated as a value, not as code.  The one exception is a
table name in ``PRAGMA table_info(...)``, which SQLite cannot parameterize;
those names are constants from this module and never user input.
"""

import sqlite3
from datetime import datetime, timezone

from flask import current_app, g

# ---------------------------------------------------------------------------
# Schema migrations
# ---------------------------------------------------------------------------
# The database is upgraded by numbered steps rather than one big
# ``CREATE TABLE IF NOT EXISTS``.  Two reasons:
#
#   1. IF NOT EXISTS only creates a table that is *missing*.  It can never
#      add a column to a table that already exists, so a study.db written by
#      the first version of this app would be stuck without ``created_at``.
#   2. The ``schema_version`` table records how far a file has been brought,
#      so an upgrade runs exactly once and every later startup is a no-op.
#
# Each step is idempotent: it checks what already exists before it changes
# anything, so re-running a half-finished step is safe.  All pending steps
# run inside one transaction, so a failure rolls the whole upgrade back
# instead of leaving a half-migrated file behind.

SCHEMA_VERSION = 4  # the newest step this code knows how to apply

CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
)
"""


def utc_now_iso():
    """Right now as UTC, ISO 8601, second precision: '2026-10-15T09:30:04Z'.

    The trailing Z keeps the string unambiguous (always UTC, never local
    time), and because every field is fixed-width the strings also sort
    chronologically as plain text.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _table_names(conn):
    """Every table in the database, as a set of names."""
    return {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _column_names(conn, table):
    """Every column of ``table``.

    ``table`` is a constant from this module -- PRAGMA cannot take a ``?``
    placeholder -- so there is no injection risk here.
    """
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migration_1_create_sessions(conn):
    """Step 1: the original table plus the study_date index."""
    if "sessions" in _table_names(conn):
        return  # already there; nothing to do
    conn.execute(
        """
        CREATE TABLE sessions (
            id         INTEGER PRIMARY KEY,     -- sqlite assigns this automatically
            subject    TEXT NOT NULL,
            minutes    INTEGER NOT NULL,
            study_date TEXT    NOT NULL,       -- stored as 'YYYY-MM-DD'
            created_at TEXT                     -- UTC 'YYYY-MM-DDTHH:MM:SSZ', added in step 2
        )
        """
    )
    # Dates are TEXT, but 'YYYY-MM-DD' sorts chronologically as a string, so
    # this index speeds up the "recent rows" list, the weekly-chart query
    # and the date filters on /sessions.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_study_date
            ON sessions (study_date)
        """
    )


def _migration_2_add_created_at(conn):
    """Step 2: add the ``created_at`` column to an existing table."""
    # ALTER TABLE has no IF NOT EXISTS, so ask first.  Checking also makes
    # this step idempotent: a second run sees the column and does nothing.
    if "created_at" in _column_names(conn, "sessions"):
        return
    conn.execute("ALTER TABLE sessions ADD COLUMN created_at TEXT")

    # Rows that already existed keep created_at = NULL, and the sessions
    # list shows those as "--".  We know when they were *studied*, not when
    # they were typed in, and a made-up timestamp would be worse than an
    # honest blank: it would look like real data.  If you want them filled
    # in, do it by hand (see the README's "Migrating an existing study.db").


def _migration_3_create_weekly_goals(conn):
    """Step 3: the weekly goals table.

    A goal is one row: ``subject`` NULL is the overall target, a named row
    is the target for that subject.  The app replaces the whole set in one
    transaction when the /goals form is saved, so there is no per-row
    uniqueness rule to maintain in SQL.
    """
    if "weekly_goals" in _table_names(conn):
        return  # already there; nothing to do
    conn.execute(
        """
        CREATE TABLE weekly_goals (
            id      INTEGER PRIMARY KEY,
            subject TEXT,               -- NULL = the overall goal
            minutes INTEGER NOT NULL
        )
        """
    )


def _migration_4_create_session_tags(conn):
    """Step 4: one row per tag, so tags can be matched and indexed.

    A separate table beats a comma-separated column: matching one tag
    needs no string parsing, the expression index makes that match cheap,
    and ON DELETE CASCADE takes a session's tags with it when the session
    is deleted (connections turn foreign keys on in get_db).
    """
    if "session_tags" in _table_names(conn):
        return  # already there; nothing to do
    conn.execute(
        """
        CREATE TABLE session_tags (
            session_id INTEGER NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
            tag        TEXT    NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_session_tags_session
            ON session_tags (session_id)
        """
    )
    # Matching is case-insensitive (LOWER(tag) = LOWER(?)), so index the
    # folded value where SQLite can use it.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_session_tags_tag
            ON session_tags (LOWER(tag))
        """
    )


# (number, description, function) -- applied in this order.
MIGRATIONS = (
    (1, "create the sessions table", _migration_1_create_sessions),
    (2, "add created_at", _migration_2_add_created_at),
    (3, "create the weekly_goals table", _migration_3_create_weekly_goals),
    (4, "create the session_tags table", _migration_4_create_session_tags),
)


def applied_migrations():
    """The set of migration numbers already applied to this database."""
    conn = get_db()
    if "schema_version" not in _table_names(conn):
        return frozenset()
    return frozenset(
        row["version"] for row in conn.execute("SELECT version FROM schema_version")
    )


def init_db():
    """Create or upgrade the schema.  Safe to call on every startup.

    Pending steps are applied in order and recorded; if there is nothing to
    do this is a single read and no write, so a restart is cheap.
    """
    conn = get_db()
    conn.execute(CREATE_SCHEMA_VERSION)
    applied = {
        row["version"] for row in conn.execute("SELECT version FROM schema_version")
    }
    pending = [step for step in MIGRATIONS if step[0] not in applied]
    if not pending:
        return

    try:
        for version, _description, apply_step in pending:
            apply_step(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                (version,),
            )
        conn.commit()
    except BaseException:
        # Leave the file exactly as it was rather than half-upgraded.
        conn.rollback()
        raise


def get_db():
    """Return this request's database connection, opening it on first use.

    ``g`` is Flask's per-request storage, so every call inside one request
    reuses the same connection, and ``close_db`` closes it when the request
    (or the app context) ends.
    """
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"])
        # Row objects support row["subject"] as well as row[1].
        conn.row_factory = sqlite3.Row
        # SQLite leaves foreign keys off by default; the tag rows rely on
        # ON DELETE CASCADE to disappear with their session.
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(exception=None):
    """Close the connection at the end of a request (registered in app.py)."""
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _replace_tags(conn, session_id, tags):
    """Swap a session's tag rows.  ``tags`` may be an empty list."""
    conn.execute("DELETE FROM session_tags WHERE session_id = ?", (session_id,))
    for tag in tags or ():
        conn.execute(
            "INSERT INTO session_tags (session_id, tag) VALUES (?, ?)",
            (session_id, tag),
        )


def insert_session(subject, minutes, study_date, created_at=None, tags=None):
    """Insert one session and return the new row's id.

    ``study_date`` must already be a validated 'YYYY-MM-DD' string and
    ``created_at`` a UTC ISO 8601 string; validators.py guarantees the
    format before we get here.  ``created_at`` is optional so a test can
    pin it to a fixed value -- at runtime it defaults to now, in UTC.
    ``tags`` is the cleaned list from validators.validate_tags; the rows
    and their tags are written together in one commit.
    """
    conn = get_db()
    cursor = conn.execute(
        """
        INSERT INTO sessions (subject, minutes, study_date, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (subject, minutes, study_date, created_at or utc_now_iso()),
    )
    _replace_tags(conn, cursor.lastrowid, tags)
    conn.commit()  # sqlite needs an explicit commit to save
    return cursor.lastrowid


def insert_sessions(rows):
    """Insert many sessions in one transaction: all of them, or none.

    ``rows`` is an iterable of ``(subject, minutes, study_date, created_at,
    tags)`` tuples, already validated by validators.validate_sessions_csv.
    The CSV importer uses this so a failure part-way cannot leave half a
    file imported; the whole list commits together or rolls back together.
    Returns the number of rows inserted.
    """
    conn = get_db()
    inserted = 0
    try:
        for subject, minutes, study_date, created_at, tags in rows:
            cursor = conn.execute(
                """
                INSERT INTO sessions (subject, minutes, study_date, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (subject, minutes, study_date, created_at or utc_now_iso()),
            )
            _replace_tags(conn, cursor.lastrowid, tags)
            inserted += 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return inserted


def update_session(session_id, subject, minutes, study_date, tags=None):
    """Overwrite one session's fields.  Returns True if a row was updated.

    ``created_at`` is deliberately left alone: it records when the row was
    first logged, and correcting a typo in the subject should not make it
    look like the session was just re-entered.  ``tags=None`` leaves the
    stored tags untouched (an edit that never showed them); a list,
    including an empty one, replaces them.
    """
    conn = get_db()
    cursor = conn.execute(
        """
        UPDATE sessions
        SET subject = ?, minutes = ?, study_date = ?
        WHERE id = ?
        """,
        (subject, minutes, study_date, session_id),
    )
    if tags is not None and cursor.rowcount > 0:
        _replace_tags(conn, session_id, tags)
    conn.commit()
    return cursor.rowcount > 0


def delete_session(session_id):
    """Delete one session by id.  Returns True if a row was actually removed."""
    cursor = get_db().execute(
        "DELETE FROM sessions WHERE id = ?",
        (session_id,),
    )
    get_db().commit()
    return cursor.rowcount > 0


def get_session(session_id):
    """One session by id, or ``None`` if there is no such row.

    ``tags_text`` is the tags joined with ", " for display, or NULL when
    the row has none.
    """
    return get_db().execute(
        """
        SELECT id, subject, minutes, study_date, created_at,
               (SELECT GROUP_CONCAT(t.tag, ', ') FROM session_tags t
                WHERE t.session_id = sessions.id) AS tags_text
        FROM sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()


def subject_totals():
    """Minutes per subject, largest total first (ties A-Z).

    How capitalization is handled (the "Subjects and capitalization" note
    in the README explains the choice in full):

    * the row keeps exactly what the user typed, only trimmed -- their data
      is never rewritten behind their back;
    * the *grouping* is case-insensitive, done in SQL with ``LOWER()``, so
      "math", "Math" and "MATH" all land in one total;
    * the displayed label is the most recently logged spelling, so the
      dashboard keeps saying whatever the user last called the subject.

    Note SQLite's ``LOWER()`` folds ASCII letters, which covers realistic
    subject names; if you ever need full Unicode folding, do the grouping
    in Python instead.
    """
    return get_db().execute(
        """
        SELECT newest.subject AS subject, totals.minutes AS minutes
        FROM (
            -- One row per case-folded subject: its total minutes and the id
            -- of the newest row that used that spelling.
            SELECT LOWER(subject) AS folded_subject,
                   SUM(minutes)     AS minutes,
                   MAX(id)          AS newest_id
            FROM sessions
            GROUP BY LOWER(subject)
        ) AS totals
        JOIN sessions AS newest ON newest.id = totals.newest_id
        ORDER BY totals.minutes DESC, newest.subject ASC
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Weekly goals
# ---------------------------------------------------------------------------

def get_goals():
    """Every stored goal: the overall one first, then subjects A-Z.

    The overall goal is the row whose ``subject`` is NULL; per-subject goal
    rows carry the subject label as it was when the goal was saved.
    """
    return get_db().execute(
        """
        SELECT id, subject, minutes
        FROM weekly_goals
        ORDER BY subject IS NOT NULL, LOWER(subject)
        """
    ).fetchall()


def save_goals(overall_minutes, subject_goals):
    """Replace the whole goal set in one transaction.

    ``overall_minutes`` is an int or None (None clears the overall goal);
    ``subject_goals`` maps subject label -> minutes.  Replace-all keeps the
    stored set exactly equal to what the form showed, and the transaction
    means a mid-way failure cannot leave half the new set behind.
    """
    conn = get_db()
    try:
        conn.execute("DELETE FROM weekly_goals")
        if overall_minutes is not None:
            conn.execute(
                "INSERT INTO weekly_goals (subject, minutes) VALUES (NULL, ?)",
                (overall_minutes,),
            )
        for subject, minutes in subject_goals.items():
            conn.execute(
                "INSERT INTO weekly_goals (subject, minutes) VALUES (?, ?)",
                (subject, minutes),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _like_needle(text):
    """A LIKE pattern that matches ``text`` anywhere, literally.

    "%" and "_" are LIKE wildcards and "\\" is the escape character, so
    all three are escaped and the query says ESCAPE '\\'.  Without this,
    searching for "50%" would match every subject starting with "50".
    """
    escaped = (
        text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def _session_filters(start_date, end_date, search=None, tag=None):
    """Build the WHERE clause shared by the session queries.

    Returns ``(sql_fragment, params)``.  Only fixed fragments are joined
    into the SQL text; every value a caller could control goes into
    ``params`` as a ``?`` placeholder.

    * ``search`` -- matches the subject anywhere, case-insensitively.
    * ``tag``    -- matches one tag exactly, case-insensitively.
    """
    clauses = []
    params = []
    if start_date:
        clauses.append("study_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("study_date <= ?")
        params.append(end_date)
    if search:
        clauses.append("LOWER(subject) LIKE ? ESCAPE '\\'")
        params.append(_like_needle(search.lower()))
    if tag:
        clauses.append(
            "EXISTS (SELECT 1 FROM session_tags t"
            " WHERE t.session_id = sessions.id AND LOWER(t.tag) = LOWER(?))"
        )
        params.append(tag)
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def count_sessions(start_date=None, end_date=None, search=None, tag=None):
    """How many sessions a filtered query would return (for pagination)."""
    where, params = _session_filters(start_date, end_date, search, tag)
    row = get_db().execute(
        f"SELECT COUNT(*) AS total FROM sessions{where}", params
    ).fetchone()
    return row["total"]


def fetch_sessions(start_date=None, end_date=None, search=None, tag=None,
                   limit=None, offset=None):
    """Fetch sessions, newest study date first.

    The date filters come first because they are the ones a caller most
    often passes positionally; ``?start=&end=`` is the common case.

    * ``start_date`` / ``end_date`` -- inclusive 'YYYY-MM-DD' bounds, used
      by the /sessions filters and the CSV export.  Omit them for all rows.
    * ``search`` -- free text matched against the subject, case-insensitive.
    * ``tag``    -- one tag, matched case-insensitively.
    * ``limit`` / ``offset`` -- the SQL LIMIT and OFFSET that /sessions
      pagination uses.  SQLite wants LIMIT before OFFSET, so an offset with
      no limit becomes ``LIMIT -1 OFFSET ?`` ("all rows, skip this many").

    Call it with no arguments at all to get every session.
    """
    where, params = _session_filters(start_date, end_date, search, tag)
    sql = f"""
        SELECT id, subject, minutes, study_date, created_at,
               (SELECT GROUP_CONCAT(t.tag, ', ') FROM session_tags t
                WHERE t.session_id = sessions.id) AS tags_text
        FROM sessions{where}
        ORDER BY study_date DESC, id DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params = params + [limit]
        if offset is not None:
            sql += " OFFSET ?"
            params = params + [offset]
    elif offset is not None:
        sql += " LIMIT -1 OFFSET ?"
        params = params + [offset]
    return get_db().execute(sql, params).fetchall()


def sessions_on_or_after(iso_date):
    """Sessions on or after an ISO date string, oldest first.

    Used for the weekly chart.  The ``study_date >= ?`` comparison matches
    the index created by migration 1, so the database only hands back the
    rows the chart could possibly use.
    """
    return get_db().execute(
        """
        SELECT id, subject, minutes, study_date, created_at
        FROM sessions
        WHERE study_date >= ?
        ORDER BY study_date ASC, id ASC
        """,
        (iso_date,),
    ).fetchall()
