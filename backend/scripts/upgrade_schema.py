"""
Bring an existing database up to the current schema.

`Base.metadata.create_all` at app startup creates missing *tables*. It will
not add a column to a table that already exists, so a database created
before these changes has an `items` table with no `upc`, no `use_by_date`
and no deadline columns, and a `reservations` table with no
`scheduled_pickup_at` — and the app would fail on its first query against
them.

Run once per database, after deploying:

    python -m scripts.upgrade_schema

Idempotent: every step checks before it acts, so re-running is a no-op and
running against a fresh database does nothing but seed the rules.

This is not a migration system. It is the one-time bridge for the
databases that exist today. NFR-4.6.4 asks for Alembic before this project
holds data it cannot afford to drop, and that is still the right answer —
the next schema change should arrive as a migration, not as another script
like this one.
"""
import sys

from sqlalchemy import Enum as SAEnum, inspect, text

from app import expiration, models, schemas
from app.database import Base, SessionLocal, engine

# table -> column name -> DDL type, per dialect. The types differ enough
# between Postgres and SQLite that spelling them out beats trying to render
# them from the SQLAlchemy column objects.
NEW_COLUMNS = {
    "items": {
        "upc":             {"postgresql": "VARCHAR(14)", "sqlite": "VARCHAR(14)"},
        "product_id":      {"postgresql": "UUID",        "sqlite": "CHAR(32)"},
        "use_by_date":     {"postgresql": "TIMESTAMP",   "sqlite": "DATETIME"},
        "date_label_type": {"postgresql": "datelabeltype", "sqlite": "VARCHAR(9)"},
        "date_source":     {"postgresql": "datesource",   "sqlite": "VARCHAR(10)"},
        "donate_after":    {"postgresql": "TIMESTAMP",   "sqlite": "DATETIME"},
        "discard_after":   {"postgresql": "TIMESTAMP",   "sqlite": "DATETIME"},
        "unit_value":      {"postgresql": "NUMERIC(10,2)", "sqlite": "NUMERIC(10,2)"},
    },
    "reservations": {
        "scheduled_pickup_at": {"postgresql": "TIMESTAMP", "sqlite": "DATETIME"},
        # Multi-item pickups. pickup_orders is a new table, so create_all
        # makes it; this is the column on the existing table that points at it.
        "order_id": {"postgresql": "UUID REFERENCES pickup_orders(id)", "sqlite": "CHAR(32) REFERENCES pickup_orders(id)"},
    },
    # Existing stores stay open: the default is what keeps a location on the
    # pantry map as available after this runs.
    "stores": {
        "open_for_pickup": {"postgresql": "BOOLEAN NOT NULL DEFAULT TRUE", "sqlite": "BOOLEAN NOT NULL DEFAULT 1"},
    },
    "products": {
        "unit_value": {"postgresql": "NUMERIC(10,2)", "sqlite": "NUMERIC(10,2)"},
    },
    # Multi-store (NFR-4.6.1). The stores and brands tables are new, so
    # create_all makes them; these are the columns that point at them.
    "users": {
        "store_id": {"postgresql": "UUID REFERENCES stores(id)", "sqlite": "CHAR(32) REFERENCES stores(id)"},
        "brand_id": {"postgresql": "UUID REFERENCES brands(id)", "sqlite": "CHAR(32) REFERENCES brands(id)"},
    },
    "shelves": {
        "store_id": {"postgresql": "UUID REFERENCES stores(id)", "sqlite": "CHAR(32) REFERENCES stores(id)"},
    },
    "items": {
        "store_id": {"postgresql": "UUID REFERENCES stores(id)", "sqlite": "CHAR(32) REFERENCES stores(id)"},
    },
    "intake_scans": {
        "store_id": {"postgresql": "UUID REFERENCES stores(id)", "sqlite": "CHAR(32) REFERENCES stores(id)"},
    },
    "donation_records": {
        "store_id": {"postgresql": "UUID REFERENCES stores(id)", "sqlite": "CHAR(32) REFERENCES stores(id)"},
    },
}

# Name of the store that existing, unassigned rows are moved into. Rename it
# on the dashboard once that exists; until then the name is only a label.
DEFAULT_STORE_NAME = "Default store"

