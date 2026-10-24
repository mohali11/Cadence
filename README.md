# Cadence

A small self-hosted study tracker. Log how many minutes you studied a subject
on a given day, see your totals, watch the last 8 weeks as a bar chart, and
export everything to CSV. One SQLite file, no accounts, no cloud.

## What it does

- **Log a session**: a subject, a number of minutes, and a date.
- **Dashboard**: total minutes and hours per subject, a bar chart of minutes
  per week for the last 8 weeks (weeks start on Monday), a table breaking
  those same weeks down by subject, and the 10 most recent sessions.
- **All sessions**: the full history with optional date, subject-search and
  tag filters, one page at a time, with Edit and Delete buttons on every row.
- **Tags**: comma-separated labels on any session, shown as chips in the
  history and usable as a filter.
- **Weekly goals**: one overall weekly target and optional per-subject
  targets, with this week's progress against each goal shown on the
  dashboard. Weeks start on Monday.
- **CSV export**: everything, or just the rows matching the current filters.
- **CSV import**: upload a CSV of sessions; every row is checked first and
  nothing is imported unless the whole file is clean. Rejected rows come
  back with their line numbers.
- **JSON API**: read the dashboard summary and add sessions from scripts.
- **A calm, responsive interface**: a sidebar on desktop that becomes a top
  bar with a floating "Log session" button on phones, summary cards, styled
  empty states, and a light/dark switch that remembers your choice. Icons,
  fonts and scripts are served from this repo, so pages make no external
  requests and the dashboard works offline.
- Subjects are stored exactly as typed (whitespace trimmed), but they are
  grouped case-insensitively: "math", "Math" and "MATH" count as one subject.

## Setup

Requirements: Python 3.9 or newer and pip. Node 18 or newer is only needed
if you want to run the JavaScript chart tests.

### Windows (PowerShell)

```powershell
cd study_tracker
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell refuses to run the activation script with an execution policy
error, allow it for this window only and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
cd study_tracker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Running the app

### Development server

```powershell
python app.py
```

Runs at http://127.0.0.1:5000 . Debug mode is on, so the server reloads when
you edit a file. Stop it with Ctrl+C.

### Production-style server (waitress)

```bash
waitress-serve --call app:create_app
```

waitress serves the same app at http://127.0.0.1:8080 . It is a real WSGI
server (no debug mode, no auto-reload) and is the command to use if you keep
the app running somewhere other than your own machine.

Both commands work the same in PowerShell and in a macOS/Linux shell; only
the venv activation step differs.

## Deploying

The pieces are in the repo; what is still missing is a host to run them on
(see "Still missing").

HOSTING.md walks through a full deployment on a server: installing the
requirements, setting `SECRET_KEY` and `DATABASE_PATH`, running waitress
(optionally as a systemd service), the Docker image, and an HTTPS reverse
proxy example.

### Docker

```powershell
docker build -t cadence .
docker run -d --name cadence -p 8080:8080 -e SECRET_KEY="<a long random string>" -v cadence-data:/data cadence
```

- The image runs the app with waitress (never the Flask dev server) as a
  non-root user, on port 8080.
- `SECRET_KEY` is required for a real deployment. Without it the container
  starts with the hardcoded dev-only key and says so in `docker logs`.
- `-v cadence-data:/data` is where `study.db` lives (`DATABASE_PATH` is set
  to `/data/cadence.db` in the image). Without the volume, a rebuilt
  container starts with an empty database.
- Check it with `docker logs cadence`, then open http://localhost:8080 .

### The parts around the container

A real deployment also needs the things this repo deliberately leaves out:

- **TLS.** Put a reverse proxy in front (Caddy gets certificates
  automatically; nginx with certbot works too) and have it forward to the
  container's port 8080.
- **Backups.** The whole database is the one file on the volume
  (`/data/cadence.db`). Copy it while the app is stopped, or use SQLite's
  `.backup` command.
- **A secret key that stays secret.** Generate one, for example with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`, and pass
  it in via `-e SECRET_KEY=...` or your host's secret store.

## Running the tests

Python tests:

```powershell
pytest -v
```

`tests/test_ui.py` covers the interface: every page renders (empty and with
data), the static assets are served, no page loads anything from an external
host, the dashboard's summary cards agree with the chart JSON, and the form
error states keep what was typed.

JavaScript tests (need Node 18+ and no npm install; they use only Node's
built-in test runner):

