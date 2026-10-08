"""
CRUD + core business logic.

The confidence-branch and holding-window logic live here (not in the
routers) so they can be unit-tested directly without spinning up the API.
"""
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth, expiration, geocode, models, schemas, upc as upc_lib

# Section 13.1 of the proposal: 95% is the starting confidence target.
# Kept as a module-level constant so it's easy to tune during testing week
# without hunting through route handlers.
CONFIDENCE_THRESHOLD = 0.95


# ---------- Shelves ----------

def _in_scope(query, model, store_ids: Optional[list[str]]):
    """
    Restrict a query to the stores a caller may see. `None` is unrestricted
    (platform admin only). An empty list matches nothing, which is what an
    unlinked account should get.
    """
    if store_ids is None:
        return query
    return query.filter(model.store_id.in_(store_ids))


def shelf_in_store(db: Session, shelf_id: str, store_id: str) -> bool:
    shelf = db.get(models.Shelf, shelf_id)
    return shelf is not None and shelf.store_id == store_id


def create_shelf(db: Session, shelf: schemas.ShelfCreate, store_id: str) -> models.Shelf:
    """The store is the one the caller resolved (auth.resolve_write_store),
    never whatever the body said (NFR-4.6.1)."""
    db_shelf = models.Shelf(**shelf.model_dump(exclude={"store_id"}), store_id=store_id)
    db.add(db_shelf)
    db.commit()
    db.refresh(db_shelf)
    return db_shelf


def update_shelf_reading(
    db: Session, shelf_id: str, reading: schemas.ShelfReadingUpdate, store_ids: Optional[list[str]]
) -> Optional[models.Shelf]:
    db_shelf = _in_scope(db.query(models.Shelf).filter(models.Shelf.id == shelf_id), models.Shelf, store_ids).first()
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


def list_shelves(db: Session, store_ids: Optional[list[str]] = None):
    return _in_scope(db.query(models.Shelf), models.Shelf, store_ids).all()


# ---------- Items ----------

def create_item(db: Session, item: schemas.ItemCreate, store_id: str) -> models.Item:
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
    data["store_id"] = store_id

    if data.get("shelf_id") and not shelf_in_store(db, data["shelf_id"], store_id):
        # A shelf from another store is the same as no shelf: the item is
        # not placed anywhere this store could not see.
        raise ValueError("That shelf is not one of your store's shelves.")

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
            data["unit_value"] = data.get("unit_value") or product.unit_value

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


def get_item(db: Session, item_id: str, store_ids: Optional[list[str]] = None) -> Optional[models.Item]:
    return _in_scope(db.query(models.Item).filter(models.Item.id == item_id), models.Item, store_ids).first()


def _pickup_store_ids():
    """
    Stores a pantry can collect from right now: active, and not marked closed.
    One definition, used both to list what a pantry can see and to decide what
    it can claim, so the list never offers food the claim would then refuse.
    """
    return select(models.Store.id).where(
        models.Store.active == True,  # noqa: E712
        models.Store.open_for_pickup == True,  # noqa: E712
    )


def list_items(
    db: Session,
    status: Optional[models.ItemStatus] = None,
    store_ids: Optional[list[str]] = None,
    active_stores_only: bool = False,
):
    """
    `store_ids` scopes staff views. `active_stores_only` is for the pantry
    side: food from every store open for pickups is offered, and a
    deactivated or closed store's food is withdrawn from the pool without
    touching its own records.
    """
    q = db.query(models.Item)
    q = _in_scope(q, models.Item, store_ids)
    if active_stores_only:
        q = q.filter(models.Item.store_id.in_(_pickup_store_ids()))
    if status:
        q = q.filter(models.Item.status == status)
    return q.order_by(models.Item.sell_by_date.asc().nullslast()).all()


def list_near_expiry(db: Session, within_hours: int = 48, store_ids: Optional[list[str]] = None):
    """Items whose sell-by date falls within the given window from now."""
    cutoff = datetime.utcnow() + timedelta(hours=within_hours)
    q = (
        db.query(models.Item)
        .filter(models.Item.sell_by_date != None)  # noqa: E711
        .filter(models.Item.sell_by_date <= cutoff)
        .filter(models.Item.status.in_([models.ItemStatus.IN_STOCK, models.ItemStatus.NEAR_EXPIRY]))
    )
    return _in_scope(q, models.Item, store_ids).order_by(models.Item.sell_by_date.asc()).all()


