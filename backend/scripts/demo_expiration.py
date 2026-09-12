"""
Watch the automatic expiration rules work, without waiting three days.

The rules act on real dates, so a freshly stocked item does nothing
observable for a day or more. That makes the one part of the system that
runs unattended also the part nobody ever actually sees run. This script
fixes that: it stocks one item per interesting category, then steps a
simulated clock past their deadlines and prints what moved.

Nothing is faked. It is the real sweep, against a real database, reading
the real rules — only `now` is supplied rather than read off the wall
clock, which is the one parameter `expiration.sweep` already takes.

    python -m scripts.demo_expiration

Runs against a throwaway SQLite file by default, so it cannot touch the
team's shared database. To run it against whatever DATABASE_URL is
configured — and leave its demo items behind in that database:

    python -m scripts.demo_expiration --use-configured-db

What to look for in the output:

  · Bakery reaches `available` before its sell-by date. Day-old bread is
    the classic donation, so the rule publishes it 12 hours early.
  · Meat reaches `near_expiry` and stops. Its rule sets auto_publish=false,
    so no amount of elapsed time will publish it — a person must.
  · Infant formula never leaves `in_stock` and is discarded from there.
    It is donation-restricted (NFR-4.8.6).
  · The reserved item goes to `expired_hold`, not `discarded`, and its
    reservation flips to `expired`. Something was promised to a pantry and
    then withdrawn; that is a different fact worth counting separately.
  · Pantry stock sits in `available` for a month. A best-by date on shelf-
    stable goods is a quality date, not a safety one.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

USE_CONFIGURED_DB = "--use-configured-db" in sys.argv

if not USE_CONFIGURED_DB:
    # Set before importing app.database, which reads DATABASE_URL at import.
    _path = os.path.join(tempfile.mkdtemp(prefix="nourishnet-demo-"), "demo.db")
    os.environ["DATABASE_URL"] = "sqlite:///" + _path.replace("\\", "/")
    os.environ.setdefault("JWT_SECRET", "demo-only-not-a-real-secret")

from app import crud, expiration, models, schemas  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402

# The clock starts before every deadline and ends after all of them.
SELL_BY = datetime.utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(days=9)
STEP_HOURS = 4
FROM_HOURS, TO_HOURS = -40, 40

STOCK = [
    # name,              category,  date kind
    ("Sourdough Loaf",   "Bakery",  models.DateLabelType.SELL_BY),
    ("Whole Milk 1 gal", "Dairy",   models.DateLabelType.SELL_BY),
    ("Ribeye 16oz",      "Meat",    models.DateLabelType.SELL_BY),
    ("Infant Formula",   "Infant",  models.DateLabelType.USE_BY),
    ("Black Beans 15oz", "Pantry",  models.DateLabelType.BEST_BY),
]


def stock_one(db, user, name, category, label_type):
    """Put one item in through the real intake path, not a direct insert —
    so this exercises the pipeline it is meant to demonstrate."""
    scan = crud.create_intake_scan(
        db,
        schemas.IntakeScanCreate(name_override=name, category_override=category, quantity=1),
        user.id,
    )
    _scan, [item] = crud.confirm_intake_scan(
        db,
        scan.id,
        schemas.IntakeConfirm(confirmed_date=SELL_BY, date_label_type=label_type),
        user.id,
    )
    return item


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        expiration.ensure_default_rules(db)

        # A throwaway operator and recipient. On the configured database
        # these are found by email rather than duplicated on a re-run.
        user = crud.get_user_by_email(db, "demo-expiration@example.com")
        if user is None:
            user = models.User(
                email="demo-expiration@example.com",
                password_hash="not-a-usable-login",
                role=models.UserRole.MANAGER,
                full_name="Expiration demo",
                is_active=False,   # cannot be signed into
            )
            db.add(user)
        pantry = (
            db.query(models.Pantry)
            .filter(models.Pantry.contact_email == "demo-pantry@example.com")
            .first()
        )
        if pantry is None:
            pantry = models.Pantry(
                org_name="Expiration demo pantry",
                ein="00-0000000",
                contact_email="demo-pantry@example.com",
                verified=True,
            )
            db.add(pantry)
        db.commit()

        items = [stock_one(db, user, *row) for row in STOCK]

        print(f"Database    : {engine.dialect.name}")
        print(f"Sell-by date: {SELL_BY}  (all five items)")
        print()
        print("Deadlines the rules computed at intake:")
        for item in items:
            rule = expiration.get_rule(db, item.category)
            print(
                f"  {item.name:18} {item.category:8} "
                f"offer={str(item.donate_after):19}  off-shelf={item.discard_after}"
                f"{'' if rule.auto_publish else '   (never offered automatically)'}"
            )

        names = [item.name for item in items]
        width = max(len(n) for n in names)
        print()
        print("  " + "clock".ljust(17) + " | " + " | ".join(n.ljust(width) for n in names))
        print("  " + "-" * (17 + 3 + sum(width + 3 for n in names)))

        reserved = False
        for hours in range(FROM_HOURS, TO_HOURS + 1, STEP_HOURS):
            now = SELL_BY + timedelta(hours=hours)
            crud.expire_stale_reservations(db)
            expiration.sweep(db, now=now)
            for item in items:
                db.refresh(item)

            marker = "  <- sell-by" if hours == 0 else ""
            print(
                "  " + now.strftime("%a %m-%d %H:%M").ljust(17) + " | "
                + " | ".join(i.status.value.ljust(width) for i in items) + marker
            )

            # Reserve the bread the moment it is offered, and let the hold
            # outlive the food, to show what happens to a promise that the
            # safety deadline overtakes.
            if not reserved and items[0].status == models.ItemStatus.AVAILABLE:
                crud.create_reservation(
                    db,
                    schemas.ReservationCreate(item_id=items[0].id, hold_minutes=60 * 24 * 30),
                    pantry.id,
                )
                print(
                    "  " + " " * 17 + " >  a pantry reserved the sourdough, on a hold long "
                    "enough to outlast the bread"
                )
                reserved = True

        print()
        print("Reservation outcome:")
        for res in (
            db.query(models.Reservation)
            .filter(models.Reservation.pantry_id == pantry.id)
            .all()
        ):
            item = db.get(models.Item, res.item_id)
            print(f"  {item.name}: reservation {res.status.value}, item {item.status.value}")

        print()
        print("Re-running the sweep at the same instant changes nothing (idempotent):")
        print(f"  {expiration.sweep(db, now=SELL_BY + timedelta(hours=TO_HOURS))}")

        if not USE_CONFIGURED_DB:
            print(f"\nThrowaway database: {engine.url.database}")
            print("Nothing was written to your configured DATABASE_URL.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