# Indexes the hot queries need (NFR-4.6.3). create_all adds these to new
# tables; existing tables need them stated.
NEW_INDEXES = [
    ("ix_items_status", "items", "status"),
    ("ix_items_sell_by_date", "items", "sell_by_date"),
    ("ix_items_category", "items", "category"),
    ("ix_items_upc", "items", "upc"),
    ("ix_items_donate_after", "items", "donate_after"),
    ("ix_items_discard_after", "items", "discard_after"),
    ("ix_reservations_status", "reservations", "status"),
    ("ix_reservations_hold_expires_at", "reservations", "hold_expires_at"),
    ("ix_reservations_scheduled_pickup_at", "reservations", "scheduled_pickup_at"),
    ("ix_reservations_order_id", "reservations", "order_id"),
    ("ix_users_store_id", "users", "store_id"),
    ("ix_shelves_store_id", "shelves", "store_id"),
    ("ix_items_store_id", "items", "store_id"),
    ("ix_intake_scans_store_id", "intake_scans", "store_id"),
    ("ix_donation_records_store_id", "donation_records", "store_id"),
]


def create_enum_types(connection) -> list[str]:
    """
    Postgres needs the enum type to exist before a column can use it.

    create_all makes the types for the new tables, but the two new *columns*
    on the existing items table reference types nothing else created yet.
    SQLite has no enum types and skips this entirely.
    """
    if engine.dialect.name != "postgresql":
        return []

    made = []
    for enum_class in (models.DateLabelType, models.DateSource, models.ScanStatus):
        sa_enum = SAEnum(enum_class)
        sa_enum.create(connection, checkfirst=True)
        made.append(sa_enum.name)
    return made


def add_missing_columns(connection) -> list[str]:
    # inspect(connection), not inspect(engine): main() runs this inside a
    # transaction, and an engine-level inspector checks out a *different*
    # pooled connection that cannot see uncommitted DDL. That made
    # add_missing_indexes below skip the index on any column this function
    # had just added, so it only appeared on a second run.
    dialect = engine.dialect.name
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())

    added = []
    for table, columns in NEW_COLUMNS.items():
        if table not in tables:
            continue  # create_all will have made it complete already
        existing = {c["name"] for c in inspector.get_columns(table)}
        for column, types in columns.items():
            if column in existing:
                continue
            ddl_type = types.get(dialect, types["sqlite"])
            connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}'))
            added.append(f"{table}.{column}")
    return added


def add_missing_indexes(connection) -> list[str]:
    inspector = inspect(connection)  # see add_missing_columns
    tables = set(inspector.get_table_names())

    added = []
    for index_name, table, column in NEW_INDEXES:
        if table not in tables:
            continue
        existing = {i["name"] for i in inspector.get_indexes(table)}
        if index_name in existing:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if column not in columns:
            continue
        connection.execute(
            text(f'CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})')
        )
        added.append(index_name)
    return added


def backfill_deadlines(db) -> int:
    """
    Give pre-existing items the deadlines the sweep reads.

    Without this, every item stocked before the rules existed has
    donate_after and discard_after set to NULL — and NULL means "invisible
    to the sweep," so that whole cohort would sit on the shelf forever
    while new stock moved correctly around it. The silent version of this
    bug is much worse than a loud one, which is why it runs here rather
    than being left to happen lazily.

    Terminal items are skipped: their history is the donation record and is
    not ours to rewrite (FR-11.1).
    """
    items = (
        db.query(models.Item)
        .filter(
            models.Item.status.notin_([
                models.ItemStatus.PICKED_UP,
                models.ItemStatus.DISCARDED,
                models.ItemStatus.EXPIRED_HOLD,
            ])
        )
        .all()
    )
    rules = expiration.load_rules(db)
    changed = 0
    for item in items:
        before = (item.donate_after, item.discard_after)
        expiration.apply_rules_to_item(db, item, rules=rules)
        if (item.donate_after, item.discard_after) != before:
            changed += 1
    db.commit()
    return changed


