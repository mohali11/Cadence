"""Pure helpers that turn study-session rows into the numbers the dashboard draws.

There is deliberately no Flask, no database, and no printing here.  Every
function takes plain values and returns plain lists/dicts, which gives us:

* one source of truth -- the HTML page and GET /api/summary share the exact
  same numbers because they call the same functions;
* easy tests -- the tests can call these functions with hand-made rows,
  no web app or request needed.

A "row" is anything that supports key access: ``row["subject"]``,
``row["minutes"]`` and ``row["study_date"]``.  A ``dict`` works, and so does
a ``sqlite3.Row`` (what database.py returns).

What used to live here (before the 2026-10 pass) and where it went:

* ``total_minutes_by_subject`` -- summed minutes per subject in Python.
  That is *query shaping*, not chart data, so it was replaced by
  ``database.subject_totals()``, which groups with ``GROUP BY
  LOWER(subject)``.  Doing it in SQL is also what makes "math" and "Math"
  count as one subject.  Nothing in charts.py groups or filters rows now;
  it only turns rows it was handed into the exact shape a chart needs.
* ``weekly_window_start`` is still here because picking the window is chart
  logic (how many weeks the chart shows), not storage logic.
"""

from datetime import date, datetime, timedelta

DATE_FORMAT = "%Y-%m-%d"
WEEKS_ON_CHART = 8  # how many weeks the weekly bar chart covers


def parse_study_date(text):
    """'2026-10-12' -> datetime.date(2026, 10, 12).

    Dates reaching this point were already validated on the way in, so a
    strict strptime is fine here -- it is our own data coming back out.
    """
    return datetime.strptime(text, DATE_FORMAT).date()


def week_start(day):
    """The Monday of the week containing ``day``.

    ``date.weekday()`` returns 0 for Monday through 6 for Sunday, so
    subtracting it lands on the Monday.  That is why a Sunday session and
    the Monday right after it land in *different* weeks.
    """
    return day - timedelta(days=day.weekday())


def _this_monday(as_of=None):
    """Monday of the week that ``as_of`` (default: today) falls in."""
    today = as_of if as_of is not None else date.today()
    return week_start(today)


def weekly_window_start(weeks=WEEKS_ON_CHART, as_of=None):
    """First date the chart covers, as a 'YYYY-MM-DD' string.

    The chart shows the current (partial) week plus the ``weeks - 1`` weeks
    before it, so this is the Monday of the oldest week in the window.
    app.py uses it to ask the database for just those rows.
    """
    first_monday = _this_monday(as_of) - timedelta(weeks=weeks - 1)
    return first_monday.isoformat()


def weekly_minutes(rows, weeks=WEEKS_ON_CHART, as_of=None):
    """Minutes per Monday-start week for the last ``weeks`` weeks.

    Returns one entry per week, oldest first, and always returns *all*
    ``weeks`` buckets -- empty weeks are 0 rather than missing, so the
    chart's x-axis never shifts:

        [{"week_start": "2026-08-24", "label": "Aug 24", "minutes": 0}, ...]

    ``week_start`` is the Monday (used by the database query);
    ``label`` is the short human-readable form Chart.js draws;
    ``minutes`` is that week's total.

    ``as_of`` pins what "today" means, which keeps the tests deterministic.
    Rows older than the window are ignored; future rows cannot exist
    because the validators reject future dates.
    """
    this_monday = _this_monday(as_of)
    first_monday = this_monday - timedelta(weeks=weeks - 1)

    # Seed every Monday in the window with 0, including empty weeks.
    buckets = {}
    day = first_monday
    while day <= this_monday:
        buckets[day] = 0
        day += timedelta(weeks=1)

    # Fold each row into its week's bucket.  The membership test drops rows
    # that fall outside the window.
    for row in rows:
        bucket = week_start(parse_study_date(row["study_date"]))
        if bucket in buckets:
            buckets[bucket] += int(row["minutes"])

    return [
        {
            "week_start": week.isoformat(),
            "label": f"{week.strftime('%b')} {week.day}",
            "minutes": buckets[week],
        }
        for week in sorted(buckets)
    ]


