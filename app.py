"""Cadence: the Flask app factory and all the routes.

Run it for development:  python app.py   ->  http://127.0.0.1:5000  (Ctrl+C)
Run it "production style":  waitress-serve --call app:create_app
    (see the "Run in production-style mode" section of the README)

Two environment variables matter:

* ``SECRET_KEY``     -- signs session cookies and CSRF tokens.  If it is
  unset you get a hardcoded dev-only key and a warning at startup.
* ``DATABASE_PATH``  -- the SQLite file.  Defaults to ``study.db`` next to
  this file.

Design notes:

* ``create_app`` *builds and returns* an app; nothing runs at import time.
  That is why the tests can create their own app pointed at a temporary
  database file before making any requests.
* Views are small on purpose: validation lives in validators.py, the chart
  math lives in charts.py, storage lives in database.py.  This file is just
  the wiring between HTTP and those three.
"""

import csv
import io
import os
from datetime import date
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_wtf.csrf import CSRFProtect

import charts
import validators
from database import (
    close_db,
    count_sessions,
    delete_session as delete_session_row,  # alias: the view keeps the nice name
    fetch_sessions,
    get_goals,
    get_session,
    init_db,
    insert_session,
    insert_sessions,
    save_goals,
    sessions_on_or_after,
    subject_totals,
    update_session,
)

RECENT_SESSIONS_LIMIT = 10  # the dashboard's list; the full history is at /sessions

# Pagination for /sessions.  per_page is one of these choices, so a URL can
# never ask for a million rows at once.
DEFAULT_PER_PAGE = 20
PER_PAGE_CHOICES = (10, 20, 50, 100)

# The CSV import caps uploads at 1 MiB.  A bigger file is thousands of rows,
# which is too much to check and hand back in one page anyway.
MAX_IMPORT_BYTES = 1024 * 1024

# ---------------------------------------------------------------------------
# Configuration read from the environment
# ---------------------------------------------------------------------------
# Both values can be overridden without editing code, which is how you point
# the app at a different database (a backup, a test file, a network drive)
# or supply the real secret key on a server.

SECRET_KEY_ENV = "SECRET_KEY"
DATABASE_PATH_ENV = "DATABASE_PATH"

# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# !!  DEV ONLY -- DO NOT USE THIS KEY IN A REAL DEPLOYMENT.               !!
# !!  It is hardcoded on purpose so "python app.py" works on a fresh clone  !!
# !!  with zero setup.  Anyone who can read this source knows the key, so   !!
# !!  it protects you from yourself (no session tampering in dev) and       !!
# !!  never from an attacker.  Set the SECRET_KEY environment variable and  !!
# !!  this fallback is never used; the app also logs a loud warning every   !!
# !!  time it has to fall back to it.                                       !!
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
DEV_ONLY_SECRET_KEY = "dev-only-insecure-fallback-key-do-not-ship-this-value"


def _database_path(app):
    """The SQLite file to use.

    Defaults to ``study.db`` next to ``app.py`` so "python app.py" always
    finds the same database regardless of the directory it was launched
    from.  ``DATABASE_PATH`` overrides it, absolutely or relative to the
    directory you launch from.
    """
    return os.environ.get(DATABASE_PATH_ENV) or str(Path(app.root_path) / "study.db")


