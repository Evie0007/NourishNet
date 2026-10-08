"""
Seeds the two demo accounts for the testing phase, plus enough shelf and
item data that both dashboards have something to show.

Run from backend/, where app/ and scripts/ live:

    # PowerShell
    $env:DEMO_STAFF_PASSWORD="..."; $env:DEMO_ORG_PASSWORD="..."
    python -m scripts.seed_demo

Passwords come from the environment on purpose. This repo is public — a
hardcoded demo password would be a live credential on a database holding
real partner data the moment someone reuses it.

Safe to re-run: existing rows are updated in place, not duplicated.
"""
import os
import secrets
import sys
from datetime import datetime, timedelta

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app import expiration, models, schemas, upc as upc_lib

# Real, check-digit-valid barcodes so the intake scanner can be demonstrated
# by typing one of these into the UPC field. They are ordinary retail codes;
# the names are ours, since the point is exercising the pipeline rather than
# claiming anything about a real product.
DEMO_PRODUCTS = [
    # upc,            name,                   category,   shelf life, restricted
    ("036000291452", "Whole Milk, 1 gal",      "Dairy",     7,  False),
    ("038000356216", "Greek Yogurt 6-pack",    "Dairy",    14,  False),
    ("041196910759", "Sourdough Loaf",         "Bakery",    3,  False),
    ("028400157155", "Blueberry Muffins 4ct",  "Bakery",    4,  False),
    ("000284001199", "Bagels 6ct",             "Bakery",    5,  False),
    ("041500291208", "Whole Wheat Bread Loaf", "Bakery",    5,  False),
    ("681131022217", "Baby Spinach 5oz",       "Produce",   5,  False),
    ("000333836208", "Carrots 2lb Bag",        "Produce",  21,  False),
    ("000333837205", "Apples 3lb Bag",         "Produce",  21,  False),
    ("000420000451", "Bananas Bunch",          "Produce",   6,  False),
    ("073731000106", "Cheddar Block 8oz",      "Dairy",    60,  False),
    ("024000098621", "Canned Black Beans 15oz","Pantry",  730,  False),
    ("024000016304", "Canned Diced Tomatoes 14.5oz", "Pantry", 730, False),
    ("000284005098", "Pretzels 10oz",          "Pantry",  180,  False),
    ("002143000633", "Ground Beef 1lb 80/20",  "Meat",      4,  False),
    ("002144120422", "Chicken Breast 1lb",     "Meat",      3,  False),
    # Never auto-published, whatever the read looked like (NFR-4.8.6). Worth
    # having in the demo: it is the case where the automation declines to act.
    ("300871239609", "Infant Formula 12.4oz",  "Infant",  365,  True),
]

STAFF_EMAIL = os.getenv("DEMO_STAFF_EMAIL", "nourishnet26+staff@gmail.com")
ORG_EMAIL = os.getenv("DEMO_ORG_EMAIL", "nourishnet26+organizer@gmail.com")

DEMO_ORG_NAME = "Second Harvest Demo Pantry"
DEMO_ORG_EIN = "94-2960297"

# The project has three smart shelves at three different locations, so the demo
# has three. A "location" is a Store row; each one holds a single shelf.
#
# Addresses are ordinary San Jose streets and the coordinates are approximate,
# written down here rather than looked up: a demo pin that depends on a live
# geocoder can land somewhere else on presentation day. The seed owns these
# rows, so a re-run resets them to what is below.
#
# `legacy` is the name an earlier version of this script gave the same row.
# Looking it up too means a database seeded before the rename is renamed in
# place rather than left with a stale store beside the new one.
DEMO_LOCATIONS = [
    {
        # The one the demo staff account belongs to. Every store-side view is
        # scoped to a store, so without this link the staff login would see an
        # empty dashboard (NFR-4.6.1).
        "name": "TEST Shelf",
        "legacy": "Demo Market",
        "address": "123 S 1st St, San Jose, CA 95113",
        "coords": (37.3331, -121.8896),   # downtown San Jose
        "open": True,
    },
    {
        "name": "Willow Glen Shelf",
        "legacy": "Demo Market East",
        "address": "1150 Lincoln Ave, San Jose, CA 95125",
        "coords": (37.3016, -121.8996),   # Willow Glen
        "open": True,
    },
    {
        # Stocked but closed, so the pantry map has an "unavailable" shelf to
        # show without anyone having to stage one.
        "name": "Berryessa Shelf",
        "legacy": None,
        "address": "1700 Berryessa Rd, San Jose, CA 95133",
        "coords": (37.3727, -121.8700),   # Berryessa
        "open": False,
    },
]
DEMO_STORE_NAME = DEMO_LOCATIONS[0]["name"]

