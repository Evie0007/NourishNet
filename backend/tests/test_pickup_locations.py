"""
FR-8.14: the pantry map's side panel — find shelves near a zip code, and see
which of them are actually available.

The geocoder is stubbed in every test that needs a zip; nothing here reaches
the network.
"""
from datetime import datetime, timedelta

import pytest

from app import geocode, models

from .conftest import _bearer, _make_user, iso_in

# Downtown San Jose, Willow Glen, and a point far to the north.
DOWNTOWN = (37.3331, -121.8896)
WILLOW_GLEN = (37.3016, -121.8996)
OAKLAND = (37.8044, -122.2712)
SEARCH_FROM = (37.3040, -121.9000)  # almost on top of Willow Glen


@pytest.fixture
def zip_centers(monkeypatch):
    centers = {"95125": SEARCH_FROM}
    monkeypatch.setattr(geocode, "geocode_zip", lambda z: centers.get(z))
    return centers


def _place(db, name, coords, *, available=1, **fields):
    store = models.Store(
        name=name, address=f"{name} St", latitude=coords[0], longitude=coords[1], active=True, **fields
    )
    db.add(store)
    db.commit()
    for i in range(available):
        db.add(
            models.Item(
                store_id=store.id,
                name=f"{name} item {i}",
                status=models.ItemStatus.AVAILABLE,
                sell_by_date=datetime.utcnow() + timedelta(days=3),
                discard_after=datetime.utcnow() + timedelta(days=5),
            )
        )
    db.commit()
    db.refresh(store)
    return store


