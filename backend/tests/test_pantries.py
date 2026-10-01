"""
UC-03: organizer self-registration. Covers FR-7.1 (org + password in one
atomic registration), FR-7.6 (EIN format + uniqueness), and alt-flows
3a/3b/3c.
"""
from app import models

from .conftest import _make_pantry


def _payload(**overrides):
    base = {
        "org_name": "Third Street Pantry",
        "ein": "94-2960297",
        "address": "123 Third St",
        "phone": "555-0100",
        "contact_email": "coordinator@example.org",
        "password": "correct-horse-battery",
    }
    base.update(overrides)
    return base


def test_register_creates_pantry_and_working_login(client):
    res = client.post("/pantries", json=_payload())
    assert res.status_code == 200
    body = res.json()
    assert body["verified"] is False
    assert "password" not in body
    assert "password_hash" not in body

    login = client.post(
        "/auth/login",
        json={"email": "coordinator@example.org", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200


def test_coordinator_bound_to_new_pantry(client, db):
    res = client.post("/pantries", json=_payload())
    pantry_id = res.json()["id"]
    user = db.query(models.User).filter(models.User.email == "coordinator@example.org").one()
    assert user.role == models.UserRole.ORG_COORDINATOR
    assert user.pantry_id == pantry_id


def test_duplicate_email_conflict(client):
    client.post("/pantries", json=_payload())
    res = client.post("/pantries", json=_payload(ein="11-1111111"))
    assert res.status_code == 409


def test_duplicate_email_case_insensitive(client):
    client.post("/pantries", json=_payload(contact_email="dupe@example.org"))
    res = client.post("/pantries", json=_payload(contact_email="Dupe@Example.org", ein="11-1111111"))
    assert res.status_code == 409


def test_duplicate_ein_conflict(client, db):
    _make_pantry(db, "Existing Pantry", "existing@example.org", False)  # ein="12-3456789"
    res = client.post("/pantries", json=_payload(ein="12-3456789"))
    assert res.status_code == 409


def test_malformed_ein_missing_hyphen(client):
    res = client.post("/pantries", json=_payload(ein="942960297"))
    assert res.status_code == 422


def test_malformed_ein_too_few_digits(client):
    res = client.post("/pantries", json=_payload(ein="9-123456"))
    assert res.status_code == 422


def test_malformed_ein_letters(client):
    res = client.post("/pantries", json=_payload(ein="9A-296029B"))
    assert res.status_code == 422


def test_weak_password_rejected(client):
    res = client.post("/pantries", json=_payload(password="short1"))
    assert res.status_code == 422
