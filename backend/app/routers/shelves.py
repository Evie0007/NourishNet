from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auth, crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/shelves", tags=["shelves"])


@router.post("", response_model=schemas.ShelfOut)
def add_shelf(
    shelf: schemas.ShelfCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    return crud.create_shelf(db, shelf)


@router.get("", response_model=list[schemas.ShelfOut])
def query_shelves(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_role(*auth.ALL_STORE_ROLES)),
):
    return crud.list_shelves(db)


@router.patch("/{shelf_id}/reading", response_model=schemas.ShelfOut)
def update_reading(
    shelf_id: str,
    reading: schemas.ShelfReadingUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    Called by the microcontroller/sensor bridge every N seconds.

    FR-1.11 wants the bridge on its own service credential rather than a
    human staff token. Until that exists, a staff token is what it uses.
    """
    shelf = crud.update_shelf_reading(db, shelf_id, reading)
    if not shelf:
        raise HTTPException(status_code=404, detail="Shelf not found")
    return shelf