def update_item_status(
    db: Session, item_id: str, status: models.ItemStatus, store_ids: Optional[list[str]] = None
) -> Optional[models.Item]:
    db_item = get_item(db, item_id, store_ids)
    if not db_item:
        return None
    db_item.status = status
    db.commit()
    db.refresh(db_item)
    return db_item


def apply_ocr_result(
    db: Session, item_id: str, result: schemas.ItemOCRResult, store_ids: Optional[list[str]] = None
) -> Optional[models.Item]:
    """
    Core confidence-branch logic from Section 9.3 / 13.1 of the proposal:
      - confidence >= threshold AND SKU match confirmed -> auto-log, mark AVAILABLE
      - otherwise -> NEEDS_REVIEW, an employee has to confirm it
    """
    db_item = get_item(db, item_id, store_ids)
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


# ---------- Brands and stores ----------

def create_brand(db: Session, brand: schemas.BrandCreate) -> models.Brand:
    db_brand = models.Brand(name=brand.name.strip())
    db.add(db_brand)
    db.commit()
    db.refresh(db_brand)
    return db_brand


def list_brands(db: Session):
    return db.query(models.Brand).order_by(models.Brand.name.asc()).all()


def _locate(store: models.Store) -> None:
    """
    Fill in the store's coordinates from its address. Left empty when the
    lookup fails, which takes the store off the map and changes nothing else.
    Only called when the address is set or changes, never on a page load.
    """
    coords = geocode.geocode_address(store.address)
    store.latitude, store.longitude = coords if coords else (None, None)


def create_store(db: Session, store: schemas.StoreCreate) -> models.Store:
    db_store = models.Store(
        name=store.name.strip(),
        address=store.address,
        brand_id=store.brand_id,
        active=True,
    )
    _locate(db_store)
    db.add(db_store)
    db.commit()
    db.refresh(db_store)
    return db_store


def update_store(db: Session, store_id: str, patch: schemas.StoreUpdate) -> Optional[models.Store]:
    """
    Change a store's details. Re-geocodes only when the address actually
    changed, so a rename does not spend a lookup.
    """
    db_store = db.get(models.Store, store_id)
    if not db_store:
        return None
    data = patch.model_dump(exclude_unset=True)
    address_changed = "address" in data and data["address"] != db_store.address
    for field, value in data.items():
        setattr(db_store, field, value)
    if address_changed:
        _locate(db_store)
    db.commit()
    db.refresh(db_store)
    return db_store


def list_mappable_stores(db: Session) -> list[models.Store]:
    """Stores a pantry can be shown on the map: active, and located."""
    return (
        db.query(models.Store)
        .filter(models.Store.active == True)  # noqa: E712
        .filter(models.Store.latitude.isnot(None))
        .filter(models.Store.longitude.isnot(None))
        .order_by(models.Store.name.asc())
        .all()
    )


def set_store_open(db: Session, store_id: str, open_for_pickup: bool) -> Optional[models.Store]:
    """Open or close a location for pickups. Reservations already made are
    left alone: closing stops new ones, it does not cancel a pantry's trip."""
    store = db.get(models.Store, store_id)
    if not store:
        return None
    store.open_for_pickup = open_for_pickup
    db.commit()
    db.refresh(store)
    return store


def list_pickup_locations(
    db: Session, center: Optional[tuple[float, float]] = None
) -> list[dict]:
    """
    Every located, active store with what a pantry needs to choose between
    them: how many items it can collect there, and whether the location is
    available at all (FR-8.14).

    A location is available when it is open for pickups and has something to
    collect. Unavailable ones are kept in the list rather than dropped, marked
    with the reason — a pantry looking for the nearest shelf should see that
    the closest one is closed, not wonder why it is missing.

    Ordering: available first, then nearest to `center` when there is one,
    otherwise by name. Closed or empty locations never outrank one that has
    food, however close they are.
    """
    counts = dict(
        db.query(models.Item.store_id, func.count(models.Item.id))
        .filter(models.Item.status == models.ItemStatus.AVAILABLE)
        .group_by(models.Item.store_id)
        .all()
    )
    rows = []
    for store in list_mappable_stores(db):
        count = counts.get(store.id, 0)
        if not store.open_for_pickup:
            reason = "closed"
        elif count == 0:
            reason = "no_items"
        else:
            reason = None
        distance = (
            round(geocode.distance_miles(center, (store.latitude, store.longitude)), 1)
            if center
            else None
        )
        rows.append(
            {
                "id": store.id,
                "name": store.name,
                "address": store.address,
                "latitude": store.latitude,
                "longitude": store.longitude,
                "open_for_pickup": store.open_for_pickup,
                "available_count": count,
                "available": reason is None,
                "unavailable_reason": reason,
                "distance_miles": distance,
            }
        )
    rows.sort(
        key=lambda r: (
            not r["available"],
            r["distance_miles"] if r["distance_miles"] is not None else 0,
            r["name"].lower(),
        )
    )
    return rows


