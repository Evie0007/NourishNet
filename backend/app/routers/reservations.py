from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auth, crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/reservations", tags=["reservations"])


def _detail(res: models.Reservation) -> schemas.ReservationDetailOut:
    """Flattens the joined item/shelf/pantry names the dashboards display."""
    item = res.item
    return schemas.ReservationDetailOut(
        **schemas.ReservationOut.model_validate(res).model_dump(),
        item_name=item.name if item else None,
        item_category=item.category if item else None,
        item_sell_by_date=item.sell_by_date if item else None,
        shelf_name=item.shelf.name if item and item.shelf else None,
        pantry_name=res.pantry.org_name if res.pantry else None,
    )


@router.post("", response_model=schemas.ReservationDetailOut)
def reserve_item(
    res: schemas.ReservationCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_organizer),
):
    """
    Reserves an AVAILABLE item for the caller's organization and starts the
    holding-window clock (default 3 hours, Section 13.3). Returns a QR code
    string the organizer's dashboard renders for pickup.
    """
    # FR-7.4: unverified organizations may browse but not reserve. This is
    # the gate the Good Samaritan Act protection depends on (NFR-4.8.3).
    if not (user.pantry and user.pantry.verified):
        raise HTTPException(
            status_code=403,
            detail=(
                "Your organization is still pending verification. "
                "You can browse available donations, but reserving is "
                "unavailable until a NourishNet admin verifies your EIN."
            ),
        )

    reservation = crud.create_reservation(db, res, pantry_id=user.pantry_id)
    if not reservation:
        raise HTTPException(
            status_code=409,
            detail="That item was just reserved by someone else. Refresh to see what's still available.",
        )
    return _detail(reservation)


@router.get("/mine", response_model=list[schemas.ReservationDetailOut])
def my_reservations(
    status: Optional[models.ReservationStatus] = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_organizer),
):
    """FR-8.10 — scoped to the caller's own organization, always."""
    return [_detail(r) for r in crud.list_reservations(db, pantry_id=user.pantry_id, status=status)]


@router.get("", response_model=list[schemas.ReservationDetailOut])
def all_reservations(
    status: Optional[models.ReservationStatus] = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(*auth.ALL_STORE_ROLES)),
):
    """Store-side view of every reservation, across all organizations."""
    return [_detail(r) for r in crud.list_reservations(db, status=status)]


@router.post("/{reservation_id}/cancel", response_model=schemas.ReservationDetailOut)
def cancel(
    reservation_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_organizer),
):
    """FR-8.11. Returns the item to the available pool immediately."""
    reservation = crud.cancel_reservation(db, reservation_id, pantry_id=user.pantry_id)
    if not reservation:
        # Deliberately 404 for both "not yours" and "not pending" — see
        # FR-2.4 on not confirming the existence of other orgs' records.
        raise HTTPException(
            status_code=404,
            detail="No pending reservation with that ID under your organization.",
        )
    return _detail(reservation)


@router.post("/expire-stale", response_model=list[schemas.ReservationOut])
def expire_stale(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(models.UserRole.MANAGER, models.UserRole.ADMIN)),
):
    """
    Expires PENDING reservations past their hold window and frees the item
    back to AVAILABLE. Still a manual trigger — FR-10.2 wants this on a
    scheduler running at most 5 minutes apart. Now at least it requires a
    manager token rather than being open to anyone (FR-10.5).
    """
    return crud.expire_stale_reservations(db)


@router.post("/pickup/{qr_code}", response_model=schemas.ReservationDetailOut)
def confirm_pickup(
    qr_code: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    Staff scan the organizer's QR code at the shelf to complete the handoff.
    FR-9.4: this is staff-only on purpose — before authentication existed
    this endpoint was open and the confirm form lived on the organizer's
    own page, which meant the party receiving the food confirmed its own
    pickup.
    """
    reservation = crud.confirm_pickup(db, qr_code)
    if not reservation:
        raise HTTPException(status_code=404, detail="Invalid or already-used QR code")
    return _detail(reservation)