def create_app(test_config=None):
    """Build and configure the Cadence app.

    ``test_config`` is a dict of overrides -- the tests pass a temporary
    DATABASE path and their own SECRET_KEY.  At normal runtime it is None
    and the defaults below apply.
    """
    app = Flask(__name__)

    if SECRET_KEY_ENV in os.environ:
        secret_key = os.environ[SECRET_KEY_ENV]
    else:
        secret_key = DEV_ONLY_SECRET_KEY
        # Said once, at startup, in the terminal you launched the app from,
        # so it cannot be missed.
        app.logger.warning(
            "%s is not set, so the built-in dev-only key is being used. "
            "Set it to a long random string before running this anywhere "
            "other than your own machine.",
            SECRET_KEY_ENV,
        )

    app.config.from_mapping(
        # flash() messages and CSRF tokens are signed with this key, so it
        # must stay secret in production (see DEV_ONLY_SECRET_KEY above).
        SECRET_KEY=secret_key,
        # The SQLite file this app reads and writes.
        DATABASE=_database_path(app),
        # Cap any request body at 1 MiB. Only the CSV import uploads a file,
        # and a file bigger than this is thousands of rows: too much to check
        # in one page anyway, so it gets a clear "split it up" message (413).
        MAX_CONTENT_LENGTH=MAX_IMPORT_BYTES,
    )
    if test_config is not None:
        app.config.update(test_config)

    # One connection per request; close it when the request ends.
    app.teardown_appcontext(close_db)

    # -----------------------------------------------------------------
    # Template helpers
    # -----------------------------------------------------------------
    # Durations are shown all over the interface ("2h 15m", "45m"), and
    # thousands separators keep a five-digit total readable.  Keeping both
    # here means the templates stay free of formatting arithmetic.

    @app.template_filter("duration")
    def format_duration(minutes):
        """Minutes -> "2h 15m", "3h", "45m" or "0m" for display."""
        total = max(0, int(minutes or 0))
        hours, rest = divmod(total, 60)
        if not hours:
            return f"{rest}m"
        return f"{hours}h {rest}m" if rest else f"{hours}h"

    @app.template_filter("num")
    def format_number(value):
        """1234 -> "1,234"; anything unparseable is shown as-is."""
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return value

    @app.context_processor
    def inject_template_globals():
        """Values every page needs: today's date, in both shapes templates use.

        ``today_iso`` feeds the date pickers' max attribute and the default
        for a new session; ``today_label`` is the dashboard's eyebrow.
        """
        today = date.today()
        return {
            "today_iso": today.isoformat(),
            "today_label": today.strftime("%A, %B ") + str(today.day),
        }

    # -----------------------------------------------------------------
    # CSRF protection for every HTML form (Flask-WTF)
    # -----------------------------------------------------------------
    # CSRFProtect installs a before_request hook that rejects
    # POST/PUT/PATCH/DELETE requests that arrive without a valid token.
    # Templates embed one with {{ csrf_token() }}; without it a form
    # submit gets a 400.  The JSON API is explicitly exempt -- see the
    # comment next to csrf.exempt() below.
    csrf = CSRFProtect()
    csrf.init_app(app)

    # Create or upgrade the schema on startup.  Safe to run every time:
    # the migrations in database.py are idempotent, so an existing study.db
    # keeps its rows and only the missing steps are applied.
    with app.app_context():
        init_db()

    # -----------------------------------------------------------------
    # Helpers for the two list pages (shared by /sessions and the CSV export)
    # -----------------------------------------------------------------

    def read_session_filters():
        """Read and validate ?start=&end=&search=&tag= on the query string.

        Returns ``(filters, errors)``.  ``filters`` holds the values to hand
        to database.py (cleaned strings) *and* the raw text the user typed,
        so a rejected filter can be re-rendered exactly as they entered it.
        ``errors`` maps a message to the field that needs fixing.  All four
        bounds are optional -- an empty box means "no limit", not "today".
        """
        start, start_error = validators.validate_optional_date(
            request.args.get("start")
        )
        end, end_error = validators.validate_optional_date(request.args.get("end"))
        search, search_error = validators.validate_search(request.args.get("search"))
        tag, tag_error = validators.validate_tag_filter(request.args.get("tag"))

        errors = {}
        if start_error:
            errors["start"] = start_error
        if end_error:
            errors["end"] = end_error
        if search_error:
            errors["search"] = search_error
        if tag_error:
            errors["tag"] = tag_error
        range_error = validators.validate_date_range(start, end)
        if range_error:
            errors["range"] = range_error

        filters = {
            # What database.py uses in its WHERE clauses.
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "search_text": search,
            "tag_text": tag,
            # What the form re-renders.
            "start": request.args.get("start", "") or "",
            "end": request.args.get("end", "") or "",
            "search": request.args.get("search", "") or "",
            "tag": request.args.get("tag", "") or "",
        }
        return filters, errors

    def read_paging(total):
        """Work out which slice of the history to show.

        The page number comes from the URL, so it is clamped to what exists
        instead of trusted: ``?page=999`` shows the last page and
        ``?page=abc`` shows the first one.  Returns a dict for the template.
        """
        per_page = validators.validate_per_page(
            request.args.get("per_page"), DEFAULT_PER_PAGE, PER_PAGE_CHOICES
        )
        total_pages = max(1, -(-total // per_page))  # ceiling division
        page = min(validators.validate_page_number(request.args.get("page")),
                   total_pages)
        return {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "offset": (page - 1) * per_page,
            "has_previous": page > 1,
            "has_next": page < total_pages,
            "per_page_choices": PER_PAGE_CHOICES,
        }

    @app.get("/")
    def dashboard():
        """Dashboard: totals per subject, weekly bar chart, recent sessions."""
        # 1) The chart only needs the sessions inside its 8-week window --
        #    the ? >= ? query uses the study_date index.
        window_start = charts.weekly_window_start()
        weekly_rows = sessions_on_or_after(window_start)
        weekly = charts.weekly_minutes(weekly_rows)
        # The per-subject table is the same rows bucketed per subject, so
        # the combined chart and the table can never disagree.
        breakdown = charts.weekly_minutes_by_subject(weekly_rows)

        # 3) The recent list is capped on purpose -- /sessions has the rest.
        #    The count is shown so the link says how many are waiting.
        totals = subject_totals()
        recent = fetch_sessions(limit=RECENT_SESSIONS_LIMIT)

        # 4) Weekly goals: this week's minutes against each stored target.
        #    A subject goal with no sessions this week reads as 0, not as
        #    "missing", so the progress can never silently disappear.
        this_week_by_subject = {
            series["subject"].lower(): series["minutes"][-1]
            for series in breakdown["series"]
        }
        goal_progress = []
        for goal in get_goals():
            if goal["subject"] is None:
                minutes, label = weekly[-1]["minutes"], "All subjects"
            else:
                minutes = this_week_by_subject.get(goal["subject"].lower(), 0)
                label = goal["subject"]
            goal_progress.append(
                {
                    "subject": label,
                    "progress": charts.goal_progress(minutes, goal["minutes"]),
                }
            )

        # Finally, the headline numbers for the four summary cards.  They
        # are all derived from the rows already fetched above, so the cards,
        # the chart and /api/summary always agree.
        total_count = count_sessions()
        total_minutes = sum(row["minutes"] for row in totals)
        return render_template(
            "dashboard.html",
            totals=totals,
            weekly=weekly,
            breakdown=breakdown,
            goal_progress=goal_progress,
            recent=recent,
            recent_limit=RECENT_SESSIONS_LIMIT,
            total_count=total_count,
            total_minutes=total_minutes,
            this_week_minutes=weekly[-1]["minutes"],
            weekly_total=sum(week["minutes"] for week in weekly),
            subject_count=len(totals),
            avg_session=round(total_minutes / total_count) if total_count else 0,
            # The single JSON payload the chart script reads.  It carries the
            # same structure as GET /api/summary, which keeps the two in step.
            chart_data={
                "weekly_minutes": weekly,
                "weekly_by_subject": breakdown,
                "weeks_on_chart": charts.WEEKS_ON_CHART,
            },
        )

    @app.route("/goals", methods=["GET", "POST"])
    def goals():
        """GET: the goal form.  POST: validate, then save or re-render."""
        errors = {}
        rows = []  # [{"name": subject, "value": minutes-or-blank}]
        overall_value = ""

        if request.method == "POST":
            data, errors = validators.validate_goals_form(request.form)
            if not errors:
                save_goals(data["overall"], data["subjects"])
                flash("Weekly goals saved.", "success")
                return redirect(url_for("goals"))
            # Re-render exactly what was typed: the hidden subject fields
            # are read back pair by pair.
            overall_value = request.form.get("overall", "")
            index = 0
            while f"subject_{index}" in request.form:
                rows.append(
                    {
                        "name": request.form.get(f"subject_{index}", ""),
                        "value": request.form.get(f"goal_{index}", ""),
                    }
                )
                index += 1
        else:
            stored = get_goals()
            overall = next(
                (row["minutes"] for row in stored if row["subject"] is None),
                None,
            )
            overall_value = overall if overall is not None else ""
            by_folded = {
                row["subject"].lower(): row["minutes"]
                for row in stored
                if row["subject"] is not None
            }
            rows = [
                {
                    "name": row["subject"],
                    "value": by_folded.get(row["subject"].lower(), ""),
                }
                for row in subject_totals()
            ]

        return render_template(
            "goals.html", errors=errors, rows=rows, overall_value=overall_value
        )

    @app.route("/sessions/new", methods=["GET", "POST"])
    def new_session():
        """GET: show the form.  POST: validate, then save or re-render."""
        form_values = {}
        errors = {}
        if request.method == "POST":
            data, errors = validators.validate_session_form(request.form)
            if not errors:
                insert_session(
                    data["subject"],
                    data["minutes"],
                    data["study_date"].isoformat(),
                    tags=data["tags"],
                )
                flash(
                    f"Logged {data['minutes']} minutes of {data['subject']} "
                    f"on {data['study_date'].isoformat()}.",
                    "success",
                )
                # Post/redirect/get: after a successful submit the browser
                # lands on the dashboard, so refreshing or going back
                # cannot submit the same session twice.
                return redirect(url_for("dashboard"))
            # Errors: fall through and re-render with the messages.
            # request.form doubles as the "what you already typed" values.
            form_values = request.form
        return render_template(
            "new_session.html",
            errors=errors,
            form=form_values,
            # Suggestions for the subject box (a <datalist>): a plain list of
            # subject names already in the database, not a requirement.
            subjects=[row["subject"] for row in subject_totals()],
        )

    @app.route("/sessions/<int:session_id>/edit", methods=["GET", "POST"])
    def edit_session(session_id):
        """GET: the form, pre-filled.  POST: validate, then save or re-render."""
        session = get_session(session_id)
        if session is None:
            # Unknown id: a real 404, not a silent redirect to the dashboard,
            # so a stale bookmark says so instead of looking like it worked.
            abort(404)

        errors = {}
        if request.method == "POST":
            data, errors = validators.validate_session_form(request.form)
            if not errors:
                update_session(
                    session_id,
                    data["subject"],
                    data["minutes"],
                    data["study_date"].isoformat(),
                    tags=data["tags"],
                )
                flash(f"Session {session_id} updated.", "success")
                return redirect(url_for("sessions"))
            form_values = request.form
        else:
            form_values = {
                "subject": session["subject"],
                "minutes": session["minutes"],
                "study_date": session["study_date"],
                # Joined with ", " so it can be edited like it was typed.
                "tags": session["tags_text"] or "",
            }

        return render_template(
            "edit_session.html",
            errors=errors,
            form=form_values,
            session=session,
            subjects=[row["subject"] for row in subject_totals()],
        )

    @app.post("/sessions/<int:session_id>/delete")
    def delete_session(session_id):
        """Delete one session, then land on the history page.

        Deliberate choice: the button also appears on the dashboard, but
        /sessions always shows the updated list, so the result of the delete
        is visible wherever the button was pressed.  The delete button is a
        small one-button POST form, so it carries the same {{ csrf_token() }}
        as every other form on the site.
        """
        if delete_session_row(session_id):
            flash("Session deleted.", "success")
        else:
            flash("That session no longer existed.", "warning")
        return redirect(url_for("sessions"))

    @app.get("/sessions")
    def sessions():
        """The full history: filters, one page of rows, and paging controls."""
        filters, errors = read_session_filters()
        if errors:
            # A bad ?start=/?end= is a client error, so answer 400 and say
            # which box is wrong rather than quietly ignoring the filter.
            # The values the user typed stay in the boxes so they can fix
            # only what is broken.
            return (
                render_template(
                    "sessions.html",
                    errors=errors,
                    filters=filters,
                    rows=[],
                    paging=None,
                ),
                400,
            )

        total = count_sessions(
            start_date=filters["start_date"],
            end_date=filters["end_date"],
            search=filters["search_text"],
            tag=filters["tag_text"],
        )
        paging = read_paging(total)
        rows = fetch_sessions(
            limit=paging["per_page"],
            offset=paging["offset"],
            start_date=filters["start_date"],
            end_date=filters["end_date"],
            search=filters["search_text"],
            tag=filters["tag_text"],
        )

        return render_template(
            "sessions.html",
            errors={},
            filters=filters,
            rows=rows,
            paging=paging,
        )

    @app.get("/sessions/export.csv")
    def export_sessions_csv():
        """The filtered sessions as a CSV file download.

        Same filters as /sessions, no paging: this is the "give me
        everything between these two dates" button.
        """
        filters, filter_errors = read_session_filters()
        if filter_errors:
            # Plain text rather than the HTML page: this is a data endpoint,
            # and a curl caller should see the message.
            message = " ".join(
                f"{field}: {text}" for field, text in filter_errors.items()
            )
            return Response(f"Invalid date filter. {message}\n", status=400,
                            mimetype="text/plain")

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "subject", "minutes", "study_date", "created_at", "tags"])
        for row in fetch_sessions(
            start_date=filters["start_date"],
            end_date=filters["end_date"],
            search=filters["search_text"],
            tag=filters["tag_text"],
        ):
            writer.writerow(
                [
                    row["id"],
                    row["subject"],
                    row["minutes"],
                    row["study_date"],
                    # Rows logged before migration 2 have no timestamp; an
                    # empty cell says "unknown", which is the truth.
                    row["created_at"] or "",
                    # Tags joined with ", " so the export round-trips through
                    # the importer's tags field.
                    row["tags_text"] or "",
                ]
            )

        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=study-sessions.csv"
            },
        )

    @app.route("/sessions/import", methods=["GET", "POST"])
    def import_sessions():
        """GET: the upload form.  POST: check the whole file, then import.

        All or nothing: every data row is validated before anything is
        written, and a file with any problem imports nothing.  The file can
        be fixed and uploaded again without hunting for half-imported rows.
        """
        if request.method == "GET":
            return render_template(
                "import_sessions.html",
                file_error=None,
                rejected=[],
            )

        upload = request.files.get("file")
        rejected = []
        if upload is None or not upload.filename:
            file_error = "Choose a CSV file to import."
        elif not upload.filename.lower().endswith(".csv"):
            file_error = "The upload must be a .csv file."
        else:
            try:
                # utf-8-sig also eats the BOM Excel writes before "subject".
                text = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                file_error = "The file must be UTF-8 text."
            else:
                parsed, rejected, file_error = validators.validate_sessions_csv(text)
                if file_error is None and not rejected:
                    count = insert_sessions(parsed)
                    flash(
                        f"Imported {count} session{'s' if count != 1 else ''}.",
                        "success",
                    )
                    return redirect(url_for("sessions"))

        return render_template(
            "import_sessions.html",
            file_error=file_error,
            rejected=rejected,
        )

    @app.get("/api/summary")
    def api_summary():
        """The dashboard's chart data as JSON.

        It calls the same database.py / charts.py functions the HTML page
        calls, so the JSON and the bars can never disagree.
        """
        window_start = charts.weekly_window_start()
        weekly_rows = sessions_on_or_after(window_start)
        weekly = charts.weekly_minutes(weekly_rows)
        breakdown = charts.weekly_minutes_by_subject(weekly_rows)
        totals = subject_totals()
        return jsonify(
            {
                "totals_by_subject": [
                    {"subject": row["subject"], "minutes": row["minutes"]}
                    for row in totals
                ],
                "weekly_minutes": weekly,
                "weekly_by_subject": breakdown,
                "weeks_on_chart": charts.WEEKS_ON_CHART,
            }
        )

    @app.post("/api/sessions")
    def api_create_session():
        """Create a session from a JSON body.

        This is the one route beyond the four in the plan: the HTML form
        already covers the "bad form input" case, so the API needs its own
        endpoint for the rule "invalid API input -> HTTP 400 + JSON error".
        """
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400

        data, errors = validators.validate_session_form(payload)
        if errors:
            return (
                jsonify({"error": "Invalid session.", "fields": errors}),
                400,
            )

        session_id = insert_session(
            data["subject"],
            data["minutes"],
            data["study_date"].isoformat(),
            tags=data["tags"],
        )
        return (
            jsonify(
                {
                    "id": session_id,
                    "subject": data["subject"],
                    "minutes": data["minutes"],
                    "study_date": data["study_date"].isoformat(),
                    "tags": data["tags"],
                }
            ),
            201,
        )

    # -----------------------------------------------------------------
    # CSRF exemption for the JSON API
    # -----------------------------------------------------------------
    # Why exempt it instead of making API clients send a token?  A CSRF
    # attack works by making a *browser* send a request the user did not mean
    # to send, riding on cookies the browser attaches automatically.  This
    # API authenticates nobody: it reads no cookies and needs no session, so
    # a cross-origin request that cannot read a cookie cannot do anything a
    # plain anonymous GET could not.  It also answers with JSON rather than
    # HTML, so it is not a target for "make the victim's browser submit this
    # form" tricks.
    #
    # The HTML forms are the opposite: they do carry a session cookie (for
    # flash messages) and they render HTML, so they stay protected.
    #
    # If you later add logins or cookie-based auth to the API, remove this
    # exemption and require the token in the X-CSRFToken header instead --
    # the README's API section documents how to send it.
    csrf.exempt(api_create_session)

    @app.errorhandler(404)
    def not_found(error):
        """A friendly page for unknown URLs and unknown session ids."""
        return render_template("404.html", description=error.description), 404

    @app.errorhandler(413)
    def request_too_large(error):
        """A request body over MAX_CONTENT_LENGTH, i.e. an oversized upload."""
        megabytes = MAX_IMPORT_BYTES // (1024 * 1024)
        return (
            render_template(
                "import_sessions.html",
                file_error=(
                    f"That file is larger than {megabytes} MB. "
                    "Split it into smaller files."
                ),
                rejected=[],
            ),
            413,
        )

    return app


if __name__ == "__main__":
    # Debug mode reloads the server when you edit a file and shows a
    # helpful traceback on errors. Never use it in production.
    create_app().run(debug=True)
