"""
Automatic expiration rules.

Two jobs:

1. **Resolve dates at intake.** Turn whatever intake produced — a date the
   OCR read and a person confirmed, a date typed by hand, or nothing at
   all — into the two deadlines that actually drive the system:

       donate_after   when the item may be published to the donation network
       discard_after  when it must come out, donated or not

2. **Act on those deadlines.** A sweep runs on a schedule and moves items
   through the lifecycle as the clock passes them, so a shelf does not
   depend on anyone remembering to look at it.

The policy lives in the expiration_rules table, one row per category, so a
manager can retune dairy without a deploy. Every number below has a reason
to be adjustable; none of them should be a constant in a route handler.

Safety invariants that hold regardless of how the rules are tuned:

  · A use-by date is never crossed. It caps discard_after outright — the
    category's margin applies only when the package carries no use-by
    (NFR-4.8.2).
  · Nothing publishes automatically if its category or product is marked
    restricted (NFR-4.8.6).
  · Nothing publishes out of needs_review. An unconfirmed read never
    reaches a recipient, which is the whole point of the review branch.
  · Discarding is never blocked or delayed by any of this (NFR-4.8.4).
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from . import models

logger = logging.getLogger(__name__)

# The fallback rule, applied to any category with no row of its own.
DEFAULT_CATEGORY = "*"

# Seeded on first startup. These are starting points chosen to be
# conservative, not measured values — revisit them against the store's own
# shrink data once there is some.
#
#   near_expiry_hours     how long before the sell-by date staff get warned
#   publish_offset_hours  when it joins the donation pool, relative to sell-by
#   discard_after_hours   the safety margin past sell-by, when no use-by exists
#   auto_publish          False = reaches near_expiry alone, but waits for a person
#   shelf_life_days       estimate used when a unit arrives with no legible date
DEFAULT_RULES = [
    # category,       near,  publish, discard, auto,  shelf_life, notes
    (DEFAULT_CATEGORY, 48,        0,      48,  True,        None, "Fallback for uncategorized stock."),
    ("Dairy",          24,       -6,      24,  True,           7, "Short margin and an early publish: cold chain is unforgiving."),
    ("Bakery",         24,      -12,      24,  True,           3, "Publishes before the sell-by — day-old bread is the classic donation."),
    ("Produce",        24,      -12,      12,  True,           5, "Judged by condition as much as by date; short margin past sell-by."),
    ("Deli",           12,        0,      12,  True,           4, "Prepared foods: narrow window either side."),
    ("Meat",           12,        0,       0,  False,          3, "Never auto-published. Staff inspect before any meat is offered."),
    ("Seafood",        12,        0,       0,  False,          2, "Never auto-published. Staff inspect before any seafood is offered."),
    ("Frozen",         72,        0,     720,  True,         180, "Frozen stock tolerates a long margin past the printed date."),
    ("Pantry",        168,        0,     720,  True,         365, "Shelf-stable: best-by dates are quality, not safety."),
    ("Beverages",     168,        0,     720,  True,         365, "Shelf-stable."),
    ("Infant",          0,        0,       0,  False,       None, "Infant formula and baby food are never auto-published (NFR-4.8.6)."),
]


def ensure_default_rules(db: Session) -> int:
    """
    Seed any missing rule rows. Called once at startup.

    Only inserts what is absent, so a manager's edits are never overwritten
    by a restart. Returns how many rows were created.
    """
    existing = {row.category for row in db.query(models.ExpirationRule.category).all()}
    created = 0
    for category, near, publish, discard, auto, shelf_life, notes in DEFAULT_RULES:
        if category in existing:
            continue
        db.add(
            models.ExpirationRule(
                category=category,
                near_expiry_hours=near,
                publish_offset_hours=publish,
                discard_after_hours=discard,
                auto_publish=auto,
                default_shelf_life_days=shelf_life,
                notes=notes,
            )
        )
        created += 1
    if created:
        db.commit()
    return created


RuleSet = dict[str, models.ExpirationRule]


def load_rules(db: Session) -> RuleSet:
    """
    Every rule, keyed by lowercased category.

    There are a dozen of these and the sweep consults one per item, so
    loading the table once beats a query per row — at 500 items that is the
    difference between one round trip a minute and five hundred.

    Lowercased because categories arrive from a catalog import, an external
    lookup, and a free-text box, and "dairy" and "Dairy" must not get
    different safety margins.
    """
    def fetch() -> RuleSet:
        return {r.category.strip().lower(): r for r in db.query(models.ExpirationRule).all()}

    rules = fetch()
    if DEFAULT_CATEGORY not in rules:
        # A database with no fallback rule would silently mean "no
        # deadlines," which reads downstream as "nothing ever expires."
        ensure_default_rules(db)
        rules = fetch()
    return rules


def rule_for(rules: RuleSet, category: Optional[str]) -> models.ExpirationRule:
    """Pick a category's rule out of a loaded set, falling back to "*"."""
    if category:
        found = rules.get(category.strip().lower())
        if found:
            return found
    return rules[DEFAULT_CATEGORY]


