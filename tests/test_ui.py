"""Tests for the interface: the templates, the static assets and the
numbers the dashboard shows.

These are deliberately about things a broken redesign would break: a page
that answers 500, an asset path that 404s, a template that reaches out to a
CDN, or a summary card that disagrees with the chart data.
"""

import json
import re

import pytest

PAGES = ["/", "/sessions", "/sessions/new", "/goals", "/sessions/import"]


@pytest.fixture
def stocked(add_session):
    """A handful of sessions across two subjects and two weeks."""
    return [
        add_session("Mathematics", 45, "2026-10-19", tags=["exam", "chapter 3"]),
        add_session("Mathematics", 30, "2026-10-21"),
        add_session("Physics", 60, "2026-10-13", tags=["reading"]),
        add_session("physics", 15, "2026-10-14"),
    ]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_when_empty(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert b"<!doctype html>" in response.data.lower()


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_with_data(client, stocked, path):
    assert client.get(path).status_code == 200


def test_edit_page_renders(client, stocked):
    response = client.get("/sessions/1/edit")
    assert response.status_code == 200
    assert b"Save changes" in response.data


def test_unknown_page_uses_the_styled_404(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert b"Back to dashboard" in response.data


def test_pages_make_no_external_requests(client, stocked):
    """Every page must work offline: no CDN scripts, fonts or images."""
    for path in PAGES:
        html = client.get(path).data.decode()
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        assert external == [], f"{path} loads external assets: {external}"


@pytest.mark.parametrize(
    "asset",
    [
        "/static/style.css",
        "/static/icons.svg",
        "/static/logo.svg",
        "/static/favicon.png",
        "/static/apple-touch-icon.png",
        "/static/js/ui.js",
        "/static/js/weekly-chart.js",
        "/static/js/local-time.js",
        "/static/vendor/chart.umd.min.js",
        "/static/vendor/fonts/inter-latin-var.woff2",
    ],
)
def test_static_assets_are_served(client, asset):
    response = client.get(asset)
    assert response.status_code == 200
    assert len(response.data) > 0


def test_icon_sprite_defines_the_icons_the_templates_use(client):
    sprite = client.get("/static/icons.svg").data.decode()
    defined = set(re.findall(r'<symbol id="([^"]+)"', sprite))
    used = set()
    for path in PAGES:
        used.update(re.findall(r"icons\.svg#([\w-]+)", client.get(path).data.decode()))
    assert used, "no icons referenced"
    assert used <= defined, f"missing from the sprite: {sorted(used - defined)}"


def test_dashboard_cards_agree_with_the_chart(client, stocked):
    html = client.get("/").data.decode()
    payload = json.loads(
        re.search(r'id="weekly-chart-data">(.*?)</script>', html, re.S).group(1)
    )
    weekly = payload["weekly_minutes"]

    this_week = re.search(r'<span class="stat-label">This week</span>.*?'
                          r'<p class="stat-value">([^<]+)</p>', html, re.S).group(1)
    all_time = re.search(r'<span class="stat-label">All time</span>.*?'
                         r'<p class="stat-value">([^<]+)</p>', html, re.S).group(1)
    sessions = re.search(r'<span class="stat-label">Sessions</span>.*?'
                         r'<p class="stat-value">([^<]+)</p>', html, re.S).group(1)

    assert len(weekly) == payload["weeks_on_chart"] == 8
    assert sessions.replace(",", "") == str(len(stocked)) == "4"
    # This week's card is the last bucket of the chart, in hours-and-minutes
    # (45 + 30 minutes across the Monday of this week).  All time is every
    # stocked row: 45 + 30 + 60 + 15.
    assert weekly[-1]["minutes"] == 75
    assert this_week == "1h 15m"
    assert all_time == "2h 30m"


def test_dashboard_chart_data_matches_the_api(client, stocked):
    html = client.get("/").data.decode()
    embedded = json.loads(
        re.search(r'id="weekly-chart-data">(.*?)</script>', html, re.S).group(1)
    )
    api = client.get("/api/summary").get_json()
    assert embedded["weekly_minutes"] == api["weekly_minutes"]
    assert embedded["weekly_by_subject"] == api["weekly_by_subject"]


def test_empty_dashboard_offers_a_first_step(client):
    html = client.get("/").data.decode()
    assert "Welcome to Cadence" in html
    assert "Log your first session" in html
    assert 'id="weekly-chart-data"' not in html  # nothing to draw


def test_duration_filter_shapes():
    from app import create_app

    app = create_app({"TESTING": True, "DATABASE": ":memory:"})
    with app.app_context():
        duration = app.jinja_env.filters["duration"]
        assert duration(0) == "0m"
        assert duration(45) == "45m"
        assert duration(60) == "1h"
        assert duration(135) == "2h 15m"
        number = app.jinja_env.filters["num"]
        assert number(1234) == "1,234"
        assert number(7) == "7"


def test_new_session_form_keeps_what_was_typed_and_flags_errors(client):
    response = client.post(
        "/sessions/new",
        data={"subject": "", "minutes": "9999", "study_date": "2099-01-01",
              "tags": ""},
    )
    html = response.data.decode()
    assert response.status_code == 200
    assert 'aria-invalid="true"' in html
    assert 'class="field-error"' in html
    assert 'value="9999"' in html


def test_new_session_defaults_the_date_to_today(client):
    from datetime import date

    html = client.get("/sessions/new").data.decode()
    assert f'value="{date.today().isoformat()}"' in html


def test_sessions_page_marks_the_active_nav_link(client):
    html = client.get("/sessions").data.decode()
    assert 'aria-current="page"' in html


def test_delete_still_redirects_to_the_history(client, stocked):
    deleted_id = stocked[0]
    response = client.post(f"/sessions/{deleted_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert b"Session deleted" in response.data
    # The history page is what we land on, and the row is no longer in it.
    assert f"/sessions/{deleted_id}/edit".encode() not in response.data


def test_theme_toggle_and_scripts_are_wired_up(client):
    html = client.get("/").data.decode()
    assert 'id="theme-toggle"' in html
    assert "/static/js/ui.js" in html
    # The inline head script re-applies a saved choice before first paint.
    assert "cadence-theme" in html
