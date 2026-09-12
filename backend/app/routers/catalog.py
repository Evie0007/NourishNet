"""
The UPC catalog and the expiration rules — the two reference tables the
intake pipeline reads from.

Split by who may write them: any staff member adds a product, because an
unknown barcode at the dock has to be resolvable by whoever is standing
there. Only a manager edits an expiration rule, because those numbers
decide what gets offered to a food pantry and when (FR-2, permission
matrix: "Configure hold window / confidence threshold").
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import auth, crud, expiration, models, schemas, upc as upc_lib
from ..database import get_db

router = APIRouter(prefix="/catalog", tags=["catalog"])

require_manager = auth.require_role(models.UserRole.MANAGER)


# ---------- UPC lookup ----------

@router.get("/upc/{raw_upc}", response_model=schemas.UpcLookupOut)
def lookup_upc(
    raw_upc: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    Resolve a scanned barcode. The first call the intake screen makes.

    A code that resolves to nothing comes back 200 with `product: null`,
    not 404. The distinction matters at the dock: 404 reads as "something
    went wrong," when in fact the scan worked perfectly and the catalog
    simply hasn't met this product yet. The screen's next step is to ask
    for a name, not to show an error.
    """
    try:
        normalized, product, newly_cached = upc_lib.resolve(db, raw_upc)
    except upc_lib.InvalidBarcode as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    category = product.category if product else None
    rule = expiration.get_rule(db, category)
    shelf_life = (product.default_shelf_life_days if product else None) or (
        rule.default_shelf_life_days if rule else None
    )

    return schemas.UpcLookupOut(
        upc=normalized,
        display_upc=upc_lib.format_display(normalized),
        product=product,
        suggested_shelf_life_days=shelf_life,
        suggested_category=category,
        # Restricted either because this product is flagged, or because its
        # whole category is (infant formula). Surfaced here so the intake
        # screen can say so before anyone photographs a label.
        donation_restricted=bool(
            (product and product.donation_restricted) or (rule and not rule.auto_publish)
        ),
        newly_cached=newly_cached,
    )


# ---------- Products ----------

@router.get("/products", response_model=list[schemas.ProductOut])
def list_products(
    search: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    return crud.list_products(db, search=search, limit=limit)


@router.post("/products", response_model=schemas.ProductOut)
def create_product(
    product: schemas.ProductCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """
    Add or correct a catalog entry — how an unknown barcode becomes a known
    one. Scanning the same code again resolves instantly from then on.
    """
    try:
        return crud.upsert_product(db, product)
    except upc_lib.InvalidBarcode as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/products/{product_id}", response_model=schemas.ProductOut)
def update_product(
    product_id: str,
    patch: schemas.ProductUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    product = crud.update_product(db, product_id, patch)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


# ---------- Expiration rules ----------

@router.get("/expiration-rules", response_model=list[schemas.ExpirationRuleOut])
def list_rules(
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.require_staff),
):
    """Readable by any staff member: these numbers explain every status
    change the system makes on its own, and staff who cannot see the reason
    for a transition end up working around it."""
    return crud.list_expiration_rules(db)


@router.post("/expiration-rules", response_model=schemas.ExpirationRuleOut)
def create_rule(
    rule: schemas.ExpirationRuleCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_manager),
):
    return crud.upsert_expiration_rule(db, rule)


@router.patch("/expiration-rules/{rule_id}", response_model=schemas.ExpirationRuleOut)
def update_rule(
    rule_id: str,
    patch: schemas.ExpirationRuleUpdate,
    recompute: bool = Query(
        default=False,
        description=(
            "Also re-apply the new numbers to items of this category already "
            "in stock. Off by default: retuning a category should change how "
            "the next delivery is handled, not silently move a deadline on "
            "food a person already signed off on."
        ),
    ),
    db: Session = Depends(get_db),
    user: models.User = Depends(require_manager),
):
    updated = crud.update_expiration_rule(db, rule_id, patch)
    if not updated:
        raise HTTPException(status_code=404, detail="Expiration rule not found")
    if recompute:
        crud.recompute_deadlines_for_category(db, updated.category)
    return updated
