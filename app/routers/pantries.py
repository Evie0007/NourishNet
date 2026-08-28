from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..database import get_db

router = APIRouter(prefix="/pantries", tags=["pantries"])


@router.post("", response_model=schemas.PantryOut)
def register_pantry(pantry: schemas.PantryCreate, db: Session = Depends(get_db)):
    """
    Registration only — per Section 4.3 of the proposal, `verified` stays
    False until org name / EIN / address / phone / email are confirmed.
    Wire up the verification step before this ships past the prototype.
    """
    return crud.create_pantry(db, pantry)


@router.get("", response_model=list[schemas.PantryOut])
def query_pantries(verified_only: bool = False, db: Session = Depends(get_db)):
    return crud.list_pantries(db, verified_only=verified_only)