def get_rule(db: Session, category: Optional[str]) -> models.ExpirationRule:
    """
    The rule governing one category. Convenience for single-item callers;
    anything looping over items should use load_rules + rule_for so the
    table is read once rather than once per row.
    """
    return rule_for(load_rules(db), category)


# ---------- Resolving dates at intake ----------

def estimate_from_shelf_life(
    db: Session,
    *,
    category: Optional[str],
    product: Optional[models.Product],
    arrival: datetime,
) -> Optional[datetime]:
    """
    Estimate a date for a unit whose label could not be read.

    The product's own shelf life wins over its category's, since a catalog
    entry is specific and a category average is not. Returns None when
    neither knows — and a None here is correct behaviour, not a failure: an
    unknown product with an unreadable date has no defensible date, so the
    confirmation screen makes a person supply one.
    """
    days = product.default_shelf_life_days if product else None
    if days is None:
        days = get_rule(db, category).default_shelf_life_days
    return arrival + timedelta(days=days) if days else None


def scheduling_reference(
    item: models.Item, rule: models.ExpirationRule
) -> Optional[datetime]:
    """
    The date every other deadline is measured from.

    Normally the sell-by date — the point at which the store stops selling
    an item and NourishNet's whole premise begins. Some packaging carries
    only a use-by date though (deli counters, prepared food), and reading
    the category's safety margin backwards from it recovers the retail
    deadline the package never printed. Without this, a use-by-only item
    has no donation window at all: it would sit in stock until its safety
    deadline and then be discarded, which is the exact waste the system
    exists to prevent.
    """
    if item.sell_by_date:
        return item.sell_by_date
    if item.use_by_date:
        return item.use_by_date - timedelta(hours=rule.discard_after_hours)
    return None


def apply_rules_to_item(
    db: Session, item: models.Item, rules: Optional[RuleSet] = None
) -> models.Item:
    """
    Recompute donate_after and discard_after from the item's dates.

    Call this whenever a date changes — at intake, and again whenever a
    reviewer corrects one. The deadlines are stored rather than derived at
    query time so the sweep is an indexed comparison, and so staff can see
    on screen exactly when each transition will fire rather than having to
    trust that it will.

    Pass `rules` when calling this in a loop, so the rule table is read once
    instead of once per item.
    """
    rule = rule_for(rules, item.category) if rules else get_rule(db, item.category)

    # The safety deadline. A printed use-by date is an absolute limit and
    # overrides the category margin; without one, the margin past the
    # sell-by date is the best estimate available.
    if item.use_by_date:
        item.discard_after = item.use_by_date
    elif item.sell_by_date:
        item.discard_after = item.sell_by_date + timedelta(hours=rule.discard_after_hours)
    else:
        item.discard_after = None

    reference = scheduling_reference(item, rule)
    if reference:
        donate_after = reference + timedelta(hours=rule.publish_offset_hours)
        # A publish window that opens after the safety deadline is not a
        # window. Leaving donate_after None keeps the item out of the
        # automatic publish path and in front of a person.
        item.donate_after = (
            donate_after
            if item.discard_after is None or donate_after < item.discard_after
            else None
        )
    else:
        item.donate_after = None

    return item


# A pantry needs time to collect and distribute. An item offered inside
# this window of its safety deadline cannot realistically be used, so
# offering it would only move the waste rather than prevent it.
MIN_USABLE_WINDOW = timedelta(hours=12)


def may_auto_publish(
    db: Session,
    item: models.Item,
    rules: Optional[RuleSet] = None,
    now: Optional[datetime] = None,
) -> bool:
    """
    Whether the sweep is allowed to publish this item without a person.

    Every clause here is a reason a human should look first, not a
    performance optimization.
    """
    now = now or datetime.utcnow()

    if item.donate_after is None:
        return False
    rule = rule_for(rules, item.category) if rules else get_rule(db, item.category)
    if not rule.auto_publish:
        return False
    if item.product and item.product.donation_restricted:
        return False
    # A use-by date is a safety limit, so an item carrying one is offered
    # only while it is still comfortably inside it.
    if item.use_by_date and item.discard_after:
        if item.discard_after - now < MIN_USABLE_WINDOW:
            return False
    return True


# ---------- The sweep ----------

