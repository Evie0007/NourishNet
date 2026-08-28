from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..database import get_db

router = APIRouter(prefix="/reservations", tags=["reservations"])


@router.post("", response_model=schemas.ReservationOut)
def reserve_item(res: schemas.ReservationCreate, db: Session = Depends(get_db)):
    """
    Reserves an AVAILABLE item for a pantry and starts the holding-window
    clock (default 3 hours, Section 13.3). Returns a QR code string the
    app renders for pickup.
    """
    reservation = crud.create_reservation(db, res)
    if not reservation:
        raise HTTPException(
            status_code=409,
            detail="Item is not available to reserve (already reserved, picked up, or not yet donation-eligible).",
        )
    return reservation


@router.post("/expire-stale", response_model=list[schemas.ReservationOut])
def expire_stale(db: Session = Depends(get_db)):
    """
    Trigger manually for now (call from a cron job / scheduled task in
    production). Expires PENDING reservations past their hold window and
    frees the item back to AVAILABLE.
    """
    return crud.expire_stale_reservations(db)


@router.post("/pickup/{qr_code}", response_model=schemas.ReservationOut)
def confirm_pickup(qr_code: str, db: Session = Depends(get_db)):
    """Scanning the QR code at the shelf hits this endpoint to unlock/confirm pickup."""
    reservation = crud.confirm_pickup(db, qr_code)
    if not reservation:
        raise HTTPException(status_code=404, detail="Invalid or already-used QR code")
    return reservation