def _row_id(row):
    """The row's id, or None when the caller handed rows without one."""
    try:
        return int(row["id"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def weekly_minutes_by_subject(rows, weeks=WEEKS_ON_CHART, as_of=None):
    """Minutes per week *per subject* for the last ``weeks`` weeks.

    The same bucketing as ``weekly_minutes``, but one series per subject
    instead of one total.  Subjects group case-insensitively, exactly like
    ``database.subject_totals``: the grouping key is the lowercased name
    and the label is the spelling from the newest row (highest id), so
    "math", "Math" and "MATH" share one series and the table keeps saying
    whatever you last called the subject.

    Returns ``{"weeks": [...], "series": [...]}``:

        {
          "weeks":  [{"week_start": "2026-08-24", "label": "Aug 24"}, ...],
          "series": [{"subject": "Math", "minutes": [0, 45, ...]}],
        }

    ``weeks`` is the shared x-axis (oldest first, every week present) and
    each series' ``minutes`` list lines up with it entry for entry.
    Series are ordered by total minutes across the window, largest first
    (ties A-Z), the same order the totals table uses.  Rows outside the
    window are ignored.
    """
    this_monday = _this_monday(as_of)
    first_monday = this_monday - timedelta(weeks=weeks - 1)
    week_starts = []
    day = first_monday
    while day <= this_monday:
        week_starts.append(day)
        day += timedelta(weeks=1)
    in_window = set(week_starts)

    series = {}
    for row in rows:
        bucket = week_start(parse_study_date(row["study_date"]))
        if bucket not in in_window:
            continue
        key = str(row["subject"]).lower()
        entry = series.get(key)
        if entry is None:
            entry = {
                "subject": row["subject"],
                "label_id": _row_id(row),
                "buckets": {week: 0 for week in week_starts},
            }
            series[key] = entry
        else:
            row_id = _row_id(row)
            if row_id is None:
                # No ids to compare (hand-made rows): rows arrive oldest
                # first, so the last spelling seen is the newest one.
                entry["subject"] = row["subject"]
            elif entry["label_id"] is None or row_id > entry["label_id"]:
                entry["subject"] = row["subject"]
                entry["label_id"] = row_id
        entry["buckets"][bucket] += int(row["minutes"])

    ordered = sorted(
        series.values(),
        key=lambda entry: (-sum(entry["buckets"].values()), entry["subject"]),
    )
    return {
        "weeks": [
            {
                "week_start": week.isoformat(),
                "label": f"{week.strftime('%b')} {week.day}",
            }
            for week in week_starts
        ],
        "series": [
            {
                "subject": entry["subject"],
                "minutes": [entry["buckets"][week] for week in week_starts],
            }
            for entry in ordered
        ],
    }


def goal_progress(minutes, goal):
    """Progress toward a weekly goal, as the dashboard draws it.

    ``percent`` is rounded and may exceed 100, because overshooting a goal
    is real information; ``bar_percent`` is capped at 100 because a bar
    cannot be wider than its track.  ``goal`` is always a positive integer
    by the time it gets here (validators.validate_goal_minutes), so there
    is no division-by-zero case.
    """
    percent = round(minutes * 100 / goal)
    return {
        "minutes": minutes,
        "goal": goal,
        "percent": percent,
        "bar_percent": min(100, percent),
    }


# ---------------------------------------------------------------------------
# Note on the Chart.js *options*
# ---------------------------------------------------------------------------
# The chart's colours, axis titles and scales are NOT built here.  They live
# in static/js/weekly-chart.js, next to the Chart.js call that uses them,
# and that module is tested by tests/weekly_chart.test.js with `node --test`.
# Python keeps its half of the contract testable too: tests/test_frontend.py
# parses the JSON that dashboard.html embeds and checks it matches what
# weekly_minutes() returned.  Keeping the Chart.js config in one place (the
# JS) instead of mirroring it in Python means the two can never disagree.

