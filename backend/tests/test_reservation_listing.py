"""What each dashboard is allowed to see, and what it needs to render."""
from datetime import datetime, timedelta

from app import models

from .conftest import iso_in


def _item(db, name):
    item = models.Item(
        name=name,
        category="Dairy",
        status=models.ItemStatus.AVAILABLE,
        sell_by_date=datetime.utcnow() + timedelta(days=3),
        discard_after=datetime.utcnow() + timedelta(days=5),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_organizer_sees_only_own_reservations(
    client, db, organizer_headers, other_organizer_headers
):
    """FR-2.4."""
    mine = _item(db, "Mine")
    theirs = _item(db, "Theirs")

    client.post(
        "/reservations",
        json={"item_id": mine.id, "scheduled_pickup_at": iso_in(hours=2)},
        headers=organizer_headers,
    )
    client.post(
        "/reservations",
        json={"item_id": theirs.id, "scheduled_pickup_at": iso_in(hours=2)},
        headers=other_organizer_headers,
    )

    res = client.get("/reservations/mine", headers=organizer_headers)
    assert res.status_code == 200
    names = [r["item_name"] for r in res.json()]
    assert names == ["Mine"]


def test_staff_listing_includes_scheduled_pickup_at_and_pantry_name(
    client, db, organizer_headers, manager_headers
):
    """The Pickup schedule card renders straight off this payload."""
    item = _item(db, "Scheduled Milk")
    client.post(
        "/reservations",
        json={"item_id": item.id, "scheduled_pickup_at": iso_in(hours=5)},
        headers=organizer_headers,
    )

    res = client.get("/reservations", headers=manager_headers)
    assert res.status_code == 200

    row = res.json()[0]
    assert row["scheduled_pickup_at"] is not None
    assert row["pantry_name"] == "Second Harvest Test"
    assert row["item_name"] == "Scheduled Milk"


def test_staff_listing_spans_organizations(
    client, db, organizer_headers, other_organizer_headers, manager_headers
):
    for name, headers in (("A", organizer_headers), ("B", other_organizer_headers)):
        client.post(
            "/reservations",
            json={"item_id": _item(db, name).id, "scheduled_pickup_at": iso_in(hours=2)},
            headers=headers,
        )

    res = client.get("/reservations", headers=manager_headers)
    assert res.status_code == 200
    assert len(res.json()) == 2


def test_organizer_cannot_read_the_store_wide_listing(client, organizer_headers):
    res = client.get("/reservations", headers=organizer_headers)
    assert res.status_code == 403
