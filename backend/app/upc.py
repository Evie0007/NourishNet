"""
UPC/EAN handling for the intake scanner.

Scanning happens on the client — a hardware wedge scanner types the digits
like a keyboard, or the browser's BarcodeDetector reads them from the
camera. This module is the server half: it normalizes whatever those
produce into one canonical form, rejects misreads before they reach the
database, and resolves a code to a catalog product.

Why normalize at all: the same physical barcode can arrive as 12 digits
(UPC-A), 13 with a leading zero (EAN-13/GTIN-13), or 8 (UPC-E, a
compressed form). Stored as-is they become three different products. Every
code is widened to 13 digits so they collapse onto one row.

Why validate the check digit: barcode misreads are not random noise, they
are plausible-looking digit strings. The final digit of every UPC/EAN is a
checksum over the others, so a single wrong digit is caught here rather
than silently creating a phantom product.
"""
import json
import os
import urllib.error
import urllib.request
from typing import Optional

from sqlalchemy.orm import Session

from . import models

# Off by default: intake must not depend on a third-party API being up.
# When enabled, an unknown barcode is looked up once, cached as a product
# row, and never fetched again.
EXTERNAL_LOOKUP_ENABLED = os.getenv("UPC_LOOKUP_ENABLED", "false").lower() in ("1", "true", "yes")
EXTERNAL_LOOKUP_URL = os.getenv(
    "UPC_LOOKUP_URL", "https://world.openfoodfacts.org/api/v2/product/{upc}.json"
)
EXTERNAL_LOOKUP_TIMEOUT = float(os.getenv("UPC_LOOKUP_TIMEOUT_SECONDS", "3"))


class InvalidBarcode(ValueError):
    """Raised for anything that is not a well-formed UPC/EAN."""


def _check_digit(digits: str) -> int:
    """
    GS1 mod-10 checksum over everything but the last digit.

    Positions are weighted 3 and 1 alternating, counted from the right, so
    the weighting is the same whether the code is 8, 12, 13 or 14 digits.
    """
    total = 0
    for i, char in enumerate(reversed(digits)):
        weight = 3 if i % 2 == 0 else 1
        total += int(char) * weight
    return (10 - total % 10) % 10


def _expand_upc_e(code: str) -> str:
    """
    Expand a UPC-E (8-digit compressed) code to its UPC-A (12-digit) form.

    The middle six digits encode where a run of zeros was removed; the last
    of them says which of the six expansion rules applies. Small packages
    (single-serve yogurt, gum) carry UPC-E precisely because UPC-A will not
    fit on them, so this is a real case at a grocery intake desk, not an
    edge case.
    """
    number_system, body, check = code[0], code[1:7], code[7]
    if number_system not in ("0", "1"):
        raise InvalidBarcode("UPC-E codes must start with 0 or 1.")

    head, last = body[:5], body[5]
    if last in ("0", "1", "2"):
        expanded = f"{number_system}{head[:2]}{last}0000{head[2:5]}"
    elif last == "3":
        expanded = f"{number_system}{head[:3]}00000{head[3:5]}"
    elif last == "4":
        expanded = f"{number_system}{head[:4]}00000{head[4]}"
    else:
        expanded = f"{number_system}{head}0000{last}"
    return expanded + check


def normalize(raw: str) -> str:
    """
    Canonicalize a scanned code to 13 digits (GTIN-13).

    Raises InvalidBarcode on anything that is not a valid 8/12/13/14-digit
    code with a correct check digit.
    """
    if raw is None:
        raise InvalidBarcode("No barcode supplied.")

    # Wedge scanners append a newline and sometimes stray whitespace or
    # hyphens from a human retyping the number off the package.
    digits = "".join(ch for ch in str(raw).strip() if ch.isdigit())

    if not digits:
        raise InvalidBarcode("That barcode contains no digits.")

    if len(digits) == 8:
        digits = _expand_upc_e(digits)
    if len(digits) == 14:
        # GTIN-14 is a case/carton code: a packaging-level digit in front of
        # the retail code. Drop it so a case and its units share a product.
        digits = digits[1:]
    if len(digits) == 12:
        digits = "0" + digits

    if len(digits) != 13:
        raise InvalidBarcode(
            f"A barcode should be 8, 12, 13 or 14 digits — this one has {len(digits)}."
        )

    if _check_digit(digits[:-1]) != int(digits[-1]):
        raise InvalidBarcode(
            "That barcode's check digit doesn't match — it was probably misread. Scan it again."
        )

    return digits


def format_display(upc: str) -> str:
    """Show a normalized GTIN-13 the way it is printed on the package."""
    return upc[1:] if upc.startswith("0") and len(upc) == 13 else upc


# ---------- Catalog resolution ----------

def find_product(db: Session, upc: str) -> Optional[models.Product]:
    """Look up a normalized code in the store's catalog."""
    return db.query(models.Product).filter(models.Product.upc == upc).first()


def lookup_external(upc: str) -> Optional[dict]:
    """
    Ask the configured external database about an unknown barcode.

    Returns a dict of product fields, or None for "no answer" — which
    covers a miss, a timeout, a malformed response, and the service being
    down alike. Every one of those means the same thing at the intake desk:
    the person types the name in themselves. Intake never blocks on this.
    """
    if not EXTERNAL_LOOKUP_ENABLED:
        return None

    try:
        url = EXTERNAL_LOOKUP_URL.format(upc=upc)
        request = urllib.request.Request(url, headers={"User-Agent": "NourishNet/0.3 (intake)"})
        with urllib.request.urlopen(request, timeout=EXTERNAL_LOOKUP_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    product = payload.get("product") or {}
    name = product.get("product_name") or product.get("generic_name")
    if not name:
        return None

    return {
        "name": name.strip()[:200],
        "brand": (product.get("brands") or "").split(",")[0].strip()[:120] or None,
        "category": (product.get("categories") or "").split(",")[-1].strip()[:120] or None,
    }


def resolve(db: Session, raw_upc: str) -> tuple[str, Optional[models.Product], bool]:
    """
    The scanner's one entry point: raw code in, (normalized code, product,
    newly_cached) out.

    A catalog hit returns immediately. A miss optionally falls through to
    the external lookup, and anything it finds is written to the catalog so
    the second unit off the same pallet resolves instantly and offline.
    `product` is None when nothing knows the code — a normal outcome, and
    the reason the confirmation screen always lets staff type a name.
    """
    upc = normalize(raw_upc)

    product = find_product(db, upc)
    if product:
        return upc, product, False

    external = lookup_external(upc)
    if not external:
        return upc, None, False

    product = models.Product(upc=upc, source="external", **external)
    db.add(product)
    db.commit()
    db.refresh(product)
    return upc, product, True
