"""
Seeds the two demo accounts for the testing phase, plus enough shelf and
item data that both dashboards have something to show.

Run from the repo root:

    # PowerShell
    $env:DEMO_STAFF_PASSWORD="..."; $env:DEMO_ORG_PASSWORD="..."
    python -m scripts.seed_demo

Passwords come from the environment on purpose. This repo is public — a
hardcoded demo password would be a live credential on a database holding
real partner data the moment someone reuses it.

Safe to re-run: existing rows are updated in place, not duplicated.
"""
import os
import sys
from datetime import datetime, timedelta

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app import models

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
                # Already in the donation pool — the organizer sees these.
                ("Whole Milk, 1 gal", "DAIRY-001", "Dairy", 0, models.ItemStatus.AVAILABLE, 12),
                ("Greek Yogurt 6-pack", "DAIRY-014", "Dairy", 0, models.ItemStatus.AVAILABLE, 20),
                ("Sourdough Loaf", "BAKE-003", "Bakery", 1, models.ItemStatus.AVAILABLE, 8),
                # Waiting on staff review — the staff review queue.
                ("Sliced Turkey 12oz", None, "Deli", 0, models.ItemStatus.NEEDS_REVIEW, 30),
                ("Blueberry Muffins 4ct", "BAKE-021", "Bakery", 1, models.ItemStatus.NEEDS_REVIEW, 16),
                # Approaching sell-by — the near-expiry panel.
                ("Baby Spinach 5oz", "PROD-118", "Produce", 2, models.ItemStatus.IN_STOCK, 26),
                ("Strawberries 1lb", "PROD-092", "Produce", 2, models.ItemStatus.IN_STOCK, 40),
                ("Cheddar Block 8oz", "DAIRY-077", "Dairy", 0, models.ItemStatus.IN_STOCK, 400),
            ]
            for name, sku, category, shelf_idx, item_status, hours in items:
                item = models.Item(
                    name=name,
                    sku=sku,
                    category=category,
                    shelf_id=shelves[shelf_idx].id,
                    status=item_status,
                    sell_by_date=now + timedelta(hours=hours),
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
                db.add(item)
            print(f"  created: {len(shelves)} shelves, {len(items)} items")
        else:
            print("Sample inventory: skipped (shelves already exist)")

        db.commit()
        print("\nDone. Sign in at the login page with:")
        print(f"  Staff      {STAFF_EMAIL}")
        print(f"  Organizer  {ORG_EMAIL}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
