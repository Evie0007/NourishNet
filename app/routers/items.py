from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auth, crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/items", tags=["items"])


@router.post("", response_model=schemas.ItemOut)
def add_item(
    item: schemas.ItemCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    return crud.create_item(db, item)


@router.get("", response_model=list[schemas.ItemOut])
def query_items(
    status: Optional[models.ItemStatus] = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_user),
):
    """
    Staff see the full inventory in any status. Organizers see the donation
    pool and nothing else — the store's in-stock shelf contents are not
    theirs to browse, so their status filter is overridden rather than
    merely defaulted.
    """
    if user.role == models.UserRole.ORG_COORDINATOR:
        status = models.ItemStatus.AVAILABLE
    return crud.list_items(db, status=status)


@router.get("/near-expiry", response_model=list[schemas.ItemOut])
def near_expiry(
    within_hours: int = 48,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """Items whose sell-by date is within `within_hours` from now."""
    return crud.list_near_expiry(db, within_hours=within_hours)


@router.get("/{item_id}", response_model=schemas.ItemOut)
def get_item(
    item_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    item = crud.get_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.patch("/{item_id}/status", response_model=schemas.ItemOut)
def update_status(
    item_id: str,
    payload: schemas.ItemStatusUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    item = crud.update_item_status(db, item_id, payload.status)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.post("/{item_id}/ocr-result", response_model=schemas.ItemOut)
def submit_ocr_result(
    item_id: str,
    result: schemas.ItemOCRResult,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    Called by the OCR/CV pipeline after it reads a label (see app/ocr.py).
    Applies the >=95% confidence branch: auto-logs if confident + SKU
    matched, otherwise flags the item for employee review.

    FR-1.11: the pipeline should hold its own service credential rather
    than borrowing a staff token. Not yet built.
    """
    item = crud.apply_ocr_result(db, item_id, result)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item