def backfill_scheduled_pickup(db) -> int:
    """
    Give pre-existing pending reservations a scheduled pickup time, derived
    backwards from the hold they already have.

    The direction matters. A live reservation's hold_expires_at is a promise
    already made to an organization; inventing a schedule and re-deriving
    the hold from it would either shorten someone's window — they lose food
    they were told they could collect — or lengthen it, keeping the item out
    of the pool longer than anyone agreed. Deriving the schedule *from* the
    hold changes nothing observable. It only fills in a displayed time, and
    it makes the new invariant (hold = slot + PICKUP_GRACE) true for every
    live row, so the dashboards do not need a special case.

    Terminal reservations keep NULL, for the same reason backfill_deadlines
    skips terminal items: their history is the donation record and is not
    ours to rewrite (FR-11.1).

    A row whose hold has already lapsed is still PENDING only because the
    sweep has not run; it will be EXPIRED within a minute of startup.
    Backfilling it produces a pickup time in the past, which is both
    harmless and honest, so it is not special-cased.
    """
    stale = (
        db.query(models.Reservation)
        .filter(models.Reservation.status == models.ReservationStatus.PENDING)
        .filter(models.Reservation.scheduled_pickup_at.is_(None))
        .all()
    )
    for res in stale:
        res.scheduled_pickup_at = res.hold_expires_at - schemas.PICKUP_GRACE
    db.commit()
    return len(stale)


def assign_unowned_rows_to_default_store(db) -> dict[str, int]:
    """
    Move rows that predate multi-store into one default store.

    Before this change there was one implicit store, so every shelf, item,
    scan and donation record belongs to it. Without this step those rows
    have a NULL store_id and would be invisible to every store account, which
    is the safe failure but would look like missing stock on the first day.

    Only rows with no store are touched. A row that already has one keeps it,
    and a second run finds nothing to move.
    """
    tables = {
        "shelves": models.Shelf,
        "items": models.Item,
        "intake_scans": models.IntakeScan,
        "donation_records": models.DonationRecord,
    }
    counts = {
        name: db.query(model).filter(model.store_id.is_(None)).count()
        for name, model in tables.items()
    }
    if not any(counts.values()):
        return {}

    store = db.query(models.Store).filter(models.Store.name == DEFAULT_STORE_NAME).first()
    if store is None:
        store = models.Store(name=DEFAULT_STORE_NAME, active=True)
        db.add(store)
        db.flush()

    for model in tables.values():
        db.query(model).filter(model.store_id.is_(None)).update(
            {model.store_id: store.id}, synchronize_session=False
        )
    db.commit()
    return {name: n for name, n in counts.items() if n}


def main() -> None:
    print(f"Database: {engine.dialect.name}")

    print("\nTables:")
    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine)
    created = sorted(set(inspect(engine).get_table_names()) - before)
    print(f"  created: {', '.join(created) if created else '(none — already present)'}")

    with engine.begin() as connection:
        made = create_enum_types(connection)
        if made:
            print(f"\nEnum types:\n  ensured: {', '.join(made)}")

        print("\nColumns:")
        added = add_missing_columns(connection)
        print(f"  added: {', '.join(added) if added else '(none — already present)'}")

        print("\nIndexes:")
        indexed = add_missing_indexes(connection)
        print(f"  created: {', '.join(indexed) if indexed else '(none — already present)'}")

    db = SessionLocal()
    try:
        print("\nExpiration rules:")
        seeded = expiration.ensure_default_rules(db)
        print(f"  seeded: {seeded} new rule(s)")

        print("\nBackfilling deadlines on existing items:")
        changed = backfill_deadlines(db)
        print(f"  updated: {changed} item(s)")

        print("\nBackfilling scheduled pickup times on pending reservations:")
        scheduled = backfill_scheduled_pickup(db)
        print(f"  updated: {scheduled} reservation(s)")

        print("\nAssigning rows with no store to the default store:")
        moved = assign_unowned_rows_to_default_store(db)
        if moved:
            print(f"  moved into '{DEFAULT_STORE_NAME}': " + ", ".join(f"{k} {v}" for k, v in moved.items()))
        else:
            print("  (none — every row already belongs to a store)")
    finally:
        db.close()

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — a readable failure beats a traceback
        sys.exit(f"\nUpgrade failed: {exc}\n\nNothing partial was left behind — the DDL runs in one transaction.")