def list_stores(db: Session, store_ids: Optional[list[str]] = None):
    """`store_ids` is the caller's visible set. None is every store, which
    is the platform admin's view."""
    q = db.query(models.Store)
    if store_ids is not None:
        q = q.filter(models.Store.id.in_(store_ids))
    return q.order_by(models.Store.name.asc()).all()


def create_store_user(db: Session, payload: schemas.StoreStaffCreate) -> models.User:
    """
    Creates a staff or manager account linked to exactly one store, or to a
    brand. The caller has already been checked against the link it asks for
    (see the stores router); this function only writes it.
    """
    user = models.User(
        email=payload.email.strip(),
        password_hash=auth.hash_password(payload.password),
        full_name=payload.full_name,
        role=models.UserRole(payload.role),
        store_id=payload.store_id,
        brand_id=payload.brand_id,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


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
    db: Session, scan: schemas.IntakeScanCreate, user_id: Optional[str], store_id: str
) -> models.IntakeScan:
    """
    Open a scan. The barcode is resolved against the catalog here so the
    confirmation screen has a product name the moment the code is read.

    The scan belongs to the location it was made at (store_id), and so does
    any shelf it is placed on.
    """
    data = scan.model_dump(exclude={"store_id"})
    raw_upc = data.pop("upc", None)

    if data.get("shelf_id") and not shelf_in_store(db, data["shelf_id"], store_id):
        raise ValueError("That shelf is not one of your store's shelves.")

    product = None
    normalized = None
    if raw_upc:
        normalized, product, _ = upc_lib.resolve(db, raw_upc)

    db_scan = models.IntakeScan(
        **data,
        upc=normalized,
        product_id=product.id if product else None,
        scanned_by_id=user_id,
        store_id=store_id,
    )
    db.add(db_scan)
    db.commit()
    db.refresh(db_scan)
    return db_scan


def get_scan(db: Session, scan_id: str, store_ids: Optional[list[str]] = None) -> Optional[models.IntakeScan]:
    return _in_scope(db.query(models.IntakeScan).filter(models.IntakeScan.id == scan_id), models.IntakeScan, store_ids).first()


def apply_intake_date(
    db: Session, scan_id: str, result: schemas.IntakeDateResult, store_ids: Optional[list[str]] = None
) -> Optional[models.IntakeScan]:
    """
    Attach the OCR date scanner's reading to an open scan.

    Writes only what was supplied, so a second pass at a label that read
    badly the first time doesn't blank the fields it has nothing to say
    about. Nothing here changes the scan's status: an OCR read is a
    proposal, and the scan stays pending until a person acts on it.
    """
    db_scan = get_scan(db, scan_id, store_ids)
    if not db_scan or db_scan.status != models.ScanStatus.PENDING:
        return None

    for field, value in result.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(db_scan, field, value)

    db.commit()
    db.refresh(db_scan)
    return db_scan


def list_intake_scans(
    db: Session,
    status: Optional[models.ScanStatus] = None,
    limit: int = 100,
    store_ids: Optional[list[str]] = None,
):
    """Pending queue is oldest-first: the unit waiting longest is the one
    blocking the dock."""
    q = _in_scope(db.query(models.IntakeScan), models.IntakeScan, store_ids)
    if status:
        q = q.filter(models.IntakeScan.status == status)
    return q.order_by(models.IntakeScan.created_at.asc()).limit(limit).all()