```powershell
node --test tests/weekly_chart.test.js
node --test tests/local_time.test.js
```

Chart render tests (need the dev-only npm packages from `package.json` --
chart.js 4.4.1, the same version vendored in
`static/vendor/chart.umd.min.js`, plus `@napi-rs/canvas`; the app itself
needs none of this):

```powershell
npm install
node --test tests/chart_render.test.js
```

The Python suite covers the validators, the SQLite storage and migrations,
the chart math, the JSON the dashboard embeds, and every route including
CSRF protection and the JSON API. The Node suites cover the browser code
pytest cannot run: the Chart.js options in `static/js/weekly-chart.js`, the
UTC-to-local formatting in `static/js/local-time.js`, and -- with the npm
packages installed -- the drawn chart itself, rendered onto a real canvas
and checked pixel by pixel in `tests/chart_render.test.js`.

## Configuration

The app reads two environment variables:

| Variable        | What it does                                                             | Default                              |
| --------------- | ------------------------------------------------------------------------ | ------------------------------------ |
| `SECRET_KEY`    | Signs session cookies and CSRF tokens. Set it to a long random string.  | A hardcoded dev-only fallback key.   |
| `DATABASE_PATH` | The SQLite file to use. Absolute, or relative to the folder you started the app from. | `study.db` next to `app.py`. |

Windows (PowerShell):

```powershell
$env:SECRET_KEY = "replace-with-a-long-random-string"
$env:DATABASE_PATH = "D:\data\cadence.db"
python app.py
```

macOS / Linux:

```bash
export SECRET_KEY="replace-with-a-long-random-string"
export DATABASE_PATH="$HOME/data/cadence.db"
python app.py
```

**Warning about `SECRET_KEY`:** if it is not set, the app falls back to a key
that is hardcoded in `app.py`. That is fine for development on your own
machine (it is what makes a fresh clone work with zero setup), but it must
never be used in production: anyone who can read the source knows the key, so
they could forge the cookies and CSRF tokens that this key is supposed to
protect. The app prints a warning at startup every time the fallback is in
use. The JSON API does not use cookies or tokens at all, so `SECRET_KEY`
does not affect it.

## Project layout

```
study_tracker/
├── app.py                  # Flask app factory and all routes
├── charts.py               # pure weekly-bucket math for the chart
├── database.py             # SQLite storage and schema migrations
├── validators.py           # form and JSON validation rules
├── requirements.txt        # Flask, Flask-WTF, waitress, pytest
├── pytest.ini              # puts the project root on sys.path for `pytest -v`
├── package.json            # dev-only Node packages for the render test
├── package-lock.json       # exact versions for the above
├── Dockerfile              # production-style container image
├── .dockerignore
├── README.md
├── HOSTING.md              # step-by-step deployment on a server
├── .gitignore
├── static/
│   ├── style.css           # all styling, design tokens, light and dark themes
│   ├── icons.svg           # the SVG icon sprite the templates reference
│   ├── logo.svg            # the mark, used for the tab icon and the header
│   ├── favicon.png         # 32px raster fallback for old browsers
│   ├── apple-touch-icon.png # 180px icon for iOS home screens
│   ├── vendor/
│   │   ├── chart.umd.min.js # Chart.js 4.4.1, vendored so the page is offline-safe
│   │   └── fonts/           # Inter (variable, SIL OFL) and its licence
│   └── js/
│       ├── weekly-chart.js # Chart.js config, tested with Node
│       ├── local-time.js   # UTC -> visitor's local time, tested with Node
│       └── ui.js           # theme switch, toasts, confirmations, dropzone
├── templates/
│   ├── base.html           # shared header, nav, flash messages
│   ├── dashboard.html      # totals, goals, chart, breakdown, recent
│   ├── sessions.html       # history with filters and paging
│   ├── new_session.html    # log form
│   ├── edit_session.html   # edit form
│   ├── goals.html          # weekly goal targets
│   ├── import_sessions.html # CSV upload and rejected rows
│   └── 404.html
└── tests/
    ├── conftest.py         # app/client fixtures and the add_session helper
    └── test_ui.py          # templates, static assets, dashboard numbers
```

`study.db` appears after the first run (it is gitignored), and `.venv/` is
your local virtual environment.

## API endpoints

The examples assume the app is running at http://127.0.0.1:5000 . The JSON
API is exempt from CSRF checks because it authenticates nobody and reads no
cookies, so no token is needed. The HTML pages (`/`, `/sessions`, the forms)
are browser views, not API endpoints.

