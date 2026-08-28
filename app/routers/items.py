from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, schemas, models
from ..database import get_db

router = APIRouter(prefix="/items", tags=["items"])


@router.post("", response_model=schemas.ItemOut)
def add_item(item: schemas.ItemCreate, db: Session = Depends(get_db)):
    return crud.create_item(db, item)


@router.get("", response_model=list[schemas.ItemOut])
def query_items(status: Optional[models.ItemStatus] = None, db: Session = Depends(get_db)):
    return crud.list_items(db, status=status)


@router.get("/near-expiry", response_model=list[schemas.ItemOut])
def near_expiry(within_hours: int = 48, db: Session = Depends(get_db)):
    """Items whose sell-by date is within `within_hours` from now."""
    return crud.list_near_expiry(db, within_hours=within_hours)


@router.get("/{item_id}", response_model=schemas.ItemOut)
def get_item(item_id: str, db: Session = Depends(get_db)):
    item = crud.get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.patch("/{item_id}/status", response_model=schemas.ItemOut)
def update_status(item_id: str, payload: schemas.ItemStatusUpdate, db: Session = Depends(get_db)):
    item = crud.update_item_status(db, item_id, payload.status)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.post("/{item_id}/ocr-result", response_model=schemas.ItemOut)
def submit_ocr_result(item_id: str, result: schemas.ItemOCRResult, db: Session = Depends(get_db)):
    """
    Called by the OCR/CV pipeline after it reads a label (see app/ocr.py).
    Applies the >=95% confidence branch: auto-logs if confident + SKU
    matched, otherwise flags the item for employee review.
    """
    item = crud.apply_ocr_result(db, item_id, result)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item
