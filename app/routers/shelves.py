from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..database import get_db

router = APIRouter(prefix="/shelves", tags=["shelves"])


@router.post("", response_model=schemas.ShelfOut)
def add_shelf(shelf: schemas.ShelfCreate, db: Session = Depends(get_db)):
    return crud.create_shelf(db, shelf)


@router.get("", response_model=list[schemas.ShelfOut])
def query_shelves(db: Session = Depends(get_db)):
    return crud.list_shelves(db)


@router.patch("/{shelf_id}/reading", response_model=schemas.ShelfOut)
def update_reading(shelf_id: str, reading: schemas.ShelfReadingUpdate, db: Session = Depends(get_db)):
    """Called by the microcontroller/sensor bridge every N seconds."""
    shelf = crud.update_shelf_reading(db, shelf_id, reading)
    if not shelf:
        raise HTTPException(status_code=404, detail="Shelf not found")
    return shelf