# What the two other shelves hold: (name, category, hours until sell-by,
# quantity). Everything is AVAILABLE, i.e. already offered to pantries. Items
# are one row per unit, so a quantity of 12 is twelve identical rows, which is
# what a scanned delivery produces and what the pantry's Quantity column
# counts. UPCs are filled in from the catalog above wherever the name matches.
EXTRA_SHELF_STOCK = {
    "Willow Glen Shelf": [
        ("Strawberries 1lb", "Produce", 30, 6),
        ("Bananas Bunch", "Produce", 20, 12),
        ("Baby Spinach 5oz", "Produce", 14, 8),
        ("Greek Yogurt 6-pack", "Dairy", 40, 10),
        ("Cheddar Block 8oz", "Dairy", 60, 4),
        ("Sourdough Loaf", "Bakery", 10, 5),
        ("Whole Wheat Bread Loaf", "Bakery", 18, 6),
        ("Canned Black Beans 15oz", "Pantry", 300, 24),
    ],
    "Berryessa Shelf": [
        ("Apples 3lb Bag", "Produce", 72, 10),
        ("Carrots 2lb Bag", "Produce", 96, 15),
        ("Whole Milk, 1 gal", "Dairy", 16, 8),
        ("Bagels 6ct", "Bakery", 22, 6),
        ("Blueberry Muffins 4ct", "Bakery", 12, 4),
    ],
}

# How many identical units of each AVAILABLE item the TEST Shelf holds. Anything
# not listed is a single unit.
TEST_QUANTITY = {
    "Whole Milk, 1 gal": 6,
    "Greek Yogurt 6-pack": 8,
    "Sourdough Loaf": 4,
    "Bananas Bunch": 10,
    "Whole Wheat Bread Loaf": 5,
    "Apples 3lb Bag": 8,
    "Canned Diced Tomatoes 14.5oz": 12,
}


def require_password(var: str) -> str:
    value = os.getenv(var)
    if not value:
        sys.exit(
            f"{var} is not set.\n"
            "Set both DEMO_STAFF_PASSWORD and DEMO_ORG_PASSWORD before seeding, "
            "and keep them out of the repo."
        )
    if len(value) < 12:
        sys.exit(f"{var} is shorter than 12 characters. Use something longer.")
    return value


def upsert_user(db, *, email, password, role, full_name, pantry_id=None, store_id=None):
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        user = models.User(email=email)
        db.add(user)
        action = "created"
    else:
        action = "updated"

    user.password_hash = hash_password(password)
    user.role = role
    user.full_name = full_name
    user.pantry_id = pantry_id
    user.store_id = store_id
    user.is_active = True
    print(f"  {action}: {email}  ({role.value})")
    return user


