"""
Store isolation (NFR-4.6.1). These are the guard for the plan's main risk:
a scoping mistake that lets one store read or change another store's stock.

Each test asserts the negative as well as the positive: the other store's
data is not just absent from a list, it is unreachable by id, and a write
that names it is refused.
"""
from datetime import datetime, timedelta

import pytest

from app import models

from .conftest import _bearer, _make_user, iso_in


def _store(db, name, brand=None, active=True):
    store = models.Store(name=name, address="1 Main St", active=active, brand_id=brand.id if brand else None)
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def _brand(db, name):
    brand = models.Brand(name=name)
    db.add(brand)
    db.commit()
    db.refresh(brand)
    return brand


def _item(db, store, name="Test Milk 2L", status=models.ItemStatus.AVAILABLE, shelf=None):
    item = models.Item(
        store_id=store.id,
        shelf_id=shelf.id if shelf else None,
        name=name,
        category="Dairy",
        status=status,
        sell_by_date=datetime.utcnow() + timedelta(days=3),
        discard_after=datetime.utcnow() + timedelta(days=5),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _shelf(db, store, name="Shelf A"):
    shelf = models.Shelf(store_id=store.id, name=name)
    db.add(shelf)
    db.commit()
    db.refresh(shelf)
    return shelf


def _headers(db, email, store=None, brand=None, role=models.UserRole.STAFF):
    return _bearer(_make_user(db, email, role, store=store, brand=brand))


@pytest.fixture
def two_stores(db):
    a = _store(db, "Market A")
    b = _store(db, "Market B")
    return a, b


@pytest.fixture
def a_headers(db, two_stores):
    return _headers(db, "staff-a@example.com", store=two_stores[0])


@pytest.fixture
def b_headers(db, two_stores):
    return _headers(db, "staff-b@example.com", store=two_stores[1])


# ---------- Staff reads ----------

def test_staff_list_shows_only_their_own_items(client, db, two_stores, a_headers):
    a, b = two_stores
    mine = _item(db, a, name="A milk", status=models.ItemStatus.IN_STOCK)
    theirs = _item(db, b, name="B milk", status=models.ItemStatus.IN_STOCK)

    ids = {row["id"] for row in client.get("/items", headers=a_headers).json()}
    assert mine.id in ids
    assert theirs.id not in ids


def test_item_of_another_store_is_404_not_403(client, db, two_stores, a_headers):
    """404, not 403: a 403 would confirm that the item exists (FR-2.4)."""
    _, b = two_stores
    theirs = _item(db, b, status=models.ItemStatus.IN_STOCK)

    assert client.get(f"/items/{theirs.id}", headers=a_headers).status_code == 404


def test_cannot_change_status_of_another_stores_item(client, db, two_stores, a_headers):
    _, b = two_stores
    theirs = _item(db, b, status=models.ItemStatus.IN_STOCK)

    res = client.patch(f"/items/{theirs.id}/status", json={"status": "discarded"}, headers=a_headers)
    assert res.status_code == 404
    db.refresh(theirs)
    assert theirs.status == models.ItemStatus.IN_STOCK


def test_shelves_list_is_scoped(client, db, two_stores, a_headers):
    a, b = two_stores
    mine = _shelf(db, a, name="A shelf")
    theirs = _shelf(db, b, name="B shelf")

    ids = {row["id"] for row in client.get("/shelves", headers=a_headers).json()}
    assert mine.id in ids
    assert theirs.id not in ids


def test_shelf_reading_on_another_stores_shelf_is_404(client, db, two_stores, a_headers):
    _, b = two_stores
    theirs = _shelf(db, b)

    res = client.patch(
        f"/shelves/{theirs.id}/reading", json={"current_temperature_c": 4.0}, headers=a_headers
    )
    assert res.status_code == 404


# ---------- Staff writes ----------

def test_store_account_cannot_add_a_shelf_to_another_store(client, db, two_stores, a_headers):
    """Refused rather than quietly redirected, so a wrong store is noticed."""
    _, b = two_stores
    res = client.post("/shelves", json={"name": "Shelf X", "store_id": b.id}, headers=a_headers)

    assert res.status_code == 403
    assert db.query(models.Shelf).filter_by(name="Shelf X").first() is None


def test_store_account_shelf_defaults_to_its_own_store(client, db, two_stores, a_headers):
    a, _ = two_stores
    res = client.post("/shelves", json={"name": "Shelf X"}, headers=a_headers)

    assert res.status_code == 200
    assert res.json()["store_id"] == a.id


def test_new_item_cannot_be_placed_on_another_stores_shelf(client, db, two_stores, a_headers):
    _, b = two_stores
    theirs = _shelf(db, b)

    res = client.post(
        "/items",
        json={"name": "Milk", "category": "Dairy", "shelf_id": theirs.id,
              "sell_by_date": iso_in(days=3)},
        headers=a_headers,
    )
    assert res.status_code == 422


# ---------- Intake ----------

def test_intake_scan_is_recorded_at_the_location_it_was_made(client, db, two_stores, a_headers):
    a, _ = two_stores
    res = client.post("/intake/scans", json={"name_override": "Eggs"}, headers=a_headers)
    assert res.status_code == 200
    assert db.get(models.IntakeScan, res.json()["id"]).store_id == a.id


def test_other_store_cannot_see_or_confirm_a_scan(client, db, two_stores, a_headers, b_headers):
    scan = client.post("/intake/scans", json={"name_override": "Eggs"}, headers=a_headers).json()

    assert client.get(f"/intake/scans/{scan['id']}", headers=b_headers).status_code == 404
    queue = client.get("/intake/scans", headers=b_headers).json()
    assert all(row["id"] != scan["id"] for row in queue)

    confirm = client.post(
        f"/intake/scans/{scan['id']}/confirm",
        json={"confirmed_date": iso_in(days=5), "name": "Eggs"},
        headers=b_headers,
    )
    assert confirm.status_code == 409


def test_confirmed_items_inherit_the_scans_store(client, db, two_stores, a_headers):
    a, _ = two_stores
    scan = client.post("/intake/scans", json={"name_override": "Eggs", "quantity": 2}, headers=a_headers).json()

    res = client.post(
        f"/intake/scans/{scan['id']}/confirm",
        json={"confirmed_date": iso_in(days=5), "name": "Eggs"},
        headers=a_headers,
    )
    assert res.status_code == 200
    assert {row["store_id"] for row in res.json()} == {a.id}


def test_scan_cannot_be_placed_on_another_stores_shelf(client, db, two_stores, a_headers):
    _, b = two_stores
    theirs = _shelf(db, b)

    res = client.post(
        "/intake/scans", json={"name_override": "Eggs", "shelf_id": theirs.id}, headers=a_headers
    )
    assert res.status_code == 422


# ---------- Brands ----------

def test_brand_staff_see_every_location_of_their_brand(client, db):
    brand = _brand(db, "Fresh Chain")
    loc1 = _store(db, "Fresh Chain East", brand=brand)
    loc2 = _store(db, "Fresh Chain West", brand=brand)
    outsider = _store(db, "Independent Market")
    _item(db, loc1, name="East milk", status=models.ItemStatus.IN_STOCK)
    _item(db, loc2, name="West milk", status=models.ItemStatus.IN_STOCK)
    _item(db, outsider, name="Other milk", status=models.ItemStatus.IN_STOCK)

    headers = _headers(db, "brand@example.com", brand=brand, role=models.UserRole.MANAGER)
    names = {row["name"] for row in client.get("/items", headers=headers).json()}
    assert names == {"East milk", "West milk"}


def test_brand_staff_cannot_reach_another_brands_store(client, db):
    mine = _brand(db, "Fresh Chain")
    theirs = _brand(db, "Other Chain")
    _store(db, "Fresh Chain East", brand=mine)
    other_loc = _store(db, "Other Chain North", brand=theirs)
    item = _item(db, other_loc, status=models.ItemStatus.IN_STOCK)

    headers = _headers(db, "brand@example.com", brand=mine, role=models.UserRole.MANAGER)
    assert client.get(f"/items/{item.id}", headers=headers).status_code == 404


def test_brand_staff_must_name_which_location_a_new_shelf_is_for(client, db):
    brand = _brand(db, "Fresh Chain")
    loc1 = _store(db, "Fresh Chain East", brand=brand)
    _store(db, "Fresh Chain West", brand=brand)
    headers = _headers(db, "brand@example.com", brand=brand, role=models.UserRole.MANAGER)

    ambiguous = client.post("/shelves", json={"name": "Shelf"}, headers=headers)
    assert ambiguous.status_code == 422

    named = client.post("/shelves", json={"name": "Shelf", "store_id": loc1.id}, headers=headers)
    assert named.status_code == 200
    assert named.json()["store_id"] == loc1.id


# ---------- Accounts with no store ----------

def test_unlinked_staff_account_sees_nothing(client, db):
    _store(db, "Market A")
    _store(db, "Market B")
    _item(db, db.query(models.Store).first(), status=models.ItemStatus.IN_STOCK)
    headers = _headers(db, "orphan@example.com", store=None)

    assert client.get("/items", headers=headers).json() == []
    assert client.get("/shelves", headers=headers).json() == []


def test_deactivated_store_staff_see_an_empty_view(client, db):
    store = _store(db, "Closed Market", active=False)
    _item(db, store, status=models.ItemStatus.IN_STOCK)
    headers = _headers(db, "closed@example.com", store=store)

    assert client.get("/items", headers=headers).json() == []


# ---------- Pantry side ----------

def test_organizer_sees_available_food_from_active_stores_only(
    client, db, two_stores, organizer_headers
):
    a, b = two_stores
    _item(db, a, name="Open store food")
    _item(db, b, name="Closed store food")
    b.active = False
    db.commit()

    names = {row["name"] for row in client.get("/items", headers=organizer_headers).json()}
    assert names == {"Open store food"}


def test_organizer_cannot_reserve_food_from_a_deactivated_store(
    client, db, two_stores, organizer_headers
):
    _, b = two_stores
    item = _item(db, b)
    b.active = False
    db.commit()

    res = client.post(
        "/reservations",
        json={"item_id": item.id, "scheduled_pickup_at": iso_in(hours=2)},
        headers=organizer_headers,
    )
    assert res.status_code == 409


def test_store_reservation_listing_only_shows_its_own_food(
    client, db, two_stores, organizer_headers, a_headers
):
    a, b = two_stores
    mine = _item(db, a, name="A food")
    theirs = _item(db, b, name="B food")
    for item in (mine, theirs):
        assert client.post(
            "/reservations",
            json={"item_id": item.id, "scheduled_pickup_at": iso_in(hours=2)},
            headers=organizer_headers,
        ).status_code == 200

    names = {row["item_name"] for row in client.get("/reservations", headers=a_headers).json()}
    assert names == {"A food"}


def test_pickup_code_for_another_stores_food_reads_as_unknown(
    client, db, two_stores, organizer_headers, b_headers
):
    """Same wording as a code that does not exist, so the response does not
    reveal another store's reservation (FR-2.4)."""
    _, b = two_stores
    item = _item(db, b)
    reserved = client.post(
        "/reservations",
        json={"item_id": item.id, "scheduled_pickup_at": iso_in(hours=2)},
        headers=organizer_headers,
    ).json()

    a_scanner = _headers(db, "scanner-a@example.com", store=db.query(models.Store).filter_by(name="Market A").first())
    other = client.post(f"/reservations/pickup/{reserved['qr_code']}", headers=a_scanner)
    unknown = client.post("/reservations/pickup/not-a-real-code", headers=a_scanner)

    assert other.status_code == 404
    assert other.json()["detail"] == unknown.json()["detail"]
    db.refresh(item)
    assert item.status == models.ItemStatus.RESERVED


# ---------- Reports ----------

def test_donation_report_is_scoped_to_the_callers_store(client, db, two_stores):
    a, b = two_stores
    for store, name in ((a, "A donation"), (b, "B donation")):
        db.add(models.DonationRecord(
            reservation_id=_reservation_for(db, store, name).id,
            item_name=name,
            pantry_id=_pantry(db).id,
            pantry_name_at_handoff="Test Pantry",
            store_id=store.id,
            confirmed_by_user_id=_user_id(db),
            confirmed_at=datetime.utcnow(),
        ))
    db.commit()

    headers = _headers(db, "reports-a@example.com", store=a)
    names = {row["item_name"] for row in client.get("/reports/donations", headers=headers).json()}
    assert names == {"A donation"}


# ---------- Creating stores and staff ----------

@pytest.fixture
def admin_headers(db):
    return _headers(db, "admin@example.com", role=models.UserRole.ADMIN)


def test_admin_creates_store_and_brand(client, admin_headers, db):
    brand = client.post("/stores/brands", json={"name": "Fresh Chain"}, headers=admin_headers)
    assert brand.status_code == 200

    store = client.post(
        "/stores",
        json={"name": "Fresh Chain East", "address": "1 Main St", "brand_id": brand.json()["id"]},
        headers=admin_headers,
    )
    assert store.status_code == 200
    assert store.json()["brand_id"] == brand.json()["id"]


def test_store_manager_adds_staff_to_their_own_store(client, db, two_stores):
    a, _ = two_stores
    manager = _headers(db, "mgr-a@example.com", store=a, role=models.UserRole.MANAGER)

    res = client.post(
        "/stores/staff",
        json={"email": "new-a@example.com", "password": "a-long-password", "role": "staff"},
        headers=manager,
    )
    assert res.status_code == 200
    created = db.query(models.User).filter_by(email="new-a@example.com").first()
    assert created.store_id == a.id


def test_store_manager_cannot_create_a_brand_account(client, db, two_stores):
    a, _ = two_stores
    brand = _brand(db, "Fresh Chain")
    manager = _headers(db, "mgr-a@example.com", store=a, role=models.UserRole.MANAGER)

    res = client.post(
        "/stores/staff",
        json={"email": "brand@example.com", "password": "a-long-password", "brand_id": brand.id},
        headers=manager,
    )
    assert res.status_code == 403


def test_store_manager_cannot_add_staff_to_another_store(client, db, two_stores):
    a, b = two_stores
    manager = _headers(db, "mgr-a@example.com", store=a, role=models.UserRole.MANAGER)

    res = client.post(
        "/stores/staff",
        json={"email": "intruder@example.com", "password": "a-long-password", "store_id": b.id},
        headers=manager,
    )
    assert res.status_code == 403


def test_staff_creation_refuses_a_duplicate_email(client, db, two_stores):
    a, _ = two_stores
    manager = _headers(db, "mgr-a@example.com", store=a, role=models.UserRole.MANAGER)

    res = client.post(
        "/stores/staff",
        json={"email": "MGR-A@example.com", "password": "a-long-password", "role": "staff"},
        headers=manager,
    )
    assert res.status_code == 409


def test_store_manager_without_a_store_link_defaults_to_nothing(client, db):
    """A store manager's new staff go to their own store. A manager with no
    store (and no brand) has nowhere to put them, so it is refused."""
    manager = _headers(db, "orphan-mgr@example.com", store=None, role=models.UserRole.MANAGER)

    res = client.post(
        "/stores/staff", json={"email": "x@example.com", "password": "a-long-password"}, headers=manager
    )
    assert res.status_code == 403


def test_admin_must_link_a_new_account_to_exactly_one_place(client, db, two_stores, admin_headers):
    a, _ = two_stores

    neither = client.post(
        "/stores/staff", json={"email": "x@example.com", "password": "a-long-password"}, headers=admin_headers
    )
    both = client.post(
        "/stores/staff",
        json={"email": "y@example.com", "password": "a-long-password", "store_id": a.id, "brand_id": "x"},
        headers=admin_headers,
    )
    assert neither.status_code == 422
    assert both.status_code == 422


def test_store_list_is_scoped_to_the_callers_store(client, db, two_stores, a_headers):
    a, _ = two_stores
    ids = {row["id"] for row in client.get("/stores", headers=a_headers).json()}
    assert ids == {a.id}


# ---------- Helpers for the report test ----------

def _reservation_for(db, store, name):
    item = _item(db, store, name=name, status=models.ItemStatus.PICKED_UP)
    reservation = models.Reservation(
        item_id=item.id,
        pantry_id=_pantry(db).id,
        status=models.ReservationStatus.PICKED_UP,
        hold_expires_at=datetime.utcnow() + timedelta(hours=1),
        qr_code=f"qr-{item.id}",
    )
    db.add(reservation)
    db.commit()
    db.refresh(reservation)
    return reservation


def _pantry(db):
    pantry = db.query(models.Pantry).first()
    if pantry is None:
        pantry = models.Pantry(org_name="Test Pantry", ein="12-3456789", contact_email="p@example.org", verified=True)
        db.add(pantry)
        db.commit()
        db.refresh(pantry)
    return pantry


def _user_id(db):
    user = db.query(models.User).first()
    if user is None:
        user = _make_user(db, "confirmer@example.com", models.UserRole.MANAGER)
    return user.id
