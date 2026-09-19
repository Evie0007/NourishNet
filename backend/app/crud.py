"""
CRUD + core business logic.

The confidence-branch and holding-window logic live here (not in the
routers) so they can be unit-tested directly without spinning up the API.
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import expiration, models, schemas, upc as upc_lib

# Section 13.1 of the proposal: 95% is the starting confidence target.
# Kept as a module-level constant so it's easy to tune during testing week
# without hunting through route handlers.
CONFIDENCE_THRESHOLD = 0.95


# ---------- Shelves ----------

def create_shelf(db: Session, shelf: schemas.ShelfCreate) -> models.Shelf:
    db_shelf = models.Shelf(**shelf.model_dump())
    db.add(db_shelf)
    db.commit()
    db.refresh(db_shelf)
    return db_shelf


def update_shelf_reading(db: Session, shelf_id: str, reading: schemas.ShelfReadingUpdate) -> Optional[models.Shelf]:
    db_shelf = db.get(models.Shelf, shelf_id)
    if not db_shelf:
        return None
    if reading.current_temperature_c is not None:
        db_shelf.current_temperature_c = reading.current_temperature_c
    if reading.current_humidity_pct is not None:
        db_shelf.current_humidity_pct = reading.current_humidity_pct
    db_shelf.last_reading_at = datetime.utcnow()
    db.commit()
    db.refresh(db_shelf)
    return db_shelf


def list_shelves(db: Session):
    return db.query(models.Shelf).all()


# ---------- Items ----------

def create_item(db: Session, item: schemas.ItemCreate) -> models.Item:
    """
    Manual item creation — the path for stock that never crossed the
    intake station (a correction, a backfill, a partner drop-off).

    A UPC, if supplied, is normalized and linked to its catalog product so
    a hand-entered item is indistinguishable downstream from a scanned one.
    The expiration deadlines are computed here for the same reason: an item
    with no deadlines is invisible to the sweep, and an item invisible to
    the sweep sits on a shelf past its date with nothing to say so.
    """
    data = item.model_dump()
    raw_upc = data.pop("upc", None)

    product = None
    if raw_upc:
        # An invalid barcode raises InvalidBarcode, which the router turns
        # into a 422 naming the field.
        normalized = upc_lib.normalize(raw_upc)
        data["upc"] = normalized
        product = upc_lib.find_product(db, normalized)
        if product:
            data["product_id"] = product.id
            # The catalog fills gaps, it does not overwrite: whatever the
            # person typed wins over what the barcode knows.
            data["category"] = data.get("category") or product.category
            data["sku"] = data.get("sku") or product.sku

    db_item = models.Item(**data)
    if db_item.arrival_date is None:
        db_item.arrival_date = datetime.utcnow()
    if db_item.sell_by_date or db_item.use_by_date:
        db_item.date_source = models.DateSource.MANUAL

    expiration.apply_rules_to_item(db, db_item)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


def get_item(db: Session, item_id: str) -> Optional[models.Item]:
    return db.get(models.Item, item_id)


def list_items(db: Session, status: Optional[models.ItemStatus] = None):
    q = db.query(models.Item)
    if status:
        q = q.filter(models.Item.status == status)
    return q.order_by(models.Item.sell_by_date.asc().nullslast()).all()


def list_near_expiry(db: Session, within_hours: int = 48):
    """Items whose sell-by date falls within the given window from now."""
    cutoff = datetime.utcnow() + timedelta(hours=within_hours)
    return (
        db.query(models.Item)
        .filter(models.Item.sell_by_date != None)  # noqa: E711
        .filter(models.Item.sell_by_date <= cutoff)
        .filter(models.Item.status.in_([models.ItemStatus.IN_STOCK, models.ItemStatus.NEAR_EXPIRY]))
        .order_by(models.Item.sell_by_date.asc())
        .all()
    )


def update_item_status(db: Session, item_id: str, status: models.ItemStatus) -> Optional[models.Item]:
    db_item = db.get(models.Item, item_id)
    if not db_item:
        return None
    db_item.status = status
    db.commit()
    db.refresh(db_item)
    return db_item


def apply_ocr_result(db: Session, item_id: str, result: schemas.ItemOCRResult) -> Optional[models.Item]:
    """
    Core confidence-branch logic from Section 9.3 / 13.1 of the proposal:
      - confidence >= threshold AND SKU match confirmed -> auto-log, mark AVAILABLE
      - otherwise -> NEEDS_REVIEW, an employee has to confirm it
    """
    db_item = db.get(models.Item, item_id)
    if not db_item:
        return None

    db_item.ocr_raw_text = result.ocr_raw_text
    db_item.ocr_confidence = result.ocr_confidence
    db_item.sku_match_confirmed = result.sku_match_confirmed
    if result.sell_by_date:
        db_item.sell_by_date = result.sell_by_date
        db_item.date_source = models.DateSource.OCR
        # The date moved, so the deadlines derived from it have to move
        # with it. Skipping this is how an item ends up with a corrected
        # date and a discard deadline computed from the wrong one.
        expiration.apply_rules_to_item(db, db_item)

    if result.ocr_confidence >= CONFIDENCE_THRESHOLD and result.sku_match_confirmed:
        db_item.status = models.ItemStatus.AVAILABLE
    else:
        db_item.status = models.ItemStatus.NEEDS_REVIEW

    db.commit()
    db.refresh(db_item)
    return db_item


# ---------- Product catalog (UPC scanner) ----------

def upsert_product(db: Session, product: schemas.ProductCreate) -> models.Product:
    """
    Create a catalog entry, or update the existing one for that barcode.

    Upsert rather than insert because the realistic way this is called is
    "staff scanned something unknown and typed what it is" — and the second
    time that happens for the same code, it should correct the entry rather
    than fail with a uniqueness error the person can do nothing about.
    """
    data = product.model_dump()
    normalized = upc_lib.normalize(data.pop("upc"))

    db_product = upc_lib.find_product(db, normalized)
    if db_product is None:
        db_product = models.Product(upc=normalized, source="staff")
        db.add(db_product)

    for field, value in data.items():
        setattr(db_product, field, value)

    db.commit()
    db.refresh(db_product)
    return db_product


def update_product(db: Session, product_id: str, patch: schemas.ProductUpdate) -> Optional[models.Product]:
    db_product = db.get(models.Product, product_id)
    if not db_product:
        return None
    for field, value in patch.model_dump(exclude_unset=True).items():
        setattr(db_product, field, value)
    db.commit()
    db.refresh(db_product)
    return db_product


def list_products(db: Session, search: Optional[str] = None, limit: int = 100):
    q = db.query(models.Product)
    if search:
        pattern = f"%{search.strip()}%"
        q = q.filter(
            models.Product.name.ilike(pattern) | models.Product.upc.ilike(pattern)
        )
    return q.order_by(models.Product.name.asc()).limit(limit).all()


# ---------- Expiration rules ----------

def list_expiration_rules(db: Session):
    """Fallback first, then alphabetical — the order a manager reads them in."""
    return (
        db.query(models.ExpirationRule)
        .order_by(
            (models.ExpirationRule.category != expiration.DEFAULT_CATEGORY),
            models.ExpirationRule.category.asc(),
        )
        .all()
    )


def upsert_expiration_rule(db: Session, rule: schemas.ExpirationRuleCreate) -> models.ExpirationRule:
    data = rule.model_dump()
    category = data.pop("category").strip()

    db_rule = (
        db.query(models.ExpirationRule)
        .filter(models.ExpirationRule.category.ilike(category))
        .first()
    )
    if db_rule is None:
        db_rule = models.ExpirationRule(category=category)
        db.add(db_rule)

    for field, value in data.items():
        setattr(db_rule, field, value)

    db.commit()
    db.refresh(db_rule)
    return db_rule


def update_expiration_rule(
    db: Session, rule_id: str, patch: schemas.ExpirationRuleUpdate
) -> Optional[models.ExpirationRule]:
    """
    Edit a rule's numbers.

    Note what this does *not* do: it does not recompute the deadlines on
    items already in stock. Retuning a category changes how the next
    delivery is treated, not how an item already on the shelf was judged.
    Rewriting history here would silently move a discard deadline on food a
    person already looked at and signed off on. Call
    recompute_deadlines_for_category explicitly when that is genuinely what
    is wanted.
    """
    db_rule = db.get(models.ExpirationRule, rule_id)
    if not db_rule:
        return None
    for field, value in patch.model_dump(exclude_unset=True).items():
        setattr(db_rule, field, value)
    db.commit()
    db.refresh(db_rule)
    return db_rule


def recompute_deadlines_for_category(db: Session, category: str) -> int:
    """
    Re-apply the current rule to every item of a category still in play.

    Terminal items are left alone: their deadlines are the record of what
    was decided at the time, and rewriting them would corrupt the donation
    audit trail (FR-11.1).
    """
    items = (
        db.query(models.Item)
        .filter(models.Item.category.ilike(category))
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
    for item in items:
        expiration.apply_rules_to_item(db, item, rules=rules)
    db.commit()
    return len(items)


# ---------- Intake scans (UPC + OCR + manual confirmation) ----------

def create_intake_scan(
    db: Session, scan: schemas.IntakeScanCreate, user_id: Optional[str]
) -> models.IntakeScan:
    """
    Open a scan. The barcode is resolved against the catalog here so the
    confirmation screen has a product name the moment the code is read.
    """
    data = scan.model_dump()
    raw_upc = data.pop("upc", None)

    product = None
    normalized = None
    if raw_upc:
        normalized, product, _ = upc_lib.resolve(db, raw_upc)

    db_scan = models.IntakeScan(
        **data,
        upc=normalized,
        product_id=product.id if product else None,
        scanned_by_id=user_id,
    )
    db.add(db_scan)
    db.commit()
    db.refresh(db_scan)
    return db_scan


def apply_intake_date(
    db: Session, scan_id: str, result: schemas.IntakeDateResult
) -> Optional[models.IntakeScan]:
    """
    Attach the OCR date scanner's reading to an open scan.

    Writes only what was supplied, so a second pass at a label that read
    badly the first time doesn't blank the fields it has nothing to say
    about. Nothing here changes the scan's status: an OCR read is a
    proposal, and the scan stays pending until a person acts on it.
    """
    db_scan = db.get(models.IntakeScan, scan_id)
    if not db_scan or db_scan.status != models.ScanStatus.PENDING:
        return None

    for field, value in result.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(db_scan, field, value)

    db.commit()
    db.refresh(db_scan)
    return db_scan


def list_intake_scans(
    db: Session, status: Optional[models.ScanStatus] = None, limit: int = 100
):
    """Pending queue is oldest-first: the unit waiting longest is the one
    blocking the dock."""
    q = db.query(models.IntakeScan)
    if status:
        q = q.filter(models.IntakeScan.status == status)
    return q.order_by(models.IntakeScan.created_at.asc()).limit(limit).all()


def confirm_intake_scan(
    db: Session, scan_id: str, payload: schemas.IntakeConfirm, user_id: str
) -> Optional[tuple[models.IntakeScan, list[models.Item]]]:
    """
    The manual confirmation step: a pending scan becomes real inventory.

    This is the only place the intake pipeline writes to `items`. Neither
    scanner can do it alone, by design — a barcode cannot know how old a
    unit is, and an OCR read of a date is a guess with a number attached.
    A person holding the package resolves both, and this function records
    what they decided.

    `quantity` creates that many sibling items sharing a batch, because a
    pallet of identical yogurt is scanned once and reserved one unit at a
    time.

    Returns None if the scan is missing or already resolved — which
    includes the double-submit case, so a second press of Confirm cannot
    produce a second set of items.
    """
    db_scan = db.get(models.IntakeScan, scan_id)
    if not db_scan or db_scan.status != models.ScanStatus.PENDING:
        return None

    product = db.get(models.Product, db_scan.product_id) if db_scan.product_id else None

    name = (
        payload.name
        or db_scan.name_override
        or (product.name if product else None)
        # A barcode nothing recognizes and nobody named still has to be
        # findable on the shelf, so it is labelled by its code rather than
        # rejected.
        or (f"Unidentified item · {upc_lib.format_display(db_scan.upc)}" if db_scan.upc else "Unidentified item")
    )
    category = (
        payload.category
        or db_scan.category_override
        or (product.category if product else None)
    )

    # The person said what kind of date they confirmed, and that decides
    # which column it goes in. A use-by date is a safety limit and must not
    # land in sell_by_date, where the category's margin would then be added
    # on top of it (NFR-4.8.2).
    label_type = payload.date_label_type
    use_by = payload.confirmed_date if label_type == models.DateLabelType.USE_BY else None
    sell_by = None if label_type == models.DateLabelType.USE_BY else payload.confirmed_date

    quantity = payload.quantity or db_scan.quantity or 1
    batch_id = payload.batch_id or db_scan.batch_id or f"INTAKE-{db_scan.id[:8]}"
    shelf_id = payload.shelf_id or db_scan.shelf_id
    now = datetime.utcnow()

    rules = expiration.load_rules(db)
    items: list[models.Item] = []
    for _ in range(quantity):
        item = models.Item(
            name=name,
            sku=product.sku if product else None,
            upc=db_scan.upc,
            product_id=db_scan.product_id,
            batch_id=batch_id,
            category=category,
            sell_by_date=sell_by,
            use_by_date=use_by,
            date_label_type=label_type,
            date_source=(
                models.DateSource.OCR if payload.accepted_ocr_date else models.DateSource.MANUAL
            ),
            arrival_date=now,
            shelf_id=shelf_id,
            status=models.ItemStatus.IN_STOCK,
            ocr_raw_text=db_scan.ocr_raw_text,
            ocr_confidence=db_scan.ocr_confidence,
            # A person confirmed the identity by scanning a valid barcode
            # that resolved to a catalog product. That is a stronger match
            # than the substring cross-check this flag was built for.
            sku_match_confirmed=product is not None,
            image_url=db_scan.image_url,
        )
        expiration.apply_rules_to_item(db, item, rules=rules)
        db.add(item)
        items.append(item)

    db.flush()

    db_scan.status = models.ScanStatus.CONFIRMED
    db_scan.confirmed_by_id = user_id
    db_scan.confirmed_at = now
    db_scan.confirmed_date = payload.confirmed_date
    db_scan.detected_label_type = label_type
    db_scan.quantity = quantity
    db_scan.batch_id = batch_id
    db_scan.shelf_id = shelf_id
    # Points at the first of the batch; the rest are reachable by batch_id.
    db_scan.item_id = items[0].id

    db.commit()
    for item in items:
        db.refresh(item)
    db.refresh(db_scan)
    return db_scan, items


def reject_intake_scan(
    db: Session, scan_id: str, notes: Optional[str], user_id: str
) -> Optional[models.IntakeScan]:
    """
    Throw a scan away without creating an item.

    No reason is required (NFR-4.8.4). If someone at the dock has decided
    a unit is not fit to donate, the software's job is to get out of the
    way and record it, not to interview them about it.
    """
    db_scan = db.get(models.IntakeScan, scan_id)
    if not db_scan or db_scan.status != models.ScanStatus.PENDING:
        return None

    db_scan.status = models.ScanStatus.REJECTED
    db_scan.confirmed_by_id = user_id
    db_scan.confirmed_at = datetime.utcnow()
    if notes:
        db_scan.notes = notes

    db.commit()
    db.refresh(db_scan)
    return db_scan


# ---------- Pantries ----------

def create_pantry(db: Session, pantry: schemas.PantryCreate) -> models.Pantry:
    db_pantry = models.Pantry(**pantry.model_dump())
    db.add(db_pantry)
    db.commit()
    db.refresh(db_pantry)
    return db_pantry


def list_pantries(db: Session, verified_only: bool = False):
    q = db.query(models.Pantry)
    if verified_only:
        q = q.filter(models.Pantry.verified == True)  # noqa: E712
    return q.all()


# ---------- Users ----------

def get_user_by_email(db: Session, email: str) -> Optional[models.User]:
    """Case-insensitive: people type their email with whatever capitalization
    their phone keyboard decided on."""
    return (
        db.query(models.User)
        .filter(func.lower(models.User.email) == email.strip().lower())
        .first()
    )


# ---------- Reservations ----------

def create_reservation(
    db: Session,
    res: schemas.ReservationCreate,
    pantry_id: str,
) -> tuple[Optional[models.Reservation], str]:
    """
    Claims an AVAILABLE item for `pantry_id` and starts the holding-window
    clock. `pantry_id` comes from the authenticated user, never from the
    request body (FR-2.3).

    The hold is derived from the organization's scheduled pickup time
    (FR-8.6) rather than running for a flat three hours from the click. The
    old shape held the food for a window that had nothing to do with when
    anyone intended to arrive, so staff could not plan and a no-show tied
    the item up for the full three hours. Now the organization states a
    slot, the hold is that slot plus PICKUP_GRACE, and the food returns to
    the pool half an hour after a missed pickup.

    The claim is a conditional UPDATE rather than a read-then-write. The
    previous version read item.status, then wrote, with no lock in
    between — two concurrent requests could both observe "available" and
    both succeed, producing two valid QR codes for one physical item
    (NFR-4.7.1). Here the database decides the winner: whoever's UPDATE
    matches zero rows lost the race and gets the same 409 as someone
    reserving an already-taken item.

    Returns (reservation, outcome). Outcomes: "ok", "unavailable" (lost the
    race or never eligible), "past_discard" (see below).
    """
    claimed = (
        db.query(models.Item)
        .filter(models.Item.id == res.item_id)
        .filter(models.Item.status == models.ItemStatus.AVAILABLE)
        .update(
            {models.Item.status: models.ItemStatus.RESERVED},
            synchronize_session=False,
        )
    )
    if claimed == 0:
        db.rollback()
        return None, "unavailable"

    # A slot may not be booked past the moment the food has to leave the
    # shelf. expiration.sweep() moves any RESERVED item past its
    # discard_after to EXPIRED_HOLD, so without this check a 24-hour
    # booking on short-dated produce would be silently killed overnight and
    # the organization would discover it at the shelf. Refusing here costs
    # them one retry; the alternative costs them the trip. This lives in
    # crud rather than the schema validator because it needs the Item row.
    item = db.get(models.Item, res.item_id)
    if item and item.discard_after and res.scheduled_pickup_at > item.discard_after:
        db.rollback()
        return None, "past_discard"

    db_res = models.Reservation(
        item_id=res.item_id,
        pantry_id=pantry_id,
        scheduled_pickup_at=res.scheduled_pickup_at,
        hold_expires_at=res.scheduled_pickup_at + schemas.PICKUP_GRACE,
        qr_code=secrets.token_urlsafe(16),
    )
    db.add(db_res)
    db.commit()
    db.refresh(db_res)
    return db_res, "ok"


def list_reservations(
    db: Session,
    pantry_id: Optional[str] = None,
    status: Optional[models.ReservationStatus] = None,
):
    """Newest first. `pantry_id` scopes the result to one organization —
    the organizer dashboard always passes it, staff never do (FR-2.4)."""
    q = db.query(models.Reservation)
    if pantry_id:
        q = q.filter(models.Reservation.pantry_id == pantry_id)
    if status:
        q = q.filter(models.Reservation.status == status)
    return q.order_by(models.Reservation.reserved_at.desc()).all()


def cancel_reservation(db: Session, reservation_id: str, pantry_id: str) -> Optional[models.Reservation]:
    """
    FR-8.11: an organization cancels its own pending reservation and the
    item returns to the pool immediately.

    A reservation belonging to another organization returns None, which the
    router renders as 404 rather than 403 — a 403 would confirm that the
    reservation exists (FR-2.4).
    """
    db_res = db.get(models.Reservation, reservation_id)
    if not db_res or db_res.pantry_id != pantry_id:
        return None
    if db_res.status != models.ReservationStatus.PENDING:
        return None

    db_res.status = models.ReservationStatus.CANCELLED
    item = db.get(models.Item, db_res.item_id)
    if item:
        item.status = models.ItemStatus.AVAILABLE
    db.commit()
    db.refresh(db_res)
    return db_res


def expire_stale_reservations(db: Session):
    """
    Run periodically (cron / background task). Any PENDING reservation past
    its hold window gets marked EXPIRED and the item goes back to AVAILABLE
    so another pantry can grab it — mirrors Section 13.3's mitigation.

    Unchanged by scheduled pickups: hold_expires_at is still a real stored
    column, only its derivation moved (it is now the scheduled slot plus
    PICKUP_GRACE, not a flat window from the click). In practice that means
    this fires thirty minutes after a missed pickup rather than three hours
    after a reservation, with no new logic here.
    """
    now = datetime.utcnow()
    stale = (
        db.query(models.Reservation)
        .filter(models.Reservation.status == models.ReservationStatus.PENDING)
        .filter(models.Reservation.hold_expires_at < now)
        .all()
    )
    for res in stale:
        res.status = models.ReservationStatus.EXPIRED
        item = db.get(models.Item, res.item_id)
        if item:
            item.status = models.ItemStatus.AVAILABLE
    db.commit()
    return stale


def confirm_pickup(db: Session, qr_code: str) -> tuple[Optional[models.Reservation], str]:
    """
    Redeems a pickup token at the shelf. Returns (reservation, outcome);
    outcomes are "ok", "not_found", "expired", "already_picked_up" and
    "cancelled".

    The expiry time governs, not the sweep (FR-9.7). This function used to
    check the status only, which left an up-to-sixty-second window — the
    scheduler's interval — in which a lapsed code still redeemed. With the
    hold now pinned thirty minutes to a promised slot, that window sits
    exactly where a late arrival turns up, so the check has to happen here.
    A lapsed scan expires the reservation and releases the item on the spot
    rather than waiting for the next tick, so the shelf and the screen
    agree by the time the staff member looks up.

    The outcomes are distinguished rather than collapsed into one failure
    because the scan result is the only feedback a staff member holding a
    phone at the shelf gets (FR-9.6, FR-9.8). "Invalid or already-used
    code" is useless when the real answer is that the organization is forty
    minutes late and the food went back in the pool at 2:30.
    """
    db_res = db.query(models.Reservation).filter(models.Reservation.qr_code == qr_code).first()
    if not db_res:
        return None, "not_found"

    if db_res.status == models.ReservationStatus.PICKED_UP:
        return db_res, "already_picked_up"
    if db_res.status == models.ReservationStatus.CANCELLED:
        return db_res, "cancelled"
    if db_res.status == models.ReservationStatus.EXPIRED:
        return db_res, "expired"

    if db_res.hold_expires_at and db_res.hold_expires_at < datetime.utcnow():
        db_res.status = models.ReservationStatus.EXPIRED
        item = db.get(models.Item, db_res.item_id)
        if item:
            item.status = models.ItemStatus.AVAILABLE
        db.commit()
        db.refresh(db_res)
        return db_res, "expired"

    db_res.status = models.ReservationStatus.PICKED_UP
    db_res.picked_up_at = datetime.utcnow()
    item = db.get(models.Item, db_res.item_id)
    if item:
        item.status = models.ItemStatus.PICKED_UP
    db.commit()
    db.refresh(db_res)
    return db_res, "ok"
