"""
FR-11.1: the immutable donation record written when a pickup is confirmed,
and FR-11.3/11.7: the staff-only report and CSV export built on top of it.
"""
from datetime import datetime, timedelta

import pytest

from app import models

from .conftest import iso_in


def _valued_item(db, name="Canned Beans", sku="BEANS-1", unit_value=2.50):
    item = models.Item(
        name=name,
        sku=sku,
        category="Pantry",
        status=models.ItemStatus.AVAILABLE,
        sell_by_date=datetime.utcnow() + timedelta(days=3),
        discard_after=datetime.utcnow() + timedelta(days=5),
        unit_value=unit_value,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _donate(client, db, organizer_headers, staff_headers, item):
    """Reserves `item` for the caller's org and immediately confirms pickup,
    the same sequence the shelf/QR flow drives in production."""
    res = client.post(
        "/reservations",
        json={"item_id": item.id, "scheduled_pickup_at": iso_in(hours=2)},
        headers=organizer_headers,
    )
    assert res.status_code == 200, res.text
    reservation = db.query(models.Reservation).filter(models.Reservation.item_id == item.id).one()

    confirm = client.post(f"/reservations/pickup/{reservation.qr_code}", headers=staff_headers)
    assert confirm.status_code == 200, confirm.text
    db.refresh(reservation)
    return reservation


@pytest.fixture
def valued_item(db):
    return _valued_item(db)


@pytest.fixture
def picked_up_reservation(client, db, organizer_headers, staff_headers, valued_item):
    return _donate(client, db, organizer_headers, staff_headers, valued_item)


def test_confirming_pickup_writes_a_donation_record(
    db, picked_up_reservation, valued_item, verified_pantry
):
    record = db.query(models.DonationRecord).one()
    assert record.reservation_id == picked_up_reservation.id
    assert record.item_name == "Canned Beans"
    assert record.item_sku == "BEANS-1"
    assert float(record.unit_value_at_handoff) == 2.50
    assert record.pantry_id == verified_pantry.id
    assert record.pantry_name_at_handoff == verified_pantry.org_name
    assert record.confirmed_at is not None


def test_donation_record_survives_a_later_edit_to_the_item(db, picked_up_reservation, valued_item):
    """A snapshot, not a live join — the record must read the same after
    the item it came from is renamed or repriced."""
    valued_item.name = "Renamed Later"
    valued_item.unit_value = 999
    db.commit()

    record = db.query(models.DonationRecord).one()
    assert record.item_name == "Canned Beans"
    assert float(record.unit_value_at_handoff) == 2.50


def test_donations_report_filters_by_date_range(client, db, staff_headers, picked_up_reservation):
    record = db.query(models.DonationRecord).one()
    record.confirmed_at = datetime(2020, 1, 1)
    db.commit()

    too_late = client.get(
        "/reports/donations?date_from=2021-01-01T00:00:00", headers=staff_headers
    )
    assert too_late.status_code == 200
    assert too_late.json() == []

    in_range = client.get(
        "/reports/donations?date_from=2019-01-01T00:00:00&date_to=2020-06-01T00:00:00",
        headers=staff_headers,
    )
    assert in_range.status_code == 200
    assert len(in_range.json()) == 1
    assert in_range.json()[0]["item_name"] == "Canned Beans"


def test_donations_report_filters_by_pantry(
    client, db, staff_headers, organizer_headers, other_organizer_headers, verified_pantry
):
    mine = _valued_item(db, name="Mine", sku="A1")
    theirs = _valued_item(db, name="Theirs", sku="A2")
    _donate(client, db, organizer_headers, staff_headers, mine)
    _donate(client, db, other_organizer_headers, staff_headers, theirs)

    res = client.get(f"/reports/donations?pantry_id={verified_pantry.id}", headers=staff_headers)
    assert res.status_code == 200
    names = [r["item_name"] for r in res.json()]
    assert names == ["Mine"]


def test_donations_export_returns_csv_with_the_expected_rows(client, picked_up_reservation, staff_headers):
    res = client.get("/reports/donations/export", headers=staff_headers)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")

    lines = res.text.strip().splitlines()
    assert lines[0].split(",")[:3] == ["confirmed_at", "item_name", "item_sku"]
    assert "Canned Beans" in lines[1]
    assert "2.5" in lines[1] or "2.50" in lines[1]


def test_donations_report_requires_staff_role(client, organizer_headers):
    """FR-11.3 — this is the store's own tax data, not a pantry's."""
    res = client.get("/reports/donations", headers=organizer_headers)
    assert res.status_code == 403

    res = client.get("/reports/donations/export", headers=organizer_headers)
    assert res.status_code == 403
