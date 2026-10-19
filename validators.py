"""Input rules shared by the HTML forms and the JSON API.

Every validator returns a ``(value, error)`` pair:

* good input -> ``(cleaned_value, None)``
* bad input  -> ``(None, "message shown to the user")``

Nothing in this module knows about Flask or requests, so the rules can be
tested by calling the functions directly -- no app, no browser, no server.

There are two small exceptions for values that have no single "right"
answer: the paging helpers below return a number instead of an error,
because a page number typed into a URL is not the user's mistake, it is
just something to fall back on.
"""

import csv
import io
import re
from datetime import date, datetime

DATE_FORMAT = "%Y-%m-%d"
MAX_SUBJECT_LENGTH = 50
MAX_MINUTES = 1440  # minutes in a day; a single session cannot beat that
MAX_WEEKLY_GOAL = 10080  # minutes in seven days; a weekly target cannot beat that
MAX_TAG_LENGTH = 30    # one tag
MAX_TAGS_PER_SESSION = 10  # how many tags a session may carry
MAX_SEARCH_LENGTH = 100    # the /sessions free-text search box

# An optional sign followed by digits, and nothing else.  "45" passes,
# "45.5" and "45.5 minutes" do not.
_INTEGER_RE = re.compile(r"[+-]?\d+")


def validate_subject(raw):
    """Required (after stripping whitespace), at most 50 characters."""
    subject = raw.strip() if isinstance(raw, str) else ""
    if not subject:
        return None, "Subject is required."
    if len(subject) > MAX_SUBJECT_LENGTH:
        return None, f"Subject must be {MAX_SUBJECT_LENGTH} characters or fewer."
    return subject, None


def validate_minutes(raw):
    """A whole number from 1 to MAX_MINUTES.

    Accepts an int or a digit string like "45" (that is what HTML forms
    send).  Rejects 0, negatives, 1441 and above, floats like 45.5, and
    text like "abc".
    """
    # In Python, bool is a subclass of int, so True would sneak through as
    # "1 minute" if we did not check it first.
    if isinstance(raw, bool):
        return None, "Minutes must be a whole number."
    if isinstance(raw, int):
        minutes = raw
    elif isinstance(raw, str) and _INTEGER_RE.fullmatch(raw.strip()):
        minutes = int(raw)
    else:
        return None, "Minutes must be a whole number."
    if minutes <= 0:
        return None, "Minutes must be greater than 0."
    if minutes > MAX_MINUTES:
        return None, f"Minutes must be {MAX_MINUTES} or less."
    return minutes, None


def validate_study_date(raw, today=None):
    """Must parse exactly as YYYY-MM-DD, and must not be in the future.

    Returns a ``datetime.date``; callers store it with ``.isoformat()``,
    which also normalises loose input like "2026-1-5" to "2026-01-05".
    """
    if not isinstance(raw, str):
        return None, "Study date is required."
    try:
        # strptime is strict about impossible days: 2026-02-30 raises
        # ValueError and is rejected, while a real date is accepted.
        parsed = datetime.strptime(raw.strip(), DATE_FORMAT).date()
    except ValueError:
        return None, "Study date must be a real date in YYYY-MM-DD format."
    if parsed > (today if today is not None else date.today()):
        return None, "Study date cannot be in the future."
    return parsed, None


