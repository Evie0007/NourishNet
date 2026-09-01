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

from . import models, schemas

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
    db_item = models.Item(**item.model_dump())
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

    if result.ocr_confidence >= CONFIDENCE_THRESHOLD and result.sku_match_confirmed:
        db_item.status = models.ItemStatus.AVAILABLE
    else:
        db_item.status = models.ItemStatus.NEEDS_REVIEW

    db.commit()
    db.refresh(db_item)
    return db_item


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
) -> Optional[models.Reservation]:
    """
    Claims an AVAILABLE item for `pantry_id` and starts the holding-window
    clock. `pantry_id` comes from the authenticated user, never from the
    request body (FR-2.3).

    The claim is a conditional UPDATE rather than a read-then-write. The
    previous version read item.status, then wrote, with no lock in
    between — two concurrent requests could both observe "available" and
    both succeed, producing two valid QR codes for one physical item
    (NFR-4.7.1). Here the database decides the winner: whoever's UPDATE
    matches zero rows lost the race and gets the same 409 as someone
    reserving an already-taken item.
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
        return None

    db_res = models.Reservation(
        item_id=res.item_id,
        pantry_id=pantry_id,
        hold_expires_at=datetime.utcnow() + timedelta(minutes=res.hold_minutes),
        qr_code=secrets.token_urlsafe(16),
    )
    db.add(db_res)
    db.commit()
    db.refresh(db_res)
    return db_res


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


def confirm_pickup(db: Session, qr_code: str) -> Optional[models.Reservation]:
    db_res = db.query(models.Reservation).filter(models.Reservation.qr_code == qr_code).first()
    if not db_res or db_res.status != models.ReservationStatus.PENDING:
        return None
    db_res.status = models.ReservationStatus.PICKED_UP
    db_res.picked_up_at = datetime.utcnow()
    item = db.get(models.Item, db_res.item_id)
    if item:
        item.status = models.ItemStatus.PICKED_UP
    db.commit()
    db.refresh(db_res)
    return db_res
