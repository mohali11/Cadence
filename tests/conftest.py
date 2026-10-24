"""Shared pytest fixtures for the Cadence test suite.

Each test gets a brand-new SQLite file in pytest's temporary directory, so
tests never see each other's rows and the real ``study.db`` is never
touched.  CSRF protection is switched off for the test app: the templates
do render a token, but typing one by hand in every POST would only test
Flask-WTF rather than this app.
"""

import pytest

from app import create_app


@pytest.fixture
def app(tmp_path):
    """A Cadence app pointed at an empty, temporary database."""
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "DATABASE": str(tmp_path / "test.db"),
            "WTF_CSRF_ENABLED": False,
        }
    )


@pytest.fixture
def client(app):
    """A test client for the app fixture."""
    return app.test_client()


@pytest.fixture
def add_session(app):
    """Insert a session straight into the database (bypassing the form)."""

    def _add(subject, minutes, study_date, tags=None):
        from database import insert_session

        with app.app_context():
            return insert_session(subject, minutes, study_date, tags=tags)

    return _add
