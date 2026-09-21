"""
FR-9.7: the hold expiry governs, not the sweep.

The sweep runs every sixty seconds. Before this check existed, a lapsed
code still redeemed inside that gap — and with the hold now pinned thirty
minutes to a promised slot, that gap sits exactly where a late arrival
turns up.
"""
from datetime import datetime, timedelta

import pytest

from app import crud, models

from .conftest import iso_in


@pytest.fixture
def pending_reservation(client, db, organizer_headers, available_item):
    res = client.post(
        "/reservations",
        json={"item_id": available_item.id, "scheduled_pickup_at": iso_in(hours=2)},
        headers=organizer_headers,
    )
    assert res.status_code == 200, res.text
    return db.query(models.Reservation).one()


def _lapse(db, reservation, minutes_ago=1):
    """Push the hold into the past without running the sweep — the exact
    state FR-9.7 is about."""
    reservation.hold_expires_at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    db.commit()


def test_pickup_succeeds_before_hold_expires(
    client, db, staff_headers, pending_reservation, available_item
):
    res = client.post(f"/reservations/pickup/{pending_reservation.qr_code}", headers=staff_headers)
    assert res.status_code == 200, res.text
    assert res.json()["picked_up_at"] is not None

    db.refresh(pending_reservation)
    db.refresh(available_item)
    assert pending_reservation.status == models.ReservationStatus.PICKED_UP
    assert available_item.status == models.ItemStatus.PICKED_UP


def test_pickup_rejected_after_hold_expires_without_sweep(
    client, db, staff_headers, pending_reservation
):
    """The headline case. No sweep has run; the scan itself must refuse."""
    _lapse(db, pending_reservation)

    res = client.post(f"/reservations/pickup/{pending_reservation.qr_code}", headers=staff_headers)
    assert res.status_code == 409
    assert "lapsed" in res.text


def test_late_pickup_releases_item_back_to_available(
    client, db, staff_headers, pending_reservation, available_item
):
    """A refused late scan must also free the food, so the shelf and the
    screen agree without waiting for the next sweep tick."""
    _lapse(db, pending_reservation)

    client.post(f"/reservations/pickup/{pending_reservation.qr_code}", headers=staff_headers)

    db.refresh(pending_reservation)
    db.refresh(available_item)
    assert pending_reservation.status == models.ReservationStatus.EXPIRED
    assert available_item.status == models.ItemStatus.AVAILABLE


def test_pickup_twice_rejected_with_already_collected(
    client, staff_headers, pending_reservation
):
    first = client.post(
        f"/reservations/pickup/{pending_reservation.qr_code}", headers=staff_headers
    )
    assert first.status_code == 200

    second = client.post(
        f"/reservations/pickup/{pending_reservation.qr_code}", headers=staff_headers
    )
    assert second.status_code == 409
    assert "Already collected" in second.text


def test_pickup_of_cancelled_reservation_says_so(
    client, organizer_headers, staff_headers, pending_reservation
):
    cancelled = client.post(
        f"/reservations/{pending_reservation.id}/cancel", headers=organizer_headers
    )
    assert cancelled.status_code == 200

    res = client.post(f"/reservations/pickup/{pending_reservation.qr_code}", headers=staff_headers)
    assert res.status_code == 409
    assert "cancelled" in res.text.lower()


def test_pickup_requires_staff_role(client, organizer_headers, pending_reservation):
    """FR-9.4 — the party receiving the food cannot confirm its own
    pickup."""
    res = client.post(
        f"/reservations/pickup/{pending_reservation.qr_code}", headers=organizer_headers
    )
    assert res.status_code == 403


def test_unknown_code_returns_404(client, staff_headers):
    res = client.post("/reservations/pickup/not-a-real-token", headers=staff_headers)
    assert res.status_code == 404


def test_expire_stale_fires_thirty_minutes_after_the_slot(
    client, db, organizer_headers, available_item
):
    """The sweep's behaviour under the derived hold: still pending at the
    scheduled time, expired half an hour later."""
    client.post(
        "/reservations",
        json={"item_id": available_item.id, "scheduled_pickup_at": iso_in(hours=1)},
        headers=organizer_headers,
    )
    reservation = db.query(models.Reservation).one()
    slot = reservation.scheduled_pickup_at

    # Pretend the slot has just arrived.
    reservation.scheduled_pickup_at = datetime.utcnow()
    reservation.hold_expires_at = datetime.utcnow() + timedelta(minutes=30)
    db.commit()
    crud.expire_stale_reservations(db)
    db.refresh(reservation)
    assert reservation.status == models.ReservationStatus.PENDING

    # Now thirty-one minutes past it.
    reservation.hold_expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    crud.expire_stale_reservations(db)
    db.refresh(reservation)
    db.refresh(available_item)
    assert reservation.status == models.ReservationStatus.EXPIRED
    assert available_item.status == models.ItemStatus.AVAILABLE
    assert slot is not None
