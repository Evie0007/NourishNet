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
    ("681131022217", "Baby Spinach 5oz",       "Produce",   5,  False),
    ("073731000106", "Cheddar Block 8oz",      "Dairy",    60,  False),
    # Never auto-published, whatever the read looked like (NFR-4.8.6). Worth
    # having in the demo: it is the case where the automation declines to act.
    ("300871239609", "Infant Formula 12.4oz",  "Infant",  365,  True),
]

STAFF_EMAIL = os.getenv("DEMO_STAFF_EMAIL", "nourishnet26+staff@gmail.com")
ORG_EMAIL = os.getenv("DEMO_ORG_EMAIL", "nourishnet26+organizer@gmail.com")

DEMO_ORG_NAME = "Second Harvest Demo Pantry"
DEMO_ORG_EIN = "94-2960297"


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


def upsert_user(db, *, email, password, role, full_name, pantry_id=None):
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

        print("Users:")
        upsert_user(
            db,
            email=STAFF_EMAIL,
            password=staff_password,
            role=models.UserRole.MANAGER,
            full_name="Demo Store Manager",
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
        if db.query(models.Shelf).count() == 0 or force_inventory:
            print("Sample inventory:")
            now = datetime.utcnow()
            shelves = [
                models.Shelf(name="Shelf A — Dairy", location="Aisle 3, north end", camera_id="cam-a1"),
                models.Shelf(name="Shelf B — Bakery", location="Front of store", camera_id="cam-b1"),
                models.Shelf(name="Shelf C — Produce", location="Aisle 1", camera_id="cam-c1"),
            ]
            for shelf in shelves:
                db.add(shelf)
            db.flush()
            shelves[0].current_temperature_c = 3.4
            shelves[0].current_humidity_pct = 62.0
            shelves[0].last_reading_at = now
            shelves[1].current_temperature_c = 20.8
            shelves[1].current_humidity_pct = 44.0
            shelves[1].last_reading_at = now - timedelta(minutes=4)

            items = [
                # name,                  sku,         category, shelf, status,                    +hours, upc
                # Already in the donation pool — the organizer sees these.
                ("Whole Milk, 1 gal",    "DAIRY-001", "Dairy",  0, models.ItemStatus.AVAILABLE,     12, "036000291452"),
                ("Greek Yogurt 6-pack",  "DAIRY-014", "Dairy",  0, models.ItemStatus.AVAILABLE,     20, "038000356216"),
                ("Sourdough Loaf",       "BAKE-003",  "Bakery", 1, models.ItemStatus.AVAILABLE,      8, "041196910759"),
                # Waiting on staff review — the staff review queue.
                ("Sliced Turkey 12oz",   None,        "Deli",   0, models.ItemStatus.NEEDS_REVIEW,  30, None),
                ("Blueberry Muffins 4ct","BAKE-021",  "Bakery", 1, models.ItemStatus.NEEDS_REVIEW,  16, "028400157155"),
                # Approaching sell-by — the near-expiry panel. The sweep will
                # walk these forward on its own within a minute of starting up,
                # which is the point of seeding them here.
                ("Baby Spinach 5oz",     "PROD-118",  "Produce",2, models.ItemStatus.IN_STOCK,      26, "681131022217"),
                ("Strawberries 1lb",     "PROD-092",  "Produce",2, models.ItemStatus.IN_STOCK,      40, None),
                ("Cheddar Block 8oz",    "DAIRY-077", "Dairy",  0, models.ItemStatus.IN_STOCK,     400, "073731000106"),
                # Already spoken for — these back the three reservations
                # below, so the staff Pickup schedule and the organizer's
                # QR code both have something to show on first load.
                #
                # Their sell-by is deliberately generous. sweep() moves any
                # RESERVED item past its discard_after to EXPIRED_HOLD, so a
                # short-dated item here would empty the schedule card within
                # a minute of seeding and look like a bug in the feature
                # rather than a correctly-working safety rule.
                ("Butter 1lb",           "DAIRY-031", "Dairy",  0, models.ItemStatus.RESERVED,      48, None),
                ("Heavy Cream 1qt",      "DAIRY-052", "Dairy",  0, models.ItemStatus.RESERVED,      60, None),
                ("Bagels 6ct",           "BAKE-011",  "Bakery", 1, models.ItemStatus.PICKED_UP,     36, None),
            ]
            rules = expiration.load_rules(db)
            created_items = {}
            for name, sku, category, shelf_idx, item_status, hours, upc in items:
                code = upc_lib.normalize(upc) if upc else None
                product = upc_lib.find_product(db, code) if code else None
                item = models.Item(
                    name=name,
                    sku=sku,
                    upc=code,
                    product_id=product.id if product else None,
                    category=category,
                    shelf_id=shelves[shelf_idx].id,
                    status=item_status,
                    sell_by_date=now + timedelta(hours=hours),
                    date_label_type=models.DateLabelType.SELL_BY,
                    date_source=models.DateSource.MANUAL,
                    arrival_date=now - timedelta(days=2),
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
            db.flush()
            print(f"  created: {len(shelves)} shelves, {len(items)} items")

            # Three reservations so both dashboards have real state on
            # first load: one pickup later today, one tomorrow to exercise
            # the 24-hour horizon, and one already collected so the status
            # column and the organizer's History card are not single-valued.
            #
            # Built directly rather than through crud.create_reservation,
            # for the same reason the items above are: that function
            # validates and commits a live request, and the collected one
            # could not be expressed through it at all.
            reservations = [
                ("Butter 1lb",      now + timedelta(hours=3),  models.ReservationStatus.PENDING),
                ("Heavy Cream 1qt", now + timedelta(hours=20), models.ReservationStatus.PENDING),
                ("Bagels 6ct",      now - timedelta(hours=2),  models.ReservationStatus.PICKED_UP),
            ]
            for item_name, scheduled, res_status in reservations:
                code = secrets.token_urlsafe(16)
                db.add(
                    models.Reservation(
                        item_id=created_items[item_name].id,
                        pantry_id=pantry.id,
                        status=res_status,
                        reserved_at=now - timedelta(hours=1),
                        scheduled_pickup_at=scheduled,
                        hold_expires_at=scheduled + schemas.PICKUP_GRACE,
                        qr_code=code,
                        picked_up_at=(
                            scheduled + timedelta(minutes=15)
                            if res_status == models.ReservationStatus.PICKED_UP
                            else None
                        ),
                    )
                )
                if res_status == models.ReservationStatus.PENDING:
                    demo_codes.append((item_name, code))
            print(f"  created: {len(reservations)} reservations")

            # One scan left mid-flow, so the Intake tab has something in its
            # confirmation queue on first load: a barcode that resolved, a
            # date read below the confidence threshold, and a second date on
            # the label to choose between.
            sell_by = now + timedelta(hours=18)
            milk = upc_lib.find_product(db, upc_lib.normalize("036000291452"))
            db.add(
                models.IntakeScan(
                    upc=milk.upc,
                    product_id=milk.id,
                    shelf_id=shelves[0].id,
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
