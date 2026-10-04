"""
Store coordinates and the pantry map (plan Phases 2 and 3).

The geocoder is replaced with a stub in every test that needs a location.
Nothing here reaches the network.
"""
import pytest

from app import geocode, models

from .conftest import _bearer, _make_user


@pytest.fixture
def admin_headers(db):
    return _bearer(_make_user(db, "admin@example.com", models.UserRole.ADMIN))


@pytest.fixture
def stub_geocoder(monkeypatch):
    """Records every address it is asked about. Returns coordinates for any
    address that is not in `failing`."""
    calls = []
    state = {"failing": set()}

    def fake(address):
        calls.append(address)
        if address in state["failing"]:
            return None
        return (37.33, -121.89)

    monkeypatch.setattr(geocode, "geocode_address", fake)
    fake.calls = calls
    fake.state = state
    return fake


def _create_store(client, admin_headers, name="Market A", address="1 Main St, San Jose, CA"):
    res = client.post("/stores", json={"name": name, "address": address}, headers=admin_headers)
    assert res.status_code == 200, res.text
    return res.json()


# ---------- Geocoding ----------

def test_creating_a_store_locates_it_from_its_address(client, admin_headers, stub_geocoder):
    store = _create_store(client, admin_headers)

    assert stub_geocoder.calls == ["1 Main St, San Jose, CA"]
    assert store["latitude"] == pytest.approx(37.33)
    assert store["longitude"] == pytest.approx(-121.89)


def test_a_failed_lookup_leaves_the_store_unlocated_and_off_the_map(
    client, admin_headers, stub_geocoder, organizer_headers
):
    stub_geocoder.state["failing"].add("Nowhere Rd")
    store = _create_store(client, admin_headers, address="Nowhere Rd")

    assert store["latitude"] is None
    ids = {row["id"] for row in client.get("/stores/map", headers=organizer_headers).json()}
    assert store["id"] not in ids


def test_changing_the_address_relocates_the_store(client, admin_headers, stub_geocoder):
    store = _create_store(client, admin_headers)
    stub_geocoder.calls.clear()

    res = client.patch(
        f"/stores/{store['id']}", json={"address": "900 New Ave, San Jose, CA"}, headers=admin_headers
    )
    assert res.status_code == 200
    assert stub_geocoder.calls == ["900 New Ave, San Jose, CA"]


def test_renaming_a_store_does_not_spend_a_lookup(client, admin_headers, stub_geocoder):
    store = _create_store(client, admin_headers)
    stub_geocoder.calls.clear()

    client.patch(f"/stores/{store['id']}", json={"name": "Market A (renamed)"}, headers=admin_headers)
    assert stub_geocoder.calls == []


def test_geocoding_is_off_by_default_and_never_reaches_the_network():
    """With GEOCODE_ENABLED unset, the lookup returns nothing without
    opening a connection. Tests depend on this."""
    assert geocode.GEOCODE_ENABLED is False
    assert geocode.geocode_address("1 Main St") is None


# ---------- Pantry map ----------

def test_map_lists_active_located_stores_for_organizers(
    client, admin_headers, stub_geocoder, organizer_headers
):
    _create_store(client, admin_headers, name="Market A")
    _create_store(client, admin_headers, name="Market B")

    names = [row["name"] for row in client.get("/stores/map", headers=organizer_headers).json()]
    assert names == ["Market A", "Market B"]


def test_map_hides_a_deactivated_store(client, admin_headers, stub_geocoder, organizer_headers):
    store = _create_store(client, admin_headers, name="Market A")
    client.patch(f"/stores/{store['id']}", json={"active": False}, headers=admin_headers)

    assert client.get("/stores/map", headers=organizer_headers).json() == []


def test_map_is_not_open_to_store_staff(client, admin_headers, stub_geocoder, db):
    _create_store(client, admin_headers)
    staff = _bearer(_make_user(db, "staff@example.com", models.UserRole.STAFF))

    assert client.get("/stores/map", headers=staff).status_code == 403


def test_unverified_organizer_can_still_see_the_map(
    client, admin_headers, stub_geocoder, unverified_organizer_headers
):
    """FR-7.4: browsing is allowed before verification, reserving is not."""
    _create_store(client, admin_headers)

    assert client.get("/stores/map", headers=unverified_organizer_headers).status_code == 200


def test_only_an_admin_can_change_a_store(client, admin_headers, stub_geocoder, manager_headers):
    store = _create_store(client, admin_headers)

    res = client.patch(f"/stores/{store['id']}", json={"name": "x"}, headers=manager_headers)
    assert res.status_code == 403


def test_updating_an_unknown_store_is_404(client, admin_headers):
    res = client.patch("/stores/00000000-0000-0000-0000-000000000000", json={"name": "x"}, headers=admin_headers)
    assert res.status_code == 404
