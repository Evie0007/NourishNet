from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import auth, crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/pantries", tags=["pantries"])


@router.post("", response_model=schemas.PantryOut)
def register_pantry(pantry: schemas.PantryCreate, db: Session = Depends(get_db)):
    """
    Deliberately unauthenticated — this is the pre-auth self-registration
    flow of UC-03. Per Section 4.3 of the proposal, `verified` stays False
    until org name / EIN / address / phone / email are confirmed, and
    FR-7.4 blocks reservations until then (enforced in the reservations
    router). Still missing: the admin queue that flips `verified` to True.
    """
    return crud.create_pantry(db, pantry)


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
