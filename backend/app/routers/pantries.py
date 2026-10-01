from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auth, crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/pantries", tags=["pantries"])


@router.post("", response_model=schemas.PantryOut)
def register_pantry(pantry: schemas.PantryCreate, db: Session = Depends(get_db)):
    """
    Deliberately unauthenticated — this is the pre-auth self-registration
    flow of UC-03. `verified` stays False until an admin reviews the
    organization (FR-7.5, still unbuilt) and FR-7.4 blocks reservations
    until then (enforced in the reservations router).

    Alt-flow 3c (malformed EIN) is rejected by PantryCreate's field
    validator before this body ever runs; only 3a/3b are handled here, in
    the order the spec lists them.
    """
    if crud.get_user_by_email(db, pantry.contact_email) is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    if crud.get_pantry_by_ein(db, pantry.ein) is not None:
        raise HTTPException(
            status_code=409,
            detail="An organization with this EIN is already registered. Contact your administrator for access.",
        )
    return crud.register_organization(db, pantry)


@router.get("", response_model=list[schemas.PantryOut])
def query_pantries(
    verified_only: bool = False,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(*auth.ALL_STORE_ROLES)),
):
    """
    Store-side only. FR-7.9: this response carries EIN, phone, and contact
    email, which must not be readable by other organizations or by anyone
    unauthenticated — as it was before this guard existed.
    """
    return crud.list_pantries(db, verified_only=verified_only)