def confirm_intake_scan(
    db: Session,
    scan_id: str,
    payload: schemas.IntakeConfirm,
    user_id: str,
    store_ids: Optional[list[str]] = None,
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
    db_scan = get_scan(db, scan_id, store_ids)
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
    if shelf_id and not shelf_in_store(db, shelf_id, db_scan.store_id):
        raise ValueError("That shelf is not one of your store's shelves.")
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
            unit_value=product.unit_value if product else None,
            sell_by_date=sell_by,
            use_by_date=use_by,
            date_label_type=label_type,
            date_source=(
                models.DateSource.OCR if payload.accepted_ocr_date else models.DateSource.MANUAL
            ),
            arrival_date=now,
            shelf_id=shelf_id,
            store_id=db_scan.store_id,
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
    db: Session, scan_id: str, notes: Optional[str], user_id: str, store_ids: Optional[list[str]] = None
) -> Optional[models.IntakeScan]:
    """
    Throw a scan away without creating an item.

    No reason is required (NFR-4.8.4). If someone at the dock has decided
    a unit is not fit to donate, the software's job is to get out of the
    way and record it, not to interview them about it.
    """
    db_scan = get_scan(db, scan_id, store_ids)
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

def get_pantry_by_ein(db: Session, ein: str) -> Optional[models.Pantry]:
    return db.query(models.Pantry).filter(models.Pantry.ein == ein.strip()).first()


def register_organization(db: Session, payload: "schemas.PantryCreate") -> models.Pantry:
    """UC-03 step 4: creates the Pantry and its bound coordinator User as one
    transaction. The pantry is flushed (not committed) first only to obtain
    its generated id for the User's pantry_id FK; one commit at the end means
    a failure partway through (e.g. a same-email race past the router's
    pre-checks) rolls the pantry back too, instead of leaving an orphaned
    unverified organization with no coordinator ever able to log into it.
    """
    db_pantry = models.Pantry(
        org_name=payload.org_name,
        ein=payload.ein,
        address=payload.address,
        phone=payload.phone,
        contact_email=payload.contact_email,
    )
    db.add(db_pantry)
    db.flush()

    coordinator = models.User(
        email=payload.contact_email.strip(),
        password_hash=auth.hash_password(payload.password),
        full_name=None,
        role=models.UserRole.ORG_COORDINATOR,
        pantry_id=db_pantry.id,
        is_active=True,
    )
    db.add(coordinator)

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

def _claim_item(db: Session, item_id: str) -> bool:
    """
    Flip one AVAILABLE item to RESERVED, and report whether this call won it.
    A conditional UPDATE, so the database decides the winner when two
    organizations race for the same item (NFR-4.7.1). Only food from a store
    open for pickups can be claimed: a deactivated or closed store's stock
    stays on its own records but leaves the pantry pool at once.
    """
    claimed = (
        db.query(models.Item)
        .filter(models.Item.id == item_id)
        .filter(models.Item.status == models.ItemStatus.AVAILABLE)
        .filter(models.Item.store_id.in_(_pickup_store_ids()))
        .update(
            {models.Item.status: models.ItemStatus.RESERVED},
            synchronize_session=False,
        )
    )
    return claimed == 1


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
    if not _claim_item(db, res.item_id):
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
    store_ids: Optional[list[str]] = None,
):
    """Newest first. `pantry_id` scopes the result to one organization —
    the organizer dashboard always passes it, staff never do (FR-2.4).
    `store_ids` scopes the store-side view to reservations of that store's
    food (the item's store, since a reservation has none of its own)."""
    q = db.query(models.Reservation)
    if pantry_id:
        q = q.filter(models.Reservation.pantry_id == pantry_id)
    if status:
        q = q.filter(models.Reservation.status == status)
    if store_ids is not None:
        q = q.join(models.Item, models.Reservation.item_id == models.Item.id).filter(
            models.Item.store_id.in_(store_ids)
        )
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


