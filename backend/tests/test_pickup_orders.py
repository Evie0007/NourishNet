"""
FR-8.13: several items, one trip, one QR code.

An order is a set of ordinary reservations that share a code. These tests pin
down the parts that are easy to get quietly wrong: the all-or-nothing claim,
the single scan completing every item, and a second scan or a late arrival
being reported rather than half-applied.
"""
from datetime import datetime, timedelta

import pytest

from app import models

from .conftest import _bearer, _make_pantry, _make_user, default_store, iso_in


def _item(db, store, name, **overrides):
    item = models.Item(
        store_id=store.id,
        name=name,
        category="Produce",
        status=models.ItemStatus.AVAILABLE,
        sell_by_date=datetime.utcnow() + timedelta(days=3),
        discard_after=datetime.utcnow() + timedelta(days=5),
        **overrides,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def three_items(db, store):
    return [_item(db, store, n) for n in ("Apples 3lb", "Carrots 2lb", "Bagels 6ct")]


def _order(client, headers, items, **overrides):
    body = {
        "item_ids": [i.id for i in items],
        "scheduled_pickup_at": iso_in(hours=2),
        **overrides,
    }
    return client.post("/reservations/orders", json=body, headers=headers)


def _statuses(db, items):
    for item in items:
        db.refresh(item)
    return [i.status for i in items]


# ---------- Creating an order ----------

def test_an_order_reserves_every_item_under_one_code(client, db, organizer_headers, three_items):
    res = _order(client, organizer_headers, three_items)

    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 3
    codes = {r["order_qr_code"] for r in rows}
    assert len(codes) == 1 and None not in codes
    assert len({r["order_id"] for r in rows}) == 1
    # The order owns the code; the items have none of their own to show.
    assert all(r["qr_code"] is None for r in rows)
    assert sorted(rows[0]["order_item_names"]) == sorted(i.name for i in three_items)
    assert _statuses(db, three_items) == [models.ItemStatus.RESERVED] * 3


def test_the_order_shares_one_slot_and_hold(client, organizer_headers, three_items):
    rows = _order(client, organizer_headers, three_items).json()

    assert len({r["scheduled_pickup_at"] for r in rows}) == 1
    assert len({r["hold_expires_at"] for r in rows}) == 1


def test_one_unavailable_item_reserves_nothing(
    client, db, organizer_headers, other_organizer_headers, three_items
):
    """All or nothing: the pantry must not be left holding part of a trip."""
    taken = client.post(
        "/reservations",
        json={"item_id": three_items[1].id, "scheduled_pickup_at": iso_in(hours=1)},
        headers=other_organizer_headers,
    )
    assert taken.status_code == 200

    res = _order(client, organizer_headers, three_items)

    assert res.status_code == 409
    assert "Carrots 2lb" in res.json()["detail"]
    assert db.query(models.PickupOrder).count() == 0
    assert _statuses(db, three_items) == [
        models.ItemStatus.AVAILABLE,
        models.ItemStatus.RESERVED,  # the other pantry's, untouched
        models.ItemStatus.AVAILABLE,
    ]


def test_items_from_two_stores_are_refused(client, db, organizer_headers, three_items, other_store):
    elsewhere = _item(db, other_store, "Yogurt")

    res = _order(client, organizer_headers, [three_items[0], elsewhere])

    assert res.status_code == 422
    assert "one location" in res.json()["detail"]
    assert _statuses(db, [three_items[0], elsewhere]) == [models.ItemStatus.AVAILABLE] * 2


def test_a_slot_past_any_items_discard_time_is_refused(client, db, organizer_headers, three_items):
    three_items[2].discard_after = datetime.utcnow() + timedelta(hours=1)
    db.commit()

    res = _order(client, organizer_headers, three_items)

    assert res.status_code == 422
    assert "Bagels 6ct" in res.json()["detail"]
    assert _statuses(db, three_items) == [models.ItemStatus.AVAILABLE] * 3


def test_an_order_needs_at_least_one_item(client, organizer_headers):
    res = client.post(
        "/reservations/orders",
        json={"item_ids": [], "scheduled_pickup_at": iso_in(hours=2)},
        headers=organizer_headers,
    )
    assert res.status_code == 422


def test_the_same_item_twice_is_refused(client, organizer_headers, three_items):
    res = _order(client, organizer_headers, [three_items[0], three_items[0]])
    assert res.status_code == 422


def test_an_order_uses_the_same_pickup_window_as_a_single_reservation(
    client, organizer_headers, three_items
):
    assert _order(client, organizer_headers, three_items, scheduled_pickup_at=iso_in(hours=30)).status_code == 422
    assert _order(client, organizer_headers, three_items, scheduled_pickup_at=iso_in(hours=-3)).status_code == 422


def test_an_unverified_organization_cannot_order(client, unverified_organizer_headers, three_items):
    res = _order(client, unverified_organizer_headers, three_items)
    assert res.status_code == 403


def test_store_staff_cannot_create_an_order(client, staff_headers, three_items):
    assert _order(client, staff_headers, three_items).status_code == 403


# ---------- Scanning the order ----------

@pytest.fixture
def placed(client, db, organizer_headers, three_items):
    rows = _order(client, organizer_headers, three_items).json()
    return db.query(models.PickupOrder).one(), rows[0]["order_qr_code"]


def test_one_scan_hands_over_every_item(client, db, staff_headers, placed, three_items):
    order, code = placed

    res = client.post(f"/reservations/pickup/{code}", headers=staff_headers)

    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(body["order_item_names"]) == sorted(i.name for i in three_items)
    assert body["picked_up_at"] is not None
    assert _statuses(db, three_items) == [models.ItemStatus.PICKED_UP] * 3
    assert all(r.status == models.ReservationStatus.PICKED_UP for r in order.reservations)


def test_each_item_gets_its_own_donation_record(client, db, staff_headers, placed, three_items):
    """The audit trail is per item whether it left alone or in an order."""
    _, code = placed
    client.post(f"/reservations/pickup/{code}", headers=staff_headers)

    names = sorted(r.item_name for r in db.query(models.DonationRecord).all())
    assert names == sorted(i.name for i in three_items)


def test_scanning_twice_reports_it_was_already_collected(client, staff_headers, placed):
    _, code = placed
    client.post(f"/reservations/pickup/{code}", headers=staff_headers)

    again = client.post(f"/reservations/pickup/{code}", headers=staff_headers)

    assert again.status_code == 409
    assert "Already collected" in again.json()["detail"]


def test_a_late_scan_expires_the_whole_order(client, db, staff_headers, placed, three_items):
    """FR-9.7 for an order: the hold governs, not the sweep, and every item
    goes back to the pool, not just the first."""
    order, code = placed
    order.hold_expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    res = client.post(f"/reservations/pickup/{code}", headers=staff_headers)

    assert res.status_code == 409
    assert "lapsed" in res.json()["detail"]
    assert _statuses(db, three_items) == [models.ItemStatus.AVAILABLE] * 3


def test_another_stores_staff_cannot_redeem_the_order(
    client, other_store_staff_headers, placed
):
    _, code = placed
    res = client.post(f"/reservations/pickup/{code}", headers=other_store_staff_headers)
    assert res.status_code == 404


def test_an_organizer_cannot_confirm_its_own_order(client, organizer_headers, placed):
    _, code = placed
    assert client.post(f"/reservations/pickup/{code}", headers=organizer_headers).status_code == 403


def test_an_item_cancelled_beforehand_does_not_block_the_rest(
    client, db, staff_headers, organizer_headers, placed, three_items
):
    order, code = placed
    dropped = order.reservations[0]
    client.post(f"/reservations/{dropped.id}/cancel", headers=organizer_headers)

    res = client.post(f"/reservations/pickup/{code}", headers=staff_headers)

    assert res.status_code == 200
    assert len(res.json()["order_item_names"]) == 2
    assert db.query(models.DonationRecord).count() == 2


# ---------- Cancelling the order ----------

def test_cancelling_an_order_returns_every_item_to_the_pool(
    client, db, organizer_headers, placed, three_items
):
    order, _ = placed

    res = client.post(f"/reservations/orders/{order.id}/cancel", headers=organizer_headers)

    assert res.status_code == 200
    assert {r["status"] for r in res.json()} == {"cancelled"}
    assert _statuses(db, three_items) == [models.ItemStatus.AVAILABLE] * 3


def test_cancelled_order_code_no_longer_redeems(client, staff_headers, organizer_headers, placed):
    order, code = placed
    client.post(f"/reservations/orders/{order.id}/cancel", headers=organizer_headers)

    res = client.post(f"/reservations/pickup/{code}", headers=staff_headers)

    assert res.status_code == 409
    assert "cancelled" in res.json()["detail"]


def test_another_organization_cannot_cancel_the_order(client, other_organizer_headers, placed):
    order, _ = placed
    res = client.post(f"/reservations/orders/{order.id}/cancel", headers=other_organizer_headers)
    assert res.status_code == 404


def test_cancelling_twice_is_a_404(client, organizer_headers, placed):
    order, _ = placed
    client.post(f"/reservations/orders/{order.id}/cancel", headers=organizer_headers)
    assert client.post(f"/reservations/orders/{order.id}/cancel", headers=organizer_headers).status_code == 404


# ---------- Listings ----------

def test_my_reservations_carry_the_order_code_and_store_name(
    client, organizer_headers, placed, store
):
    rows = client.get("/reservations/mine", headers=organizer_headers).json()

    assert len(rows) == 3
    assert {r["order_qr_code"] for r in rows} == {placed[1]}
    assert {r["store_name"] for r in rows} == {store.name}


def test_single_reservations_are_unchanged(client, organizer_headers, available_item):
    res = client.post(
        "/reservations",
        json={"item_id": available_item.id, "scheduled_pickup_at": iso_in(hours=2)},
        headers=organizer_headers,
    )
    body = res.json()

    assert body["qr_code"]
    assert body["order_id"] is None and body["order_qr_code"] is None