def sweep(db: Session, now: Optional[datetime] = None) -> dict[str, int]:
    """
    Advance every item the clock has caught up with. Returns a count per
    transition, which is what the scheduler logs.

    Ordered most-urgent first so an item that is simultaneously due to be
    published and due to be discarded is discarded, never published.

    Idempotent and safe to run concurrently with itself (FR-10.3,
    NFR-4.4.1). Every step filters on the status it expects to find, so a
    second sweep over already-moved rows matches nothing and does nothing.
    The steps that have to load rows to make a per-row decision take
    FOR UPDATE SKIP LOCKED on Postgres, so a concurrent sweep passes over
    the rows this one holds instead of deciding about them twice; the
    clause compiles away on SQLite, where the write lock is global anyway.
    """
    now = now or datetime.utcnow()
    moved = {"discarded": 0, "released_from_reserved": 0, "published": 0, "near_expiry": 0}

    # Read once, consulted per item below. This runs every minute, so a
    # query per row would be a query per item per minute, forever.
    rules = load_rules(db)

    # 1. Past the safety deadline while unreserved — out of the pool.
    moved["discarded"] = (
        db.query(models.Item)
        .filter(models.Item.discard_after != None)  # noqa: E711
        .filter(models.Item.discard_after <= now)
        .filter(
            models.Item.status.in_([
                models.ItemStatus.IN_STOCK,
                models.ItemStatus.NEAR_EXPIRY,
                models.ItemStatus.AVAILABLE,
                models.ItemStatus.NEEDS_REVIEW,
            ])
        )
        .update({models.Item.status: models.ItemStatus.DISCARDED}, synchronize_session=False)
    )

    # 2. Past the safety deadline while reserved. The pantry's hold does not
    #    survive the food going bad, so the reservation is released and the
    #    item goes out — but into expired_hold, not discarded, because
    #    something was promised to someone and then withdrawn. That is a
    #    different fact about the item and worth being able to count.
    stale_reserved = (
        db.query(models.Item)
        .filter(models.Item.discard_after != None)  # noqa: E711
        .filter(models.Item.discard_after <= now)
        .filter(models.Item.status == models.ItemStatus.RESERVED)
        .with_for_update(skip_locked=True)
        .all()
    )
    for item in stale_reserved:
        pending = (
            db.query(models.Reservation)
            .filter(models.Reservation.item_id == item.id)
            .filter(models.Reservation.status == models.ReservationStatus.PENDING)
            .all()
        )
        for reservation in pending:
            reservation.status = models.ReservationStatus.EXPIRED
        item.status = models.ItemStatus.EXPIRED_HOLD
        moved["released_from_reserved"] += 1

    # 3. Eligible and past its publish time — into the donation network.
    #    Filtered in Python rather than SQL because may_auto_publish reads
    #    the category rule and the product's restriction flag; the SQL
    #    prefilter keeps that loop to the handful of rows actually due.
    due_to_publish = (
        db.query(models.Item)
        .filter(models.Item.donate_after != None)  # noqa: E711
        .filter(models.Item.donate_after <= now)
        .filter(
            models.Item.status.in_([models.ItemStatus.IN_STOCK, models.ItemStatus.NEAR_EXPIRY])
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    for item in due_to_publish:
        if may_auto_publish(db, item, rules=rules, now=now):
            item.status = models.ItemStatus.AVAILABLE
            moved["published"] += 1

    # 4. Approaching its sell-by date — flag it so staff see it coming.
    #    This is the writer that the near_expiry state never had (FR-6.4):
    #    with rules driving transitions, it stops being a derived condition
    #    and becomes a real one, set at a known time by a known actor.
    #
    #    The exact threshold is per-category and cannot be expressed in SQL
    #    without a join, but the widest lead time any rule uses bounds it:
    #    nothing dated beyond that horizon can be near expiry under any
    #    rule. Without this the sweep would load every in-stock item in the
    #    store, once a minute, to decide almost all of them aren't due.
    horizon = timedelta(hours=max(rule.near_expiry_hours for rule in rules.values()))
    # A use-by-only item's reference date sits one category margin earlier
    # than the date printed on it, so its horizon is that much wider.
    use_by_horizon = horizon + timedelta(
        hours=max(rule.discard_after_hours for rule in rules.values())
    )
    near_expiry_candidates = (
        db.query(models.Item)
        .filter(models.Item.status == models.ItemStatus.IN_STOCK)
        .filter(
            (models.Item.sell_by_date <= now + horizon)
            | (
                (models.Item.sell_by_date == None)  # noqa: E711
                & (models.Item.use_by_date <= now + use_by_horizon)
            )
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    for item in near_expiry_candidates:
        rule = rule_for(rules, item.category)
        reference = scheduling_reference(item, rule)
        if reference and reference - timedelta(hours=rule.near_expiry_hours) <= now:
            item.status = models.ItemStatus.NEAR_EXPIRY
            moved["near_expiry"] += 1

    db.commit()

    if any(moved.values()):
        logger.info("expiration sweep: %s", moved)
    return moved
