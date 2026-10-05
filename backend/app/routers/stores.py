"""
Stores, brands, and the staff accounts that belong to them.

Who creates what:
  - A platform admin creates brands and stores, and can add the first
    manager to any store or brand.
  - A store manager adds staff to their own store. A brand manager adds
    staff to any store in their brand. Brand-level accounts are created by
    a platform admin only, since a brand account can see a whole chain.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import auth, crud, models, schemas
from ..database import get_db
from .auth import _to_auth_user

router = APIRouter(prefix="/stores", tags=["stores"])


@router.get("", response_model=list[schemas.StoreOut])
def list_stores(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(*auth.ALL_STORE_ROLES)),
):
    """The stores this account can see. A platform admin sees all of them."""
    return crud.list_stores(db, store_ids=auth.visible_store_ids(db, user))


@router.post("", response_model=schemas.StoreOut)
def create_store(
    payload: schemas.StoreCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(models.UserRole.ADMIN)),
):
    if payload.brand_id and not db.get(models.Brand, payload.brand_id):
        raise HTTPException(status_code=422, detail="That brand does not exist.")
    return crud.create_store(db, payload)


@router.get("/map", response_model=list[schemas.StoreMapOut])
def store_map(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_organizer),
):
    """
    Active, located stores for the pantry map (FR-8.3, plan Phase 3). Shows
    where each store is and nothing about its shelves. Available food comes
    from GET /items, which is already limited to active stores.
    """
    return crud.list_mappable_stores(db)


@router.patch("/{store_id}", response_model=schemas.StoreOut)
def update_store(
    store_id: str,
    payload: schemas.StoreUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(models.UserRole.ADMIN)),
):
    store = crud.update_store(db, store_id, payload)
    if not store:
        raise HTTPException(status_code=404, detail="Store not found")
    return store


@router.get("/brands", response_model=list[schemas.BrandOut])
def list_brands(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(models.UserRole.ADMIN)),
):
    return crud.list_brands(db)


@router.post("/brands", response_model=schemas.BrandOut)
def create_brand(
    payload: schemas.BrandCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(models.UserRole.ADMIN)),
):
    return crud.create_brand(db, payload)


@router.post("/staff", response_model=schemas.AuthUserOut)
def create_staff(
    payload: schemas.StoreStaffCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(models.UserRole.MANAGER, models.UserRole.ADMIN)),
):
    """
    Link a new staff account to a store. Returns the new account's public
    fields, never its password.
    """
    if user.role == models.UserRole.ADMIN:
        if (payload.store_id is None) == (payload.brand_id is None):
            raise HTTPException(
                status_code=422,
                detail="Link the account to exactly one store, or to one brand.",
            )
        if payload.store_id and not db.get(models.Store, payload.store_id):
            raise HTTPException(status_code=422, detail="That store does not exist.")
        if payload.brand_id and not db.get(models.Brand, payload.brand_id):
            raise HTTPException(status_code=422, detail="That brand does not exist.")
    else:
        # A store or brand manager cannot create brand-level accounts. That
        # is the platform admin's call, since a brand account sees a chain.
        # A store-level account defaults to the manager's own store, so the
        # request does not have to repeat it.
        if payload.brand_id:
            raise HTTPException(status_code=403, detail="Only a platform admin can create a brand account.")
        payload.store_id = auth.resolve_write_store(db, user, payload.store_id)

    taken = (
        db.query(models.User)
        .filter(func.lower(models.User.email) == payload.email.strip().lower())
        .first()
    )
    if taken:
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    return _to_auth_user(crud.create_store_user(db, payload))
