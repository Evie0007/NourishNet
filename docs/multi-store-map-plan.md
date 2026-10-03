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
   updated in the same change.
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

## Brands and locations

- A **Brand** groups stores that belong to the same chain (for example, two
  locations of one market). `Store.brand_id` is nullable: an independent
  store has no brand.
- Store staff see their own store's shelves, items, and reservations.
- Staff of a brand can see every location under that brand, and nothing
  outside it. A user is tied to a brand, or to a single store, never to
  both. The token carries the scope, the same way it carries the role.
- Brand-level access is read-only in the first version: a brand manager can
  view inventory across locations, but staff actions (intake, confirm,
  pickup scan) still happen at the store where the shelf is.

## Visibility rules (unchanged, stated here so they are not lost)

- Pantries see only items in status `available`, and only from stores that
   are active. Items in review, near expiry, reserved by someone else, or
   discarded never appear.
- Store staff see only their own store's shelves, items, and reservations,
  unless the brand rule above applies.
- Organizers still need `verified = true` to reserve (FR-7.5).
- The pickup QR flow is unchanged: only store staff confirm a pickup.

## Phases

### Phase 1 — store scoping (backend, no UI change)

- Add a `Brand` table (`id`, `name`) and a `Store` table: `id`, `name`,
  `brand_id` (nullable), `address`, `latitude`, `longitude`, `active`.
- Add a brand or store scope to store-role users, set when the account is
  created and carried in the token.
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

## Open questions

- Should store staff see other stores' items on the pantry map? (Proposed: no.)
- Is a shared product catalog across stores acceptable? (Proposed: yes.)
- Does the presentation require the map to be working, or is the plan with
  Phase 1 done enough?

## Risks

- **Migration on the shared database.** Phase 1 changes existing rows. Take a
  backup first (NFR-4.4.5) and run the migration on a copy before the shared
  database.
- **Scoping bugs leak data between stores.** The cross-store tests in Phase 1
  are the guard. They are not optional.
- **Free-tier hosting sleeps.** Relevant to the live demo, not to the design.