def create_order(
    db: Session,
    order: schemas.PickupOrderCreate,
    pantry_id: str,
) -> tuple[Optional[models.PickupOrder], str, Optional[str]]:
    """
    Reserves several AVAILABLE items from one store for one trip, under one QR
    code (FR-8.13). Returns (order, outcome, item_name).

    All or nothing. If any one item has been taken, closed off, or cannot
    last until the slot, nothing is reserved and the name of the item that
    stopped it comes back, so the organization can drop it and try again. A
    half-made order would leave a pantry holding some of a trip's food and
    not knowing which.

    Outcomes: "ok", "unavailable" (an item was taken or never eligible),
    "mixed_stores" (items from more than one location — a trip goes to one
    place), "past_discard" (an item has to leave the shelf before the slot).
    Each item is claimed with the same conditional UPDATE a single
    reservation uses, so two organizations racing for one item still produce
    exactly one winner.
    """
    items = db.query(models.Item).filter(models.Item.id.in_(order.item_ids)).all()
    if len(items) != len(order.item_ids):
        return None, "unavailable", None
    if len({item.store_id for item in items}) > 1:
        return None, "mixed_stores", None
    for item in items:
        # The same guard a single reservation applies, per item: a slot past
        # an item's discard time would be killed by the sweep before anyone
        # arrived.
        if item.discard_after and order.scheduled_pickup_at > item.discard_after:
            return None, "past_discard", item.name

    claimed = []
    for item in items:
        if not _claim_item(db, item.id):
            db.rollback()
            return None, "unavailable", item.name
        claimed.append(item)

    db_order = models.PickupOrder(
        pantry_id=pantry_id,
        store_id=items[0].store_id,
        qr_code=secrets.token_urlsafe(16),
        scheduled_pickup_at=order.scheduled_pickup_at,
        hold_expires_at=order.scheduled_pickup_at + schemas.PICKUP_GRACE,
    )
    db.add(db_order)
    db.flush()
    for item in claimed:
        db.add(
            models.Reservation(
                item_id=item.id,
                pantry_id=pantry_id,
                order_id=db_order.id,
                scheduled_pickup_at=db_order.scheduled_pickup_at,
                hold_expires_at=db_order.hold_expires_at,
                # The order carries the one code; see Reservation.qr_code.
                qr_code=None,
            )
        )
    db.commit()
    db.refresh(db_order)
    return db_order, "ok", None


def cancel_order(db: Session, order_id: str, pantry_id: str) -> Optional[models.PickupOrder]:
    """
    Cancels every still-pending item in an order and returns them to the pool.
    None when the order is not this organization's or has nothing pending —
    the router renders both as 404, for the same reason cancel_reservation
    does (FR-2.4).
    """
    db_order = db.get(models.PickupOrder, order_id)
    if not db_order or db_order.pantry_id != pantry_id:
        return None
    pending = [r for r in db_order.reservations if r.status == models.ReservationStatus.PENDING]
    if not pending:
        return None
    for res in pending:
        res.status = models.ReservationStatus.CANCELLED
        item = db.get(models.Item, res.item_id)
        if item:
            item.status = models.ItemStatus.AVAILABLE
    db.commit()
    db.refresh(db_order)
    return db_order


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


def confirm_pickup(
    db: Session, qr_code: str, confirmed_by_user_id: str, store_ids: Optional[list[str]] = None
) -> tuple[Optional[models.Reservation], str]:
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
        # Not a single reservation's code; it may be a whole order's.
        return _confirm_order_pickup(db, qr_code, confirmed_by_user_id, store_ids)

    # A code for another store's food reads exactly like an unknown code.
    # Saying "that belongs to another store" would confirm it exists (FR-2.4).
    res_item = db.get(models.Item, db_res.item_id)
    if store_ids is not None and (res_item is None or res_item.store_id not in store_ids):
        return None, "not_found"

    if db_res.status == models.ReservationStatus.PICKED_UP:
        return db_res, "already_picked_up"
    if db_res.status == models.ReservationStatus.CANCELLED:
        return db_res, "cancelled"
    if db_res.status == models.ReservationStatus.EXPIRED:
        return db_res, "expired"

    if db_res.hold_expires_at and db_res.hold_expires_at < datetime.utcnow():
        _expire_reservation(db, db_res)
        db.commit()
        db.refresh(db_res)
        return db_res, "expired"

    _hand_over(db, db_res, confirmed_by_user_id)
    db.commit()
    db.refresh(db_res)
    return db_res, "ok"


def _expire_reservation(db: Session, db_res: models.Reservation) -> None:
    """A lapsed hold: mark it expired and put the item back in the pool."""
    db_res.status = models.ReservationStatus.EXPIRED
    item = db.get(models.Item, db_res.item_id)
    if item:
        item.status = models.ItemStatus.AVAILABLE


