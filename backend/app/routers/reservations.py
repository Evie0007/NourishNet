from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, object_session

from .. import auth, crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/reservations", tags=["reservations"])


def _detail(res: models.Reservation) -> schemas.ReservationDetailOut:
    """Flattens the joined item/shelf/pantry names the dashboards display."""
    item = res.item
    order = res.order
    # An item has no store relationship of its own, only a store_id.
    store = None
    if order is not None:
        store = order.store
    elif item is not None and item.store_id:
        store = object_session(res).get(models.Store, item.store_id)
    return schemas.ReservationDetailOut(
        **schemas.ReservationOut.model_validate(res).model_dump(),
        item_name=item.name if item else None,
        item_category=item.category if item else None,
        item_sell_by_date=item.sell_by_date if item else None,
        shelf_name=item.shelf.name if item and item.shelf else None,
        pantry_name=res.pantry.org_name if res.pantry else None,
        store_name=store.name if store else None,
        order_id=order.id if order else None,
        order_qr_code=order.qr_code if order else None,
        order_item_names=(
            [
                r.item.name
                for r in order.reservations
                if r.item and r.status not in (
                    models.ReservationStatus.CANCELLED,
                    models.ReservationStatus.EXPIRED,
                )
            ]
            if order
            else None
        ),
    )


def _require_verified(user: models.User) -> None:
    """FR-7.4: unverified organizations may browse but not reserve. This is
    the gate the Good Samaritan Act protection depends on (NFR-4.8.3)."""
    if not (user.pantry and user.pantry.verified):
        raise HTTPException(
            status_code=403,
            detail=(
                "Your organization is still pending verification. "
                "You can browse available donations, but reserving is "
                "unavailable until a NourishNet admin verifies your EIN."
            ),
        )


@router.post("", response_model=schemas.ReservationDetailOut)
def reserve_item(
    res: schemas.ReservationCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_organizer),
):
    """
    Reserves an AVAILABLE item for the caller's organization at a scheduled
    pickup time, and holds it until PICKUP_GRACE past that slot (FR-8.6).
    Returns a QR code string the organizer's dashboard renders for pickup.

    Note the ordering: Pydantic validates the body before this function
    runs, so an unverified organization submitting an out-of-range pickup
    time sees the 422 about the time, not the 403 about verification. That
    is acceptable — they hit the 403 on the next attempt — but it is why
    the verification test below cannot assume it runs first.
    """
    _require_verified(user)

    reservation, outcome = crud.create_reservation(db, res, pantry_id=user.pantry_id)
    if outcome == "unavailable":
        raise HTTPException(
            status_code=409,
            detail="That item was just reserved by someone else. Refresh to see what's still available.",
        )
    if outcome == "past_discard":
        item = db.get(models.Item, res.item_id)
        deadline = item.discard_after.strftime("%Y-%m-%d %H:%M") if item else "its discard time"
        raise HTTPException(
            status_code=422,
            detail=(
                f"This item has to be off the shelf by {deadline} UTC. "
                "Choose a pickup time before then."
            ),
        )
    return _detail(reservation)


@router.post("/orders", response_model=list[schemas.ReservationDetailOut])
def reserve_order(
    order: schemas.PickupOrderCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_organizer),
):
    """
    Reserves several items from one location for a single trip, and returns
    them all carrying the one QR code that covers the order (FR-8.13).

    All or nothing: if any item can't be had, none is reserved and the message
    names the one that stopped it, so the organization can drop it and retry.
    """
    _require_verified(user)

    db_order, outcome, item_name = crud.create_order(db, order, pantry_id=user.pantry_id)
    if outcome == "unavailable":
        which = f"“{item_name}”" if item_name else "One of those items"
        raise HTTPException(
            status_code=409,
            detail=(
                f"{which} was just reserved by someone else, or its location has "
                "closed. Nothing was reserved — remove it and try again."
            ),
        )
    if outcome == "mixed_stores":
        raise HTTPException(
            status_code=422,
            detail="A pickup covers one location. Make a separate pickup for items at another shelf.",
        )
    if outcome == "past_discard":
        raise HTTPException(
            status_code=422,
            detail=(
                f"“{item_name}” has to be off the shelf before that time. "
                "Choose an earlier pickup time, or take that item out of the pickup."
            ),
        )
    return [_detail(r) for r in db_order.reservations]


@router.post("/orders/{order_id}/cancel", response_model=list[schemas.ReservationDetailOut])
def cancel_pickup_order(
    order_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_organizer),
):
    """Cancels a whole pickup; every item returns to the available pool."""
    db_order = crud.cancel_order(db, order_id, pantry_id=user.pantry_id)
    if not db_order:
        # 404 for "not yours" and "nothing pending" alike — see FR-2.4.
        raise HTTPException(
            status_code=404,
            detail="No pending pickup with that ID under your organization.",
        )
    return [_detail(r) for r in db_order.reservations]


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
    """Store-side view of reservations across all organizations, limited to
    the caller's own stores' food."""
    store_ids = auth.visible_store_ids(db, user)
    return [_detail(r) for r in crud.list_reservations(db, status=status, store_ids=store_ids)]


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

    The failure messages name the actual reason (FR-9.6, FR-9.8). A staff
    member scanning at the shelf has no other channel to find out whether
    the problem is the wrong code, a second scan, or an organization that
    arrived too late.
    """
    reservation, outcome = crud.confirm_pickup(
        db, qr_code, confirmed_by_user_id=user.id, store_ids=auth.visible_store_ids(db, user)
    )

    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="That code doesn't match any reservation.")
    if outcome == "already_picked_up":
        when = reservation.picked_up_at.strftime("%H:%M") if reservation.picked_up_at else "earlier"
        raise HTTPException(status_code=409, detail=f"Already collected at {when} UTC.")
    if outcome == "cancelled":
        raise HTTPException(
            status_code=409,
            detail="That reservation was cancelled. The item is back in the available pool.",
        )
    if outcome == "expired":
        when = reservation.hold_expires_at.strftime("%H:%M") if reservation.hold_expires_at else ""
        raise HTTPException(
            status_code=409,
            detail=(
                f"The hold lapsed at {when} UTC and the item has returned to the "
                "available pool. Ask them to reserve it again."
            ),
        )
    return _detail(reservation)