### GET /api/summary

Returns the same numbers the dashboard shows: totals per subject, the
weekly minutes for the chart, and the same weeks broken down per subject.

curl (macOS/Linux):

```bash
curl http://127.0.0.1:5000/api/summary
```

PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/summary
```

Example response (shortened):

```json
{
  "totals_by_subject": [{"subject": "Math", "minutes": 120}],
  "weekly_minutes": [{"week_start": "2026-08-24", "label": "Aug 24", "minutes": 0}],
  "weekly_by_subject": {
    "weeks": [{"week_start": "2026-08-24", "label": "Aug 24"}],
    "series": [{"subject": "Math", "minutes": [0]}]
  },
  "weeks_on_chart": 8
}
```

`weekly_minutes` and `weekly_by_subject.weeks` always carry all 8 weeks
(oldest first, empty weeks are 0), and each series' `minutes` list lines up
entry for entry with `weekly_by_subject.weeks`.

### POST /api/sessions

Creates one session. The body must be a JSON object with `subject`
(1 to 50 characters), `minutes` (whole number, 1 to 1440), and `study_date`
(`YYYY-MM-DD`, today or earlier), plus an optional `tags` list (or a
comma-separated string; up to 10 tags, 30 characters each). A good request
answers `201` with the new row; a bad one answers `400` with an `error`
message and per-field messages.

curl (macOS/Linux):

```bash
curl -X POST http://127.0.0.1:5000/api/sessions \
  -H "Content-Type: application/json" \
  -d '{"subject": "Physics", "minutes": 60, "study_date": "2026-10-15", "tags": ["exam"]}'
```

PowerShell:

```powershell
$body = @{
  subject    = "Physics"
  minutes    = 60
  study_date = "2026-10-15"
  tags       = @("exam")
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/api/sessions `
  -ContentType "application/json" -Body $body
```

### GET /sessions/export.csv

Downloads the history as CSV. Accepts the same optional `start`, `end`,
`search` and `tag` filters as the `/sessions` page, and always exports all
matching rows (no paging). The columns are `id`, `subject`, `minutes`,
`study_date`, `created_at` (UTC) and `tags` (joined with `, `).

curl (macOS/Linux):

```bash
curl -o study-sessions.csv \
  "http://127.0.0.1:5000/sessions/export.csv?start=2026-10-01&end=2026-10-15"
```

PowerShell:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:5000/sessions/export.csv?start=2026-10-01&end=2026-10-15" `
  -OutFile study-sessions.csv
```

## Migrating existing data

An existing `study.db` is upgraded automatically when the app starts. There
is no manual migration step. Two things worth knowing:

- **Subjects are stored exactly as you typed them**, but the per-subject
  totals group case-insensitively with `LOWER()` in SQL. "math", "Math" and
  "MATH" become one total, and the label shown is the most recently logged
  spelling. Correcting the capitalization later updates the label without
  touching the stored rows.
- **Rows created before the app recorded timestamps** (anything written by
  the original schema) keep no `created_at` value. They show as `--` in the
  sessions list and as an empty cell in the CSV export. That is intentional:
  the app knows when those sessions were *studied*, not when they were typed
  in, and a made-up timestamp would look like real data. You do not need to
  fill them in; new rows always get a UTC timestamp automatically.

## Behavior notes

- Logging a session redirects to the dashboard, where the flash message
  confirms what was saved.
- Editing a session redirects to the history page.
- **Timestamps are stored in UTC and shown in your browser's local time.**
  `created_at` is written in UTC, the one zone that means the same instant
  to every reader. The sessions list and the edit page show it in the
  visitor's own zone with a small script (`static/js/local-time.js`), and
  fall back to the stored UTC text when JavaScript is off. The CSV export
  always keeps UTC.
- **Deleting a session always redirects to the history page** (`/sessions`),
  even when you pressed Delete on the dashboard. That is deliberate: the
  Delete button appears in both places, and the history page always shows
  the updated list, so the result of the delete is visible immediately.

## Still missing

Deliberately out of scope for now:

- **User accounts and login.** Design decision needed first.
- **A host to run on.** The image is ready, and the hosting guide now covers
  the steps. Actually picking and paying for a host is the user's decision.
- **A larger database.** SQLite supports one writer at a time. Move to
  Postgres only if many users need to write at once.
- **Reminders and notifications.** These need an external service.