def validate_optional_date(raw, today=None):
    """Like ``validate_study_date``, but an empty box is allowed.

    The /sessions filters are optional: no start date means "no lower
    bound", not "today".  Only *present but wrong* values are an error.
    Returns ``(date_or_None, error_or_None)``.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    return validate_study_date(raw, today=today)


def validate_date_range(start, end):
    """A start date must not come after the end date.

    Takes the two already-parsed dates (either may be ``None``) so the
    comparison cannot fail on bad input.  Returns an error message or None.
    """
    if start is not None and end is not None and start > end:
        return "The start date must be on or before the end date."
    return None


def validate_page_number(raw, default=1):
    """A page number from the URL: a whole number of at least 1.

    ``?page=2`` -> 2.  ``?page=abc``, ``?page=-3``, ``?page=0`` and a
    missing parameter all fall back to ``default``, so a hand-typed or
    stale link cannot break the list.
    """
    try:
        page = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return page if page >= 1 else default


def validate_per_page(raw, default, choices):
    """A page size from the URL, restricted to the offered choices.

    Only the values the page itself offers are accepted (``choices``);
    anything else falls back to ``default``.  That keeps a URL like
    ``?per_page=100000`` from asking SQLite for the whole table at once.
    """
    try:
        per_page = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return per_page if per_page in choices else default


def validate_tags(raw):
    """Optional tags, as a comma-separated string or a list (JSON API).

    Returns ``([tag, ...], None)``; a missing or blank value means "no
    tags" and comes back as an empty list, not an error.  Tags are trimmed,
    empty pieces are dropped, and duplicates are removed
    case-insensitively (the first spelling wins); the stored spelling stays
    exactly as typed, the same way subjects are handled.
    """
    if raw is None:
        return [], None
    if isinstance(raw, str):
        pieces = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        pieces = list(raw)
    else:
        return None, "Tags must be text."

    tags = []
    seen = set()
    for piece in pieces:
        if not isinstance(piece, str):
            return None, "Tags must be text."
        tag = piece.strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            return None, f"Each tag must be {MAX_TAG_LENGTH} characters or fewer."
        folded = tag.lower()
        if folded in seen:
            continue
        seen.add(folded)
        tags.append(tag)
    if len(tags) > MAX_TAGS_PER_SESSION:
        return None, f"Use {MAX_TAGS_PER_SESSION} tags or fewer."
    return tags, None


def validate_search(raw):
    """The /sessions free-text search box: optional, length-capped.

    Blank means "no search", not an error.  Returns ``(text | None, error)``.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if not isinstance(raw, str):
        return None, "Search must be text."
    text = raw.strip()
    if len(text) > MAX_SEARCH_LENGTH:
        return None, f"Search must be {MAX_SEARCH_LENGTH} characters or fewer."
    return text, None


def validate_tag_filter(raw):
    """The /sessions tag filter: one optional tag, same cap as a stored tag."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if not isinstance(raw, str):
        return None, "Tag must be text."
    tag = raw.strip()
    if len(tag) > MAX_TAG_LENGTH:
        return None, f"Tag must be {MAX_TAG_LENGTH} characters or fewer."
    return tag, None


def validate_goal_minutes(raw):
    """A weekly goal: whole number 1..MAX_WEEKLY_GOAL, or blank to clear.

    Same style of rules as ``validate_minutes``, including accepting digit
    strings from HTML forms.  A blank value is not an error here: on the
    /goals form it is how a goal gets removed, so blank means "no goal",
    which is different from an invalid number.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if isinstance(raw, bool):
        return None, "Weekly goal must be a whole number."
    if isinstance(raw, int):
        goal = raw
    elif isinstance(raw, str) and _INTEGER_RE.fullmatch(raw.strip()):
        goal = int(raw)
    else:
        return None, "Weekly goal must be a whole number."
    if goal <= 0:
        return None, "Weekly goal must be greater than 0."
    if goal > MAX_WEEKLY_GOAL:
        return None, f"Weekly goal must be {MAX_WEEKLY_GOAL} or less."
    return goal, None


def validate_goals_form(form):
    """Validate the whole /goals form and collect every error.

    The form carries one ``overall`` field, then pairs of hidden
    ``subject_<i>`` fields (subject names, always rendered by the server)
    and ``goal_<i>`` inputs.  Pairs are read until the first missing hidden
    subject, so holes in a hand-crafted form cannot smuggle a goal past
    validation.  Blank goal fields are simply absent from ``subjects``,
    which is how the save clears them.

    Returns ``(data, errors)`` where ``data`` is
    ``{"overall": int | None, "subjects": {subject: int}}`` and ``errors``
    maps ``"overall"`` or a subject label to a message.
    """
    overall, overall_error = validate_goal_minutes(form.get("overall"))
    errors = {}
    if overall_error:
        errors["overall"] = overall_error

    subjects = {}
    index = 0
    while f"subject_{index}" in form:
        name, name_error = validate_subject(form.get(f"subject_{index}"))
        minutes, minutes_error = validate_goal_minutes(form.get(f"goal_{index}"))
        label = name or f"row {index + 1}"
        if name_error:
            errors[label] = name_error
        elif minutes_error:
            errors[label] = minutes_error
        elif minutes is not None:
            subjects[name] = minutes
        index += 1

    return {"overall": overall, "subjects": subjects}, errors