def _hand_over(db: Session, db_res: models.Reservation, confirmed_by_user_id: str) -> None:
    """
    Completes one reservation: picked up, item picked up, and the donation
    record written. Shared by a single-reservation scan and an order scan so
    the audit trail is identical either way — one record per item.
    """
    db_res.status = models.ReservationStatus.PICKED_UP
    db_res.picked_up_at = datetime.utcnow()
    item = db.get(models.Item, db_res.item_id)
    if item:
        item.status = models.ItemStatus.PICKED_UP
    pantry = db.get(models.Pantry, db_res.pantry_id)

    # The immutable donation record (FR-11.1) — written once, here, and
    # never touched again. Fields are copied rather than joined live so
    # this row still reads correctly after the Item or Pantry it points to
    # is later edited.
    db.add(models.DonationRecord(
        reservation_id=db_res.id,
        item_name=item.name if item else "",
        item_sku=item.sku if item else None,
        item_category=item.category if item else None,
        sell_by_date_at_handoff=item.sell_by_date if item else None,
        unit_value_at_handoff=item.unit_value if item else None,
        pantry_id=db_res.pantry_id,
        pantry_name_at_handoff=pantry.org_name if pantry else "",
        store_id=item.store_id if item else None,
        confirmed_by_user_id=confirmed_by_user_id,
        confirmed_at=db_res.picked_up_at,
    ))


def _confirm_order_pickup(
    db: Session, qr_code: str, confirmed_by_user_id: str, store_ids: Optional[list[str]]
) -> tuple[Optional[models.Reservation], str]:
    """
    Redeems an order's QR code: every still-pending item in it is handed over
    at once. Returns one of the order's reservations to stand for it — the
    router reports the whole trip from `reservation.order`.

    Same rules as a single scan. The order's hold governs, not the sweep
    (FR-9.7); another store's order code reads as unknown (FR-2.4); and a
    second scan reports what already happened (FR-9.8). An item the
    organization cancelled before arriving is skipped rather than blocking
    the rest of the trip.
    """
    order = db.query(models.PickupOrder).filter(models.PickupOrder.qr_code == qr_code).first()
    if not order or (store_ids is not None and order.store_id not in store_ids):
        return None, "not_found"

    members = list(order.reservations)
    pending = [r for r in members if r.status == models.ReservationStatus.PENDING]
    if not pending:
        for status, outcome in (
            (models.ReservationStatus.PICKED_UP, "already_picked_up"),
            (models.ReservationStatus.EXPIRED, "expired"),
        ):
            match = next((r for r in members if r.status == status), None)
            if match:
                return match, outcome
        return (members[0] if members else None), "cancelled"

    if order.hold_expires_at < datetime.utcnow():
        for res in pending:
            _expire_reservation(db, res)
        db.commit()
        db.refresh(pending[0])
        return pending[0], "expired"

    for res in pending:
        _hand_over(db, res, confirmed_by_user_id)
    db.commit()
    db.refresh(pending[0])
    return pending[0], "ok"


# ---------- Donation records (FR-11.1) ----------

def list_donation_records(
    db: Session,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pantry_id: Optional[str] = None,
    store_ids: Optional[list[str]] = None,
):
    """The donation history a business pulls at tax time. Newest first, so
    the most recent filing period is what shows up without scrolling."""
    q = _in_scope(db.query(models.DonationRecord), models.DonationRecord, store_ids)
    if date_from:
        q = q.filter(models.DonationRecord.confirmed_at >= date_from)
    if date_to:
        q = q.filter(models.DonationRecord.confirmed_at <= date_to)
    if pantry_id:
        q = q.filter(models.DonationRecord.pantry_id == pantry_id)
    return q.order_by(models.DonationRecord.confirmed_at.desc()).all()


def summarize_donations_by_year(
    db: Session,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pantry_id: Optional[str] = None,
    store_ids: Optional[list[str]] = None,
) -> list[dict]:
    """Tax-year totals (FR-11.8): one row per calendar year of confirmed_at,
    the number an accountant asks for. Takes the same filters as
    list_donation_records so a filtered report's totals always match its
    detail rows. Grouped in Python, not SQL GROUP BY/EXTRACT, because
    donation volume per store is small and the records are immutable, so
    recomputing per request is cheap and this stays identical on SQLite
    (tests) and Postgres (prod) without a dialect-specific date function.
    """
    records = list_donation_records(
        db, date_from=date_from, date_to=date_to, pantry_id=pantry_id, store_ids=store_ids
    )
    by_year: dict[int, dict] = {}
    for r in records:
        bucket = by_year.setdefault(
            r.confirmed_at.year,
            {"tax_year": r.confirmed_at.year, "total_value": Decimal("0"), "item_count": 0, "unvalued_item_count": 0},
        )
        bucket["item_count"] += 1
        if r.unit_value_at_handoff is not None:
            bucket["total_value"] += r.unit_value_at_handoff
        else:
            bucket["unvalued_item_count"] += 1
    return sorted(by_year.values(), key=lambda b: b["tax_year"], reverse=True)
