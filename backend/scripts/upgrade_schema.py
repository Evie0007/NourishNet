"""
Bring an existing database up to the intake-pipeline schema.

`Base.metadata.create_all` at app startup creates missing *tables*. It will
not add a column to a table that already exists, so a database created
before this change has an `items` table with no `upc`, no `use_by_date`,
and no deadline columns — and the app would fail on its first query
against them.

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

from app import expiration, models
from app.database import Base, SessionLocal, engine

# column name -> DDL type, per dialect. The types differ enough between
# Postgres and SQLite that spelling them out beats trying to render them
# from the SQLAlchemy column objects.
NEW_ITEM_COLUMNS = {
    "upc":             {"postgresql": "VARCHAR(14)", "sqlite": "VARCHAR(14)"},
    "product_id":      {"postgresql": "UUID",        "sqlite": "CHAR(32)"},
    "use_by_date":     {"postgresql": "TIMESTAMP",   "sqlite": "DATETIME"},
    "date_label_type": {"postgresql": "datelabeltype", "sqlite": "VARCHAR(9)"},
    "date_source":     {"postgresql": "datesource",   "sqlite": "VARCHAR(10)"},
    "donate_after":    {"postgresql": "TIMESTAMP",   "sqlite": "DATETIME"},
    "discard_after":   {"postgresql": "TIMESTAMP",   "sqlite": "DATETIME"},
}

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
    dialect = engine.dialect.name
    existing = {c["name"] for c in inspect(engine).get_columns("items")}

    added = []
    for column, types in NEW_ITEM_COLUMNS.items():
        if column in existing:
            continue
        ddl_type = types.get(dialect, types["sqlite"])
        connection.execute(text(f'ALTER TABLE items ADD COLUMN {column} {ddl_type}'))
        added.append(column)
    return added


def add_missing_indexes(connection) -> list[str]:
    inspector = inspect(engine)
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

        print("\nColumns on items:")
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
    finally:
        db.close()

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — a readable failure beats a traceback
        sys.exit(f"\nUpgrade failed: {exc}\n\nNothing partial was left behind — the DDL runs in one transaction.")
