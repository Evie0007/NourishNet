"""
Shared fixtures for the backend suite.

The environment has to be set before anything under `app` is imported.
`app.database` reads DATABASE_URL at import time and builds the engine
immediately, `app.scheduler` reads SWEEP_ENABLED at import time, and
`app.main` calls `create_all` at import time. Importing first and
configuring after gives you a suite that quietly runs against the
development database.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

# A file, not :memory:. Each pooled connection to an in-memory SQLite gets
# its own private database, and SessionLocal opens several — the tables
# the fixtures create would be invisible to the request handling them.
_TMP_DIR = tempfile.mkdtemp(prefix="nourishnet-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/test.db"

# The sweep would otherwise race the assertions: several tests here put a
# reservation deliberately past its hold and then check that *the scan*
# expires it, which a background sweep would do first.
os.environ["SWEEP_ENABLED"] = "false"

# Without this, auth.py generates an ephemeral secret and warns.
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-anywhere-real")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    """Drop and recreate between tests. These tests assert on counts and on
    single-row lookups, so leakage between them is the kind of failure that
    only shows up when the suite is reordered."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    """No `with` block on purpose: entering the context manager runs the
    lifespan, which starts the background scheduler. The suite drives the
    sweep explicitly where it needs it."""
    return TestClient(app)


def _make_user(db, email, role, pantry=None):
    user = models.User(
        email=email,
        password_hash=auth.hash_password("test-password"),
        full_name=email.split("@")[0],
        role=role,
        pantry_id=pantry.id if pantry else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _bearer(user):
    token, _ = auth.create_access_token(user)
    return {"Authorization": f"Bearer {token}"}


def _make_pantry(db, name, email, verified):
    pantry = models.Pantry(
        org_name=name,
        ein="12-3456789",
        contact_email=email,
        verified=verified,
    )
    db.add(pantry)
    db.commit()
    db.refresh(pantry)
    return pantry


@pytest.fixture
def verified_pantry(db):
    return _make_pantry(db, "Second Harvest Test", "verified@example.org", True)


@pytest.fixture
def staff_headers(db):
    return _bearer(_make_user(db, "staff@example.com", models.UserRole.STAFF))


@pytest.fixture
def manager_headers(db):
    return _bearer(_make_user(db, "manager@example.com", models.UserRole.MANAGER))


@pytest.fixture
def organizer_headers(db, verified_pantry):
    return _bearer(
        _make_user(db, "organizer@example.org", models.UserRole.ORG_COORDINATOR, verified_pantry)
    )


@pytest.fixture
def unverified_organizer_headers(db):
    pantry = _make_pantry(db, "Pending Pantry", "pending@example.org", False)
    return _bearer(
        _make_user(db, "pending@example.org", models.UserRole.ORG_COORDINATOR, pantry)
    )


@pytest.fixture
def other_organizer_headers(db):
    """A second verified organization, for the isolation tests (FR-2.4)."""
    pantry = _make_pantry(db, "Hope Kitchen Test", "hope@example.org", True)
    return _bearer(
        _make_user(db, "hope@example.org", models.UserRole.ORG_COORDINATOR, pantry)
    )


@pytest.fixture
def available_item(db):
    """An AVAILABLE item with a discard deadline comfortably past the 24h
    booking horizon, so it does not accidentally constrain tests that are
    about something else."""
    item = models.Item(
        name="Test Milk 2L",
        category="Dairy",
        status=models.ItemStatus.AVAILABLE,
        sell_by_date=datetime.utcnow() + timedelta(days=3),
        discard_after=datetime.utcnow() + timedelta(days=5),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def iso_in(**delta):
    """An offset-aware RFC-3339 string `delta` from now — the shape the
    browser sends. Tests that care about the naive/aware distinction build
    their own string instead."""
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()
