# Multi-store support and pantry map — plan

Status: **proposed, for review.** No code has changed yet.

## Goal

Let each grocery store or local market use NourishNet with its own shelves and
its own inventory, while food pantries see available food from every store on
a map and choose what to reserve.

## Decisions

1. **One database, a `store_id` on every store-owned row.** Not a database per
   store. A separate database per store means a separate backend deployment,
   migrations and backups for each store, and the pantry map would need a
   cross-database aggregation layer. The spec's own recommendation (C-1,
   NFR-4.6.1) is to add `store_id` and scope every query. This reverses the
   "multi-store tenancy is out of scope" note in the spec, so the spec is
   updated in the same change. **Confirmed by the product owner.**
   A store can belong to a **brand** (a chain with several locations). Staff
   of a store see only their own store. Staff whose account is linked to a
   brand see every location of that brand. A store with no brand is a brand
   of one. See the data model below.
2. **Leaflet with OpenStreetMap tiles** for the map. No API key or billing
   account. Before a production launch, the tile provider is revisited
   (OpenStreetMap's public tile servers are for light use).
3. **Store coordinates come from geocoding the store address once, at store
   setup.** Nominatim (OpenStreetMap's geocoder) is fine at this volume. It is
   rate-limited to about one request per second and needs an identifying
   user-agent.
4. **Polling, not a live connection.** The pantry map refreshes every 30–60
   seconds. This matches the sweep's one-minute cadence and needs no new
   infrastructure.

## Visibility rules (unchanged, stated here so they are not lost)

- Pantries see only items in status `available`, and only from stores that
   are active. Items in review, near expiry, reserved by someone else, or
   discarded never appear.
- Store staff see only their own store's shelves, items, and reservations.
- Brand staff (linked to a brand, not a single store) see the shelves, items,
  and reservations of every store in that brand. They never see another
  brand's stores.
- Organizers still need `verified = true` to reserve (FR-7.5).
- The pickup QR flow is unchanged: only store staff confirm a pickup.

## Phases

### Phase 1 — store scoping (backend, no UI change)

- Add a `Brand` table: `id`, `name`. Optional for a store.
- Add a `Store` table: `id`, `name`, `address`, `latitude`, `longitude`,
  `active`, `brand_id` (nullable).
- Each store-role user has a `store_id`. A brand-role user has a `brand_id`
  and no store. The role set needs a brand-level role, so this needs a
  decision on naming before code (e.g. `brand_manager`).
- Add `store_id` (non-null after migration) to `User` (store roles only),
  `Shelf`, `Item`, `IntakeScan`, `Reservation`'s item path, and `Product`
  only if product catalogs become per-store (decide before building; the
  default here is a shared catalog).
- Scope every query in `crud.py` and the routers by the caller's `store_id`.
  Store roles get their store from the token, never from the request body
  (the same rule reservations already follow for `pantry_id`).
- `upgrade_schema` gets a step that creates a default store for existing data
  and backfills `store_id`. Idempotent, like the existing steps.
- Tests: one store cannot read, change, or reserve another store's items,
  shelves, or intake scans. Run against Postgres, not SQLite, for the same
  reasons the README gives for the reservation claim.

### Phase 2 — store coordinates

- Geocode `Store.address` when a store is created or its address changes.
  Store the result; do not geocode on every page load.
- If geocoding fails, the store is saved with no coordinates and is hidden
  from the map until someone fixes the address. Nothing else is blocked.

### Phase 3 — pantry map (frontend)

- Add `leaflet` and `react-leaflet` to `frontend/package.json`. Approved in
  principle; the exact pins are added in the same commit, per the pinning
  convention.
- New endpoint: `GET /stores/map` returns active stores with coordinates and
  a count and summary of their available items. Organizers only.
- The organizer dashboard gets a map panel. Selecting a store lists its
  available items and their pickup windows. Reserving still goes through the
  existing reservation endpoint.
- All calls go through `src/api.js`, and dates are parsed with `parseUtc()`.

### Phase 4 — demo data for presentation day

- Two stores, each with its own shelves and a small set of real products.
- The demo runs against a separate demo database, not the shared one.

## Spec updates in this change

- Replace the "multi-store tenancy is out of scope" note with the new scope.
- Mark C-1 and NFR-4.6.1 as addressed by Phase 1, with the migration noted.
- Add FR entries for store setup, geocoding, and the pantry map.

## Decided

- One database, `store_id` on store-owned rows (confirmed).
- Store staff see only their own store; brand-linked staff see all locations
  of their brand (confirmed).
- The map is in scope for the presentation, so Phases 1–4 are all needed
  (confirmed).

- Intake and pickup happen at the location where the scan is made. A brand
  user can scan at any location of their brand, but each record is tied to
  the location where it was made.
- The pantry map shows one marker per shelf location, not per brand. Each
  marker lists the items on that location's shelves. The pantry chooses the
  location; the location is not chosen by the pantry's address.
- The first account for a store is created by a **store admin**. That person
  creates the store's shelves and links staff accounts to the store. Brand
  accounts are linked by a platform admin.

## Open questions

- **Store admin role.** The code already has `manager` (provisions staff) and
  `admin` (platform-level). The store admin could be the existing `manager`
  role, scoped to one store, or a new role. Proposed: reuse `manager` for a
  store admin, and keep platform `admin` for brand linking and verification.
  This needs your confirmation, because it changes who can create accounts.
- Is a shared product catalog across stores acceptable? (Proposed: yes.)

## Risks

- **Migration on the shared database.** Phase 1 changes existing rows. Take a
  backup first (NFR-4.4.5) and run the migration on a copy before the shared
  database.
- **Scoping bugs leak data between stores.** The cross-store tests in Phase 1
  are the guard. They are not optional.
- **Free-tier hosting sleeps.** Relevant to the live demo, not to the design.