def validate_session_form(form, today=None):
    """Validate a whole form or JSON payload and collect every error.

    ``form`` is any mapping (``request.form`` or the dict from
    ``request.get_json()``).  Returns ``(data, errors)``:

    * ``data``   -- cleaned values: subject, minutes, study_date (a date)
    * ``errors`` -- ``{"subject": message, ...}`` for the fields that failed

    ``errors`` is empty exactly when ``data`` is safe to store.
    """
    subject, subject_error = validate_subject(form.get("subject"))
    minutes, minutes_error = validate_minutes(form.get("minutes"))
    study_date, date_error = validate_study_date(form.get("study_date"), today=today)
    tags, tags_error = validate_tags(form.get("tags"))

    errors = {
        name: message
        for name, message in (
            ("subject", subject_error),
            ("minutes", minutes_error),
            ("study_date", date_error),
            ("tags", tags_error),
        )
        if message is not None
    }

    data = {
        "subject": subject,
        "minutes": minutes,
        "study_date": study_date,
        "tags": tags,
    }
    return data, errors


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------

CSV_REQUIRED_COLUMNS = ("subject", "minutes", "study_date")
CSV_OPTIONAL_COLUMNS = ("created_at", "tags")

# The exact shape database.utc_now_iso() writes: 2026-10-15T09:30:04Z.
_CREATED_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def validate_csv_header(fieldnames):
    """Map a CSV header row to session fields, or say what is missing.

    Returns ``(mapping, error)`` where ``mapping`` is
    ``{canonical field: the file's spelling}`` for every column the file
    provides.  Names are compared after trimming, dropping a leading BOM
    and lowercasing, so Excel's "Subject" and a UTF-8 BOM both work.
    Required columns: subject, minutes, study_date.  Optional: created_at,
    tags.  Anything else (the export's ``id`` column, for one) is ignored,
    which is what makes the app's own export import back cleanly.
    """
    if not fieldnames:
        return None, (
            "The file needs a header row with subject, minutes and study_date."
        )

    mapping = {}
    for raw_name in fieldnames:
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip().lstrip("\ufeff").lower()
        if name in CSV_REQUIRED_COLUMNS + CSV_OPTIONAL_COLUMNS:
            if name in mapping:
                return None, f"The header has more than one {name} column."
            mapping[name] = raw_name

    missing = [name for name in CSV_REQUIRED_COLUMNS if name not in mapping]
    if missing:
        return None, (
            "The header must include subject, minutes and study_date. "
            f"Missing: {', '.join(missing)}."
        )
    return mapping, None


def validate_optional_created_at(raw):
    """The CSV ``created_at`` column: blank, or a UTC stamp like the app writes.

    Stored rows use ``YYYY-MM-DDTHH:MM:SSZ`` (see database.utc_now_iso), and
    the export writes exactly that; the importer accepts that shape or an
    empty cell and nothing else.  Returns ``(value | None, error)``.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if not isinstance(raw, str) or not _CREATED_AT_RE.fullmatch(raw.strip()):
        return None, "Created at must look like 2026-10-15T09:30:04Z."
    return raw.strip(), None


def validate_sessions_csv(text):
    """Validate a whole CSV file of sessions; every row or no rows.

    Returns ``(parsed, rejected, error)``:

    * ``parsed``   -- rows ready for ``database.insert_sessions``, as
      ``(subject, minutes, study_date, created_at, tags)`` tuples.
    * ``rejected`` -- ``[{"line": n, "messages": "field: message; ..."}]``
      for each data row that failed; line numbers match the file.
    * ``error``    -- a whole-file problem (bad header, no data rows), or
      ``None``.

    A file with an ``error`` or any ``rejected`` rows imports nothing, so
    the caller can hand back one clear "fix these lines" page.  Blank lines
    are skipped by the csv module.  Column order does not matter.
    """
    reader = csv.DictReader(io.StringIO(text))
    mapping, header_error = validate_csv_header(reader.fieldnames)
    if header_error:
        return [], [], header_error

    parsed = []
    rejected = []
    for row in reader:
        values = {name: row.get(source) for name, source in mapping.items()}
        data, errors = validate_session_form(values)
        created_at, created_error = validate_optional_created_at(
            values.get("created_at")
        )
        if created_error:
            errors["created_at"] = created_error
        if errors:
            rejected.append(
                {
                    "line": reader.line_num,
                    "messages": "; ".join(
                        f"{field}: {message}" for field, message in errors.items()
                    ),
                }
            )
            continue
        parsed.append(
            (
                data["subject"],
                data["minutes"],
                data["study_date"].isoformat(),
                created_at,
                data["tags"],
            )
        )

    if not parsed and not rejected:
        return [], [], "The file has a header but no data rows."
    return parsed, rejected, None