def main():
    staff_password = require_password("DEMO_STAFF_PASSWORD")
    org_password = require_password("DEMO_ORG_PASSWORD")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # The expiration policy has to exist before any item does: without a
        # rule, an item gets no deadlines, and no deadlines means the sweep
        # cannot see it. Idempotent, and it never overwrites a manager's edits.
        print("Expiration rules:")
        print(f"  seeded: {expiration.ensure_default_rules(db)} new rule(s)")

        print("UPC catalog:")
        for upc, name, category, shelf_life, restricted in DEMO_PRODUCTS:
            # Through the same normalizer the scanner uses, so a seeded row
            # is stored exactly as a scan of the same code would store it —
            # and so a typo in the table above fails here rather than
            # silently creating a product no scan will ever match.
            code = upc_lib.normalize(upc)
            product = db.query(models.Product).filter(models.Product.upc == code).first()
            if product is None:
                product = models.Product(upc=code, source="catalog")
                db.add(product)
            product.name = name
            product.category = category
            product.default_shelf_life_days = shelf_life
            product.donation_restricted = restricted
        db.flush()
        print(f"  {len(DEMO_PRODUCTS)} products (scan any of these at the intake desk)")

        print("Pantry:")
        pantry = (
            db.query(models.Pantry)
            .filter(models.Pantry.contact_email == ORG_EMAIL)
            .first()
        )
        if pantry is None:
            pantry = models.Pantry(contact_email=ORG_EMAIL)
            db.add(pantry)
            print(f"  created: {DEMO_ORG_NAME}")
        else:
            print(f"  updated: {DEMO_ORG_NAME}")
        pantry.org_name = DEMO_ORG_NAME
        pantry.ein = DEMO_ORG_EIN
        pantry.address = "750 Curtner Ave, San Jose, CA 95125"
        pantry.phone = "408-555-0142"
        # Pre-verified so the demo organizer can actually reserve. A real
        # registration starts unverified and waits on an admin (FR-7.3).
        pantry.verified = True
        db.flush()

        print("Locations (the three shelves on the pantry map):")
        locations = {}
        for spec in DEMO_LOCATIONS:
            names = [n for n in (spec["name"], spec["legacy"]) if n]
            found = db.query(models.Store).filter(models.Store.name.in_(names)).all()
            # Prefer a row already under the new name; otherwise adopt the
            # legacy one so it is renamed rather than duplicated.
            loc = next((r for r in found if r.name == spec["name"]), None) or (found[0] if found else None)
            verb = "updated"
            if loc is None:
                loc = models.Store()
                db.add(loc)
                verb = "created"
            loc.name = spec["name"]
            loc.address = spec["address"]
            loc.latitude, loc.longitude = spec["coords"]
            loc.active = True
            loc.open_for_pickup = spec["open"]
            db.flush()
            locations[spec["name"]] = loc
            state = "open" if spec["open"] else "closed"
            print(f"  {verb}: {spec['name']} ({state}) — {spec['address']}")
        store = locations[DEMO_STORE_NAME]

        print("Users:")
        upsert_user(
            db,
            email=STAFF_EMAIL,
            password=staff_password,
            role=models.UserRole.MANAGER,
            full_name="Demo Store Manager",
            store_id=store.id,
        )
        upsert_user(
            db,
            email=ORG_EMAIL,
            password=org_password,
            role=models.UserRole.ORG_COORDINATOR,
            full_name="Demo Pantry Coordinator",
            pantry_id=pantry.id,
        )

        # Sample inventory, only if the store is empty — re-running the
        # script shouldn't keep piling on shelves. Pass --with-inventory to
        # add it anyway, for a database that has shelves but nothing in a
        # demo-able state (e.g. every item already picked up).
        force_inventory = "--with-inventory" in sys.argv
        demo_codes = []
        if db.query(models.Shelf).filter(models.Shelf.store_id == store.id).count() == 0 or force_inventory:
            print("Sample inventory:")
            now = datetime.utcnow()
            # One physical smart shelf per location. The sensor readings are
            # fresh, so the shelf card does not show the stale-sensor warning.
            shelf = models.Shelf(
                store_id=store.id,
                name="TEST Shelf",
                location=store.address,
                camera_id="cam-test-1",
                current_temperature_c=4.1,
                current_humidity_pct=58.0,
                last_reading_at=now,
            )
            db.add(shelf)
            db.flush()

            items = [
                # name,                  sku,         category, status,                    +hours, upc
                # Already in the donation pool — the organizer sees these.
                ("Whole Milk, 1 gal",    "DAIRY-001", "Dairy", models.ItemStatus.AVAILABLE,     12, "036000291452"),
                ("Greek Yogurt 6-pack",  "DAIRY-014", "Dairy", models.ItemStatus.AVAILABLE,     20, "038000356216"),
                ("Sourdough Loaf",       "BAKE-003",  "Bakery", models.ItemStatus.AVAILABLE,      8, "041196910759"),
                # Waiting on staff review — the staff review queue.
                ("Sliced Turkey 12oz",   None,        "Deli", models.ItemStatus.NEEDS_REVIEW,  30, None),
                ("Blueberry Muffins 4ct","BAKE-021",  "Bakery", models.ItemStatus.NEEDS_REVIEW,  16, "028400157155"),
                # Approaching sell-by — the near-expiry panel. The sweep will
                # walk these forward on its own within a minute of starting up,
                # which is the point of seeding them here.
                ("Baby Spinach 5oz",     "PROD-118",  "Produce", models.ItemStatus.IN_STOCK,      26, "681131022217"),
                ("Strawberries 1lb",     "PROD-092",  "Produce", models.ItemStatus.IN_STOCK,      40, None),
                ("Cheddar Block 8oz",    "DAIRY-077", "Dairy", models.ItemStatus.IN_STOCK,     400, "073731000106"),
                # Already spoken for — these back the three reservations
                # below, so the staff Pickup schedule and the organizer's
                # QR code both have something to show on first load.
                #
                # Their sell-by is deliberately generous. sweep() moves any
                # RESERVED item past its discard_after to EXPIRED_HOLD, so a
                # short-dated item here would empty the schedule card within
                # a minute of seeding and look like a bug in the feature
                # rather than a correctly-working safety rule.
                ("Butter 1lb",           "DAIRY-031", "Dairy", models.ItemStatus.RESERVED,      48, None),
                ("Heavy Cream 1qt",      "DAIRY-052", "Dairy", models.ItemStatus.RESERVED,      60, None),
                ("Bagels 6ct",           "BAKE-011",  "Bakery", models.ItemStatus.PICKED_UP,     36, "000284001199"),
                # More of the everyday grocery mix, so the demo isn't just
                # dairy and bakery — produce, pantry staples, and meat each
                # get a representative or two.
                ("Whole Wheat Bread Loaf","BAKE-034",  "Bakery", models.ItemStatus.AVAILABLE,     60, "041500291208"),
                ("Carrots 2lb Bag",      "PROD-201",  "Produce", models.ItemStatus.IN_STOCK,     300, "000333836208"),
                ("Apples 3lb Bag",       "PROD-207",  "Produce", models.ItemStatus.AVAILABLE,     80, "000333837205"),
                ("Bananas Bunch",        "PROD-205",  "Produce", models.ItemStatus.AVAILABLE,       6, "000420000451"),
                ("Canned Black Beans 15oz","PANT-010","Pantry", models.ItemStatus.IN_STOCK,   17000, "024000098621"),
                ("Canned Diced Tomatoes 14.5oz","PANT-011","Pantry", models.ItemStatus.AVAILABLE,  2, "024000016304"),
                ("Pretzels 10oz",        "PANT-022",  "Pantry", models.ItemStatus.IN_STOCK,    4200, "000284005098"),
                # Meat's auto_publish is False (staff always inspect before
                # any meat is offered), so these are the demo's proof that
                # the automation correctly declines to act on its own.
                ("Ground Beef 1lb 80/20","MEAT-004",  "Meat", models.ItemStatus.NEEDS_REVIEW,   18, None),
                ("Chicken Breast 1lb",   "MEAT-009",  "Meat", models.ItemStatus.IN_STOCK,       14, "002144120422"),
            ]
            rules = expiration.load_rules(db)
            created_items = {}
            for name, sku, category, item_status, hours, upc in items:
                code = upc_lib.normalize(upc) if upc else None
                product = upc_lib.find_product(db, code) if code else None
                item = models.Item(
                    name=name,
                    sku=sku,
                    upc=code,
                    product_id=product.id if product else None,
                    category=category,
                    shelf_id=shelf.id,
                    status=item_status,
                    sell_by_date=now + timedelta(hours=hours),
                    date_label_type=models.DateLabelType.SELL_BY,
                    date_source=models.DateSource.MANUAL,
                    arrival_date=now - timedelta(days=2),
                    store_id=store.id,
                )
                if item_status == models.ItemStatus.NEEDS_REVIEW:
                    # The two ways an item lands in review: confidence below
                    # the 0.95 threshold, or a confident read whose SKU
                    # cross-check failed. One of each, so the queue shows
                    # both branches.
                    item.ocr_raw_text = f"SELL BY {(now + timedelta(hours=hours)).strftime('%m/%d/%Y')}"
                    item.ocr_confidence = 0.82 if sku is None else 0.97
                    item.sku_match_confirmed = False
                    item.date_source = models.DateSource.OCR
                # Without deadlines an item is invisible to the sweep, so a
                # seeded one would sit still while scanned stock moved around
                # it — and the demo would look broken for the wrong reason.
                expiration.apply_rules_to_item(db, item, rules=rules)
                db.add(item)
                created_items[name] = item
                # A delivery arrives as several identical units, one row each.
                # Only offered food is multiplied: the review and reserved
                # rows below stay single so each demo state is one clear row.
                if item_status == models.ItemStatus.AVAILABLE:
                    for _ in range(TEST_QUANTITY.get(name, 1) - 1):
                        db.add(models.Item(**{
                            c.name: getattr(item, c.name)
                            for c in models.Item.__table__.columns
                            if c.name not in ("id", "created_at", "updated_at")
                        }))
            db.flush()
            units = db.query(models.Item).filter(models.Item.store_id == store.id).count()
            print(f"  created: 1 shelf, {units} items ({len(items)} kinds)")

            # Two orders so both dashboards have real state on first load,
            # shown the way pantries use the portal: an order with its own ID
            # and one QR code covering several items.
            #   - a pending order of two items, later today
            #   - an order already collected, so the organizer's history and
            #     the staff schedule are not single-valued
            #
            # Built directly rather than through crud.create_order, for the
            # same reason the items above are: that function validates and
            # commits a live request, and the collected one could not be
            # expressed through it at all.
            orders = [
                (["Butter 1lb", "Heavy Cream 1qt"], now + timedelta(hours=3), models.ReservationStatus.PENDING),
                (["Bagels 6ct"], now - timedelta(hours=2), models.ReservationStatus.PICKED_UP),
            ]
            for item_names, scheduled, res_status in orders:
                order = models.PickupOrder(
                    pantry_id=pantry.id,
                    store_id=store.id,
                    qr_code=secrets.token_urlsafe(16),
                    scheduled_pickup_at=scheduled,
                    hold_expires_at=scheduled + schemas.PICKUP_GRACE,
                )
                db.add(order)
                db.flush()
                for item_name in item_names:
                    db.add(
                        models.Reservation(
                            item_id=created_items[item_name].id,
                            pantry_id=pantry.id,
                            order_id=order.id,
                            status=res_status,
                            reserved_at=now - timedelta(hours=1),
                            scheduled_pickup_at=scheduled,
                            hold_expires_at=scheduled + schemas.PICKUP_GRACE,
                            # The order carries the one code.
                            qr_code=None,
                            picked_up_at=(
                                scheduled + timedelta(minutes=15)
                                if res_status == models.ReservationStatus.PICKED_UP
                                else None
                            ),
                        )
                    )
                if res_status == models.ReservationStatus.PENDING:
                    demo_codes.append((" + ".join(item_names), order.qr_code))
            print(f"  created: {len(orders)} orders")

            # One scan left mid-flow, so the Intake tab has something in its
            # confirmation queue on first load: a barcode that resolved, a
            # date read below the confidence threshold, and a second date on
            # the label to choose between.
            sell_by = now + timedelta(hours=18)
            milk = upc_lib.find_product(db, upc_lib.normalize("036000291452"))
            db.add(
                models.IntakeScan(
                    store_id=store.id,
                    upc=milk.upc,
                    product_id=milk.id,
                    shelf_id=shelf.id,
                    quantity=4,
                    ocr_raw_text=(
                        f"GRADE A WHOLE MILK\nPKD {(now - timedelta(days=3)).strftime('%m/%d/%y')}\n"
                        f"SELL BY {sell_by.strftime('%m/%d/%y')}"
                    ),
                    ocr_confidence=0.93,
                    date_confidence=0.71,
                    detected_date=sell_by,
                    detected_label_type=models.DateLabelType.SELL_BY,
                    date_candidates=[
                        {
                            "text": (now - timedelta(days=3)).strftime("%m/%d/%y"),
                            "date": (now - timedelta(days=3)).isoformat(),
                            "label_type": "packed_on",
                            "confidence": 0.88,
                        },
                        {
                            "text": sell_by.strftime("%m/%d/%y"),
                            "date": sell_by.isoformat(),
                            "label_type": "sell_by",
                            "confidence": 0.71,
                        },
                    ],
                    status=models.ScanStatus.PENDING,
                )
            )
            print("  created: 1 intake scan waiting on confirmation")
        else:
            print("Sample inventory: skipped (shelves already exist)")

        # The other two locations' shelves and food. Each is stocked only while
        # it has no items, so a re-run does not keep piling stock onto it.
        rules = expiration.load_rules(db)
        for loc_name, stock in EXTRA_SHELF_STOCK.items():
            loc = locations[loc_name]
            if db.query(models.Item).filter(models.Item.store_id == loc.id).count() > 0:
                print(f"{loc_name}: skipped (already stocked)")
                continue
            print(f"{loc_name}:")
            loc_shelf = models.Shelf(
                store_id=loc.id,
                name=loc_name,
                location=loc.address,
                camera_id=f"cam-{loc_name.split()[0].lower()}-1",
                current_temperature_c=3.8,
                current_humidity_pct=60.0,
                last_reading_at=datetime.utcnow(),
            )
            db.add(loc_shelf)
            db.flush()
            loc_now = datetime.utcnow()
            by_name = {p[1]: p[0] for p in DEMO_PRODUCTS}
            sell_by_for = {}
            for name, category, hours, quantity in stock:
                code = upc_lib.normalize(by_name[name]) if name in by_name else None
                product = upc_lib.find_product(db, code) if code else None
                # Identical units must share one sell-by to read as one row.
                sell_by_for[name] = loc_now + timedelta(hours=hours)
                for _ in range(quantity):
                    loc_item = models.Item(
                        store_id=loc.id,
                        shelf_id=loc_shelf.id,
                        name=name,
                        category=category,
                        upc=code,
                        product_id=product.id if product else None,
                        status=models.ItemStatus.AVAILABLE,
                        sell_by_date=sell_by_for[name],
                        date_label_type=models.DateLabelType.SELL_BY,
                        date_source=models.DateSource.MANUAL,
                        arrival_date=loc_now - timedelta(days=1),
                    )
                    expiration.apply_rules_to_item(db, loc_item, rules=rules)
                    db.add(loc_item)
            units = sum(q for *_, q in stock)
            print(f"  created: 1 shelf, {units} available items ({len(stock)} kinds)")

        db.commit()
        print("\nDone. Sign in at the login page with:")
        print(f"  Staff      {STAFF_EMAIL}")
        print(f"  Organizer  {ORG_EMAIL}")

        if demo_codes:
            # So the pickup scanner can be exercised without signing in as
            # the organizer on a second device. These are throwaway tokens
            # against a demo database, regenerated on every seed.
            print("\nPending pickup codes (demo only):")
            for item_name, code in demo_codes:
                print(f"  {item_name:<20} {code}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