def _locations(client, headers, **params):
    res = client.get("/stores/pickup-locations", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _by_name(payload):
    return {row["name"]: row for row in payload["locations"]}


# ---------- Availability ----------

def test_a_location_with_food_is_available(client, db, organizer_headers):
    _place(db, "Downtown", DOWNTOWN, available=3)

    row = _by_name(_locations(client, organizer_headers))["Downtown"]

    assert row["available"] is True
    assert row["available_count"] == 3
    assert row["unavailable_reason"] is None


def test_a_closed_location_is_unavailable_even_with_food(client, db, organizer_headers):
    _place(db, "Downtown", DOWNTOWN, available=3, open_for_pickup=False)

    row = _by_name(_locations(client, organizer_headers))["Downtown"]

    assert row["available"] is False
    assert row["unavailable_reason"] == "closed"


def test_an_empty_location_is_unavailable(client, db, organizer_headers):
    _place(db, "Downtown", DOWNTOWN, available=0)

    row = _by_name(_locations(client, organizer_headers))["Downtown"]

    assert row["available"] is False
    assert row["unavailable_reason"] == "no_items"


def test_only_available_items_count(client, db, organizer_headers):
    store = _place(db, "Downtown", DOWNTOWN, available=1)
    db.add(models.Item(store_id=store.id, name="Reserved", status=models.ItemStatus.RESERVED))
    db.add(models.Item(store_id=store.id, name="In review", status=models.ItemStatus.NEEDS_REVIEW))
    db.commit()

    assert _by_name(_locations(client, organizer_headers))["Downtown"]["available_count"] == 1


def test_unlocated_and_deactivated_stores_are_not_listed(client, db, organizer_headers):
    _place(db, "Downtown", DOWNTOWN)
    db.add(models.Store(name="No coordinates", active=True))
    _place(db, "Deactivated", WILLOW_GLEN)
    db.query(models.Store).filter_by(name="Deactivated").update({"active": False})
    db.commit()

    assert list(_by_name(_locations(client, organizer_headers))) == ["Downtown"]


# ---------- Zip search ----------

def test_without_a_zip_there_is_no_distance_and_no_center(client, db, organizer_headers):
    _place(db, "Downtown", DOWNTOWN)

    body = _locations(client, organizer_headers)

    assert body["center"] is None
    assert body["locations"][0]["distance_miles"] is None


def test_a_zip_orders_locations_nearest_first(client, db, organizer_headers, zip_centers):
    _place(db, "Oakland", OAKLAND)
    _place(db, "Downtown", DOWNTOWN)
    _place(db, "Willow Glen", WILLOW_GLEN)

    body = _locations(client, organizer_headers, zip="95125")

    assert [r["name"] for r in body["locations"]] == ["Willow Glen", "Downtown", "Oakland"]
    distances = [r["distance_miles"] for r in body["locations"]]
    assert distances == sorted(distances)
    assert distances[0] < 1 and distances[2] > 20
    assert body["center"] == {"zip": "95125", "latitude": SEARCH_FROM[0], "longitude": SEARCH_FROM[1]}


def test_an_unavailable_location_never_outranks_one_with_food(
    client, db, organizer_headers, zip_centers
):
    """The closest shelf is closed; the pantry should be pointed at the
    nearest one that can actually help, with the closed one shown below."""
    _place(db, "Willow Glen", WILLOW_GLEN, open_for_pickup=False)
    _place(db, "Downtown", DOWNTOWN)

    rows = _locations(client, organizer_headers, zip="95125")["locations"]

    assert [r["name"] for r in rows] == ["Downtown", "Willow Glen"]
    assert rows[1]["unavailable_reason"] == "closed"


def test_zip_plus_four_is_accepted(client, db, organizer_headers, zip_centers):
    _place(db, "Downtown", DOWNTOWN)
    assert _locations(client, organizer_headers, zip="95125-1234")["center"]["zip"] == "95125"


@pytest.mark.parametrize("bad", ["9512", "abcde", "951256", "95 125"])
def test_a_malformed_zip_is_a_422(client, organizer_headers, bad):
    res = client.get("/stores/pickup-locations", params={"zip": bad}, headers=organizer_headers)
    assert res.status_code == 422
    assert "5-digit" in res.json()["detail"]


def test_a_zip_that_cannot_be_located_is_a_422_that_names_it(client, organizer_headers, zip_centers):
    res = client.get("/stores/pickup-locations", params={"zip": "00000"}, headers=organizer_headers)
    assert res.status_code == 422
    assert "00000" in res.json()["detail"]


def test_a_blank_zip_just_lists_everything(client, db, organizer_headers):
    _place(db, "Downtown", DOWNTOWN)
    assert len(_locations(client, organizer_headers, zip="  ")["locations"]) == 1


def test_locations_are_for_organizers_only(client, staff_headers):
    assert client.get("/stores/pickup-locations", headers=staff_headers).status_code == 403


def test_a_zip_search_with_lookup_off_says_so(client, organizer_headers):
    res = client.get("/stores/pickup-locations", params={"zip": "95122"}, headers=organizer_headers)
    assert res.status_code == 422
    assert "turned off" in res.json()["detail"]


def test_geocoding_a_zip_is_off_without_the_switch():
    assert geocode.geocode_zip("95125") is None


def test_normalize_zip():
    assert geocode.normalize_zip(" 95125 ") == "95125"
    assert geocode.normalize_zip("95125-0001") == "95125"
    assert geocode.normalize_zip("9512") is None
    assert geocode.normalize_zip(None) is None


def test_distance_is_zero_to_itself_and_symmetric():
    assert geocode.distance_miles(DOWNTOWN, DOWNTOWN) == pytest.approx(0)
    assert geocode.distance_miles(DOWNTOWN, OAKLAND) == pytest.approx(
        geocode.distance_miles(OAKLAND, DOWNTOWN)
    )
    # San Jose to Oakland is roughly 35 miles in a straight line.
    assert 30 < geocode.distance_miles(DOWNTOWN, OAKLAND) < 40


# ---------- Opening and closing ----------

def test_a_manager_can_close_their_own_location(client, db, store, manager_headers, organizer_headers):
    store.latitude, store.longitude = DOWNTOWN
    db.add(
        models.Item(store_id=store.id, name="Milk", status=models.ItemStatus.AVAILABLE,
                    sell_by_date=datetime.utcnow() + timedelta(days=2))
    )
    db.commit()

    res = client.patch(
        f"/stores/{store.id}/availability", json={"open_for_pickup": False}, headers=manager_headers
    )

    assert res.status_code == 200
    assert res.json()["open_for_pickup"] is False
    assert _by_name(_locations(client, organizer_headers))[store.name]["unavailable_reason"] == "closed"


def test_a_closed_location_leaves_the_pantrys_donation_list(
    client, db, store, manager_headers, organizer_headers, available_item
):
    """The list must agree with the map, or the pantry sees food it can't claim."""
    assert [i["id"] for i in client.get("/items", headers=organizer_headers).json()] == [available_item.id]

    client.patch(f"/stores/{store.id}/availability", json={"open_for_pickup": False}, headers=manager_headers)

    assert client.get("/items", headers=organizer_headers).json() == []


def test_a_closed_location_cannot_be_reserved_from(
    client, db, store, manager_headers, organizer_headers, available_item
):
    client.patch(f"/stores/{store.id}/availability", json={"open_for_pickup": False}, headers=manager_headers)

    res = client.post(
        "/reservations",
        json={"item_id": available_item.id, "scheduled_pickup_at": iso_in(hours=2)},
        headers=organizer_headers,
    )
    assert res.status_code == 409


def test_reopening_restores_the_food(client, store, manager_headers, organizer_headers, available_item):
    for state in (False, True):
        client.patch(f"/stores/{store.id}/availability", json={"open_for_pickup": state}, headers=manager_headers)

    assert len(client.get("/items", headers=organizer_headers).json()) == 1


def test_closing_does_not_cancel_a_reservation_already_made(
    client, db, store, manager_headers, staff_headers, organizer_headers, available_item
):
    reserved = client.post(
        "/reservations",
        json={"item_id": available_item.id, "scheduled_pickup_at": iso_in(hours=2)},
        headers=organizer_headers,
    ).json()

    client.patch(f"/stores/{store.id}/availability", json={"open_for_pickup": False}, headers=manager_headers)

    assert client.post(f"/reservations/pickup/{reserved['qr_code']}", headers=staff_headers).status_code == 200


def test_plain_staff_cannot_close_a_location(client, store, staff_headers):
    res = client.patch(f"/stores/{store.id}/availability", json={"open_for_pickup": False}, headers=staff_headers)
    assert res.status_code == 403


def test_a_manager_cannot_close_another_stores_location(client, other_store, manager_headers):
    res = client.patch(
        f"/stores/{other_store.id}/availability", json={"open_for_pickup": False}, headers=manager_headers
    )
    assert res.status_code == 404


def test_an_organizer_cannot_close_a_location(client, store, organizer_headers):
    res = client.patch(f"/stores/{store.id}/availability", json={"open_for_pickup": False}, headers=organizer_headers)
    assert res.status_code == 403
