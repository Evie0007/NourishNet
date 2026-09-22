"""
FR-11.3 / FR-11.7: what a business pulls at tax time — the donation history
its own staff confirmed, valued at the catalog price of each item, exportable
for an accountant. Store-side only (auth.require_staff): this is the store's
own financial record, not something a pantry organizer has any claim to.
"""
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import auth, crud, models, schemas
from ..database import get_db

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/donations", response_model=list[schemas.DonationRecordOut])
def list_donations(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pantry_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    return crud.list_donation_records(db, date_from=date_from, date_to=date_to, pantry_id=pantry_id)


@router.get("/donations/export")
def export_donations(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pantry_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """Same rows as list_donations, as a CSV a person can hand to an
    accountant without also giving them API access."""
    records = crud.list_donation_records(db, date_from=date_from, date_to=date_to, pantry_id=pantry_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "confirmed_at", "item_name", "item_sku", "item_category",
        "sell_by_date_at_handoff", "unit_value_at_handoff",
        "pantry_name_at_handoff", "reservation_id",
    ])
    for r in records:
        writer.writerow([
            r.confirmed_at.isoformat() if r.confirmed_at else "",
            r.item_name,
            r.item_sku or "",
            r.item_category or "",
            r.sell_by_date_at_handoff.isoformat() if r.sell_by_date_at_handoff else "",
            r.unit_value_at_handoff if r.unit_value_at_handoff is not None else "",
            r.pantry_name_at_handoff,
            r.reservation_id,
        ])
    buffer.seek(0)

    filename = f"donations_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
