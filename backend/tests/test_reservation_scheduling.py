"""
FR-8.6: an organization states when it will arrive, and the hold is
derived from that slot.

The timezone cases carry the most weight here. A naive datetime read as
UTC is a seven-hour error in California, and it does not fail loudly — it
produces a reservation whose hold lapsed before it was created. These
tests exist so that mistake cannot reach the shelf.
"""
from datetime import datetime, timedelta, timezone

from app import models, schemas

from .conftest import iso_in


def _reserve(client, headers, item_id, scheduled):
    return client.post(
        "/reservations",
        json={"item_id": item_id, "scheduled_pickup_at": scheduled},
        headers=headers,
    )


def test_reserve_requires_scheduled_pickup_at(client, organizer_headers, available_item):
    res = client.post(
        "/reservations", json={"item_id": available_item.id}, headers=organizer_headers
    )
    assert res.status_code == 422
    assert any(e["loc"][-1] == "scheduled_pickup_at" for e in res.json()["detail"])


def test_reserve_rejects_naive_datetime(client, organizer_headers, available_item):
    """The guard that matters most: no offset means the browser sent local
    wall time without converting it."""
    naive = (datetime.utcnow() + timedelta(hours=2)).isoformat()  # no tzinfo
    res = _reserve(client, organizer_headers, available_item.id, naive)
    assert res.status_code == 422


def test_reserve_accepts_offset_datetime_and_stores_naive_utc(
    client, db, organizer_headers, available_item
):
    """A non-UTC offset must be converted, not truncated. +05:30 sent as
    14:00 is 08:30 UTC; storing 14:00 would be five and a half hours of
    silent drift."""
    slot_utc = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(microsecond=0)
    slot_india = slot_utc.astimezone(timezone(timedelta(hours=5, minutes=30)))

    res = _reserve(client, organizer_headers, available_item.id, slot_india.isoformat())
    assert res.status_code == 200, res.text

    stored = db.query(models.Reservation).one()
    assert stored.scheduled_pickup_at.tzinfo is None
    expected = slot_utc.replace(tzinfo=None)
    assert abs((stored.scheduled_pickup_at - expected).total_seconds()) < 2


def test_reserve_rejects_time_in_the_past(client, organizer_headers, available_item):
    res = _reserve(client, organizer_headers, available_item.id, iso_in(hours=-1))
    assert res.status_code == 422
    assert "already passed" in res.text


def test_reserve_accepts_time_just_inside_skew_tolerance(
    client, organizer_headers, available_item
):
    """A minute in the past is within SUBMIT_SKEW — a slow submission, not
    a bad time."""
    res = _reserve(client, organizer_headers, available_item.id, iso_in(minutes=-1))
    assert res.status_code == 200, res.text


def test_reserve_rejects_more_than_24h_ahead(client, organizer_headers, available_item):
    res = _reserve(client, organizer_headers, available_item.id, iso_in(hours=25))
    assert res.status_code == 422
    assert "24 hours" in res.text


def test_reserve_accepts_exactly_24h_ahead(client, organizer_headers, available_item):
    """"Up to 24 hours" is inclusive."""
    res = _reserve(client, organizer_headers, available_item.id, iso_in(hours=24))
    assert res.status_code == 200, res.text


def test_hold_expires_thirty_minutes_after_scheduled_pickup(
    client, db, organizer_headers, available_item
):
    """The core rule."""
    res = _reserve(client, organizer_headers, available_item.id, iso_in(hours=4))
    assert res.status_code == 200, res.text

    stored = db.query(models.Reservation).one()
    assert stored.hold_expires_at - stored.scheduled_pickup_at == schemas.PICKUP_GRACE


def test_reserve_rejects_slot_after_item_discard_after(
    client, db, organizer_headers, available_item
):
    """An overnight booking on short-dated produce would otherwise be
    discarded by the sweep before anyone arrived."""
    available_item.discard_after = datetime.utcnow() + timedelta(hours=6)
    db.commit()

    res = _reserve(client, organizer_headers, available_item.id, iso_in(hours=12))
    assert res.status_code == 422
    assert "off the shelf" in res.text

    # The claim must be rolled back — a rejected reservation cannot leave
    # the item stranded in RESERVED with nothing pointing at it.
    db.refresh(available_item)
    assert available_item.status == models.ItemStatus.AVAILABLE
    assert db.query(models.Reservation).count() == 0


def test_reserve_allows_slot_before_item_discard_after(
    client, db, organizer_headers, available_item
):
    available_item.discard_after = datetime.utcnow() + timedelta(hours=6)
    db.commit()

    res = _reserve(client, organizer_headers, available_item.id, iso_in(hours=4))
    assert res.status_code == 200, res.text


def test_hold_minutes_in_body_is_ignored(client, db, organizer_headers, available_item):
    """The old field is gone. An old client sending it must not get a hold
    derived from it."""
    res = client.post(
        "/reservations",
        json={
            "item_id": available_item.id,
            "scheduled_pickup_at": iso_in(hours=2),
            "hold_minutes": 999,
        },
        headers=organizer_headers,
    )
    assert res.status_code == 200, res.text
    stored = db.query(models.Reservation).one()
    assert stored.hold_expires_at - stored.scheduled_pickup_at == schemas.PICKUP_GRACE


def test_reserve_unverified_pantry_still_403(
    client, unverified_organizer_headers, available_item
):
    res = _reserve(client, unverified_organizer_headers, available_item.id, iso_in(hours=2))
    assert res.status_code == 403
    assert "verification" in res.text


def test_second_reserve_on_same_item_gets_409(
    client, organizer_headers, other_organizer_headers, available_item
):
    first = _reserve(client, organizer_headers, available_item.id, iso_in(hours=2))
    assert first.status_code == 200, first.text

    second = _reserve(client, other_organizer_headers, available_item.id, iso_in(hours=3))
    assert second.status_code == 409


def test_reserve_marks_item_reserved_and_mints_a_code(
    client, db, organizer_headers, available_item
):
    res = _reserve(client, organizer_headers, available_item.id, iso_in(hours=2))
    assert res.status_code == 200, res.text

    body = res.json()
    assert body["qr_code"]
    assert body["scheduled_pickup_at"] is not None
    assert body["item_name"] == "Test Milk 2L"

    db.refresh(available_item)
    assert available_item.status == models.ItemStatus.RESERVED
