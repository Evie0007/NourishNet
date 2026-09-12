"""
The intake station: UPC scan, OCR date read, manual confirmation.

    POST /intake/scans                  barcode scanned, scan opens
    POST /intake/scans/{id}/date        label photographed, OCR proposes a date
    POST /intake/scans/{id}/ocr-image   same, but the server calls Vision
    GET  /intake/scans?status=pending   the confirmation queue
    POST /intake/scans/{id}/confirm     a person accepts → Item(s) created
    POST /intake/scans/{id}/reject      a person refuses → nothing created

Only the confirm step writes to inventory. That is the design: two
machines propose, a person disposes. A barcode is reliable about identity
and silent about age; an OCR date read is a guess with a confidence score
attached. Neither is a basis for putting food in front of a family, and
the confirmation step is what stands between them and that.
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from .. import auth, crud, expiration, models, ocr, schemas, upc as upc_lib
from ..database import get_db

router = APIRouter(prefix="/intake", tags=["intake"])

# Big enough for a phone photo of a label, small enough that a mistaken
# upload of a video cannot exhaust the worker's memory.
MAX_LABEL_IMAGE_BYTES = 8 * 1024 * 1024


def _decorate(db: Session, scan: models.IntakeScan) -> schemas.IntakeScanOut:
    """
    Render a scan for the confirmation screen, including what the
    expiration rules *would* produce if it were confirmed as it stands.

    The projection is the point. Showing the person the discard deadline
    before they press confirm turns the rules into something they can check
    against the package in their hand; showing it afterwards turns them
    into something that happens to inventory for reasons nobody can see.
    """
    out = schemas.IntakeScanOut.model_validate(scan)

    product = scan.product
    out.product_name = scan.name_override or (product.name if product else None)
    out.product_category = scan.category_override or (product.category if product else None)
    out.display_upc = upc_lib.format_display(scan.upc) if scan.upc else None

    proposed_date = scan.confirmed_date or scan.detected_date
    if proposed_date:
        is_use_by = scan.detected_label_type == models.DateLabelType.USE_BY
        rule = expiration.get_rule(db, out.product_category)
        # A throwaway Item, never added to the session, run through the same
        # function the real one will be. Reimplementing the arithmetic here
        # instead would give the person a preview that could quietly stop
        # matching what actually happens.
        preview = models.Item(
            category=out.product_category,
            sell_by_date=None if is_use_by else proposed_date,
            use_by_date=proposed_date if is_use_by else None,
        )
        expiration.apply_rules_to_item(db, preview)
        out.projected_donate_after = preview.donate_after
        out.projected_discard_after = preview.discard_after
        out.projected_auto_publish = bool(
            preview.donate_after
            and rule.auto_publish
            and not (product.donation_restricted if product else False)
        )

    return out


@router.post("/scans", response_model=schemas.IntakeScanOut)
def open_scan(
    scan: schemas.IntakeScanCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    Open a scan from a barcode read.

    The barcode is optional, because a crushed or missing label is an
    ordinary event at a loading dock and refusing the unit would mean
    throwing away food over a printing defect. A scan with no UPC is
    confirmed the same way; the person types the name.
    """
    try:
        return _decorate(db, crud.create_intake_scan(db, scan, user.id))
    except upc_lib.InvalidBarcode as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/scans", response_model=list[schemas.IntakeScanOut])
def list_scans(
    status: Optional[models.ScanStatus] = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """The pending queue, oldest first. Pass ?status=confirmed to read back
    the intake log — every read, what the machine proposed, and what the
    person accepted (FR-5.10, FR-11.5)."""
    return [_decorate(db, scan) for scan in crud.list_intake_scans(db, status=status, limit=limit)]


@router.get("/scans/{scan_id}", response_model=schemas.IntakeScanOut)
def get_scan(
    scan_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    scan = db.get(models.IntakeScan, scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    return _decorate(db, scan)


@router.post("/scans/{scan_id}/date", response_model=schemas.IntakeScanOut)
def submit_date_result(
    scan_id: str,
    result: schemas.IntakeDateResult,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    Attach a date read to an open scan.

    Used when OCR ran somewhere else — on the device, or in a separate
    pipeline process. The scan stays pending either way: nothing here can
    move an item, only propose a date for one.

    FR-1.11: a pipeline running unattended should hold its own service
    credential rather than borrowing a staff token. Still outstanding.
    """
    scan = crud.apply_intake_date(db, scan_id, result)
    if not scan:
        raise HTTPException(
            status_code=404,
            detail="No scan waiting on a date read — it may already have been confirmed.",
        )
    return _decorate(db, scan)


@router.post("/scans/{scan_id}/ocr-image", response_model=schemas.IntakeScanOut)
async def submit_label_image(
    scan_id: str,
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    Upload a label photo and let the server read it.

    Vision being unavailable is not an error here (FR-5.7, NFR-4.4.3): the
    scan comes back with no date proposed, the screen falls back to a typed
    date, and the dock keeps moving. An outage at Google is not a reason to
    stop receiving food.

    NFR-4.2.1/4.2.2: the frame is read and dropped. It is not written to
    disk, because a still of a product label is only privacy-safe as long
    as nobody is standing behind it, and that is not something this
    endpoint can verify. Wiring up image storage means solving retention
    (NFR-4.2.3) first.
    """
    content = await image.read()
    if len(content) > MAX_LABEL_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That image is larger than {MAX_LABEL_IMAGE_BYTES // (1024 * 1024)} MB. Take a smaller photo.",
        )

    result = ocr.extract_text_from_bytes(content)
    best = result.best

    payload = schemas.IntakeDateResult(
        ocr_raw_text=result.raw_text or None,
        ocr_confidence=result.confidence,
        date_confidence=best.confidence if best else None,
        detected_date=best.date if best else None,
        detected_label_type=best.label_type if best else models.DateLabelType.UNKNOWN,
        date_candidates=[c.as_dict() for c in result.candidates] or None,
    )

    scan = crud.apply_intake_date(db, scan_id, payload)
    if not scan:
        raise HTTPException(
            status_code=404,
            detail="No scan waiting on a date read — it may already have been confirmed.",
        )
    return _decorate(db, scan)


@router.post("/scans/{scan_id}/confirm", response_model=list[schemas.ItemOut])
def confirm_scan(
    scan_id: str,
    payload: schemas.IntakeConfirm,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    The confirmation step. This is the only endpoint in the intake pipeline
    that creates inventory.

    Returns the items created — one per unit of `quantity`, sharing a batch
    — each already carrying the donate_after and discard_after deadlines
    the scheduler will act on.

    A scan that is already confirmed or rejected returns 409 rather than
    creating a second set of items, so a double-tapped Confirm button on a
    phone at the dock cannot duplicate a pallet.
    """
    result = crud.confirm_intake_scan(db, scan_id, payload, user.id)
    if result is None:
        raise HTTPException(
            status_code=409,
            detail="That scan is no longer waiting for confirmation — it was already confirmed or rejected.",
        )
    _scan, items = result
    return items


@router.post("/scans/{scan_id}/reject", response_model=schemas.IntakeScanOut)
def reject_scan(
    scan_id: str,
    payload: schemas.IntakeRejectRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    Refuse a scan. No item is created and no reason is required
    (NFR-4.8.4). The scan stays as the record of what was turned away.
    """
    scan = crud.reject_intake_scan(db, scan_id, payload.notes, user.id)
    if not scan:
        raise HTTPException(
            status_code=409,
            detail="That scan is no longer waiting for confirmation.",
        )
    return _decorate(db, scan)
