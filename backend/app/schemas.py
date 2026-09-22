"""
Pydantic schemas — these define the JSON shape of the API, separate from
the SQLAlchemy models in models.py. Keeping them separate means we can
change the DB schema without automatically changing what the app/OCR
pipeline sends and receives, and vice versa.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from pydantic import (
    AwareDatetime, BaseModel, EmailStr, ConfigDict, Field, field_validator, model_validator
)

from .models import (
    DateLabelType, DateSource, ItemStatus, ReservationStatus, ScanStatus, UserRole
)


# ---------- Auth ----------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthUserOut(BaseModel):
    """What the client needs to render the right dashboard (FR-1.10).
    The pantry fields are flattened in so the organizer view can show its
    org name and verification banner without a second request."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    full_name: Optional[str]
    role: UserRole
    pantry_id: Optional[str] = None
    pantry_name: Optional[str] = None
    pantry_verified: Optional[bool] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: AuthUserOut


# ---------- Shelf ----------

class ShelfCreate(BaseModel):
    name: str
    location: Optional[str] = None
    camera_id: Optional[str] = None


class ShelfReadingUpdate(BaseModel):
    current_temperature_c: Optional[float] = None
    current_humidity_pct: Optional[float] = None


class ShelfOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    location: Optional[str]
    camera_id: Optional[str]
    current_temperature_c: Optional[float]
    current_humidity_pct: Optional[float]
    last_reading_at: Optional[datetime]


# ---------- Item ----------

class ItemCreate(BaseModel):
    name: str
    sku: Optional[str] = None
    upc: Optional[str] = None
    batch_id: Optional[str] = None
    category: Optional[str] = None
    sell_by_date: Optional[datetime] = None
    use_by_date: Optional[datetime] = None
    date_label_type: DateLabelType = DateLabelType.UNKNOWN
    arrival_date: Optional[datetime] = None
    shelf_id: Optional[str] = None
    image_url: Optional[str] = None
    # Usually left unset and inherited from the matched Product at creation
    # (crud.py) — set here only to override the catalog value for this unit.
    unit_value: Optional[Decimal] = None


class ItemStatusUpdate(BaseModel):
    status: ItemStatus


class ItemOCRResult(BaseModel):
    """Payload the OCR/CV pipeline posts back after reading a label."""
    ocr_raw_text: Optional[str] = None
    ocr_confidence: float
    sku_match_confirmed: bool = False
    sell_by_date: Optional[datetime] = None


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    sku: Optional[str]
    upc: Optional[str] = None
    batch_id: Optional[str]
    category: Optional[str]
    sell_by_date: Optional[datetime]
    use_by_date: Optional[datetime] = None
    date_label_type: DateLabelType = DateLabelType.UNKNOWN
    date_source: DateSource = DateSource.UNKNOWN
    # The two deadlines the scheduler acts on. Sent to the client so the
    # dashboard can show when an item will move rather than only that it
    # did — an unexplained status change is what makes staff stop trusting
    # the automation and start double-checking everything by hand.
    donate_after: Optional[datetime] = None
    discard_after: Optional[datetime] = None
    arrival_date: Optional[datetime]
    shelf_id: Optional[str]
    unit_value: Optional[Decimal] = None
    status: ItemStatus
    ocr_raw_text: Optional[str]
    ocr_confidence: Optional[float]
    sku_match_confirmed: bool
    image_url: Optional[str]
    created_at: datetime
    updated_at: datetime


# ---------- Product catalog (UPC scanner) ----------

class ProductBase(BaseModel):
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    sku: Optional[str] = None
    default_shelf_life_days: Optional[int] = Field(default=None, ge=0, le=3650)
    date_label_type: DateLabelType = DateLabelType.SELL_BY
    donation_restricted: bool = False
    # Per-unit value used to value a donated unit of this product for tax
    # records (FR-11.1). Left unset for a product with no known value.
    unit_value: Optional[Decimal] = Field(default=None, ge=0)


class ProductCreate(ProductBase):
    """`upc` is normalized and check-digit validated server-side, so
    whatever the scanner or a person's typing produced is accepted here as
    a plain string and rejected with 422 if it isn't a real barcode."""
    upc: str


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    sku: Optional[str] = None
    default_shelf_life_days: Optional[int] = Field(default=None, ge=0, le=3650)
    date_label_type: Optional[DateLabelType] = None
    donation_restricted: Optional[bool] = None
    unit_value: Optional[Decimal] = Field(default=None, ge=0)


class ProductOut(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    upc: str
    source: Optional[str] = None
    created_at: datetime


class UpcLookupOut(BaseModel):
    """
    What the intake screen gets back the instant a barcode is scanned.

    `product` is None for a code nothing knows, which is an ordinary
    outcome and not an error — the screen then asks the person for a name.
    """
    upc: str
    display_upc: str
    product: Optional[ProductOut] = None
    # Populated from the catalog or the category rule, so the screen can
    # pre-fill an estimated date before any label is photographed.
    suggested_shelf_life_days: Optional[int] = None
    suggested_category: Optional[str] = None
    donation_restricted: bool = False
    newly_cached: bool = False


# ---------- Expiration rules ----------

class ExpirationRuleBase(BaseModel):
    near_expiry_hours: int = Field(default=48, ge=0, le=8760)
    publish_offset_hours: int = Field(default=0, ge=-8760, le=8760)
    discard_after_hours: int = Field(default=48, ge=0, le=8760)
    auto_publish: bool = True
    default_shelf_life_days: Optional[int] = Field(default=None, ge=0, le=3650)
    notes: Optional[str] = None


class ExpirationRuleCreate(ExpirationRuleBase):
    category: str


class ExpirationRuleUpdate(BaseModel):
    near_expiry_hours: Optional[int] = Field(default=None, ge=0, le=8760)
    publish_offset_hours: Optional[int] = Field(default=None, ge=-8760, le=8760)
    discard_after_hours: Optional[int] = Field(default=None, ge=0, le=8760)
    auto_publish: Optional[bool] = None
    default_shelf_life_days: Optional[int] = Field(default=None, ge=0, le=3650)
    notes: Optional[str] = None


class ExpirationRuleOut(ExpirationRuleBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    category: str
    updated_at: Optional[datetime] = None


class SweepResultOut(BaseModel):
    """Counts from one expiration sweep, per transition."""
    discarded: int = 0
    released_from_reserved: int = 0
    published: int = 0
    near_expiry: int = 0
    reservations_expired: int = 0


# ---------- Intake (UPC + OCR + manual confirmation) ----------

class IntakeScanCreate(BaseModel):
    """
    Opens a scan. Everything is optional but the two scanners' output,
    because the flow tolerates either half failing: a barcode with no
    legible date, or a date on a package whose barcode won't read.
    """
    upc: Optional[str] = None
    shelf_id: Optional[str] = None
    quantity: int = Field(default=1, ge=1, le=999)
    batch_id: Optional[str] = None
    name_override: Optional[str] = None
    category_override: Optional[str] = None
    image_url: Optional[str] = None
    notes: Optional[str] = None


class IntakeDateResult(BaseModel):
    """
    The OCR date scanner's output, posted against an open scan.

    Separate from IntakeScanCreate because in the real flow these arrive at
    different moments — the barcode the instant the unit is picked up, the
    date once the camera has framed the label.
    """
    ocr_raw_text: Optional[str] = None
    ocr_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    date_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    detected_date: Optional[datetime] = None
    detected_label_type: DateLabelType = DateLabelType.UNKNOWN
    date_candidates: Optional[list[dict[str, Any]]] = None
    image_url: Optional[str] = None


class IntakeConfirm(BaseModel):
    """
    The manual confirmation step — the only thing that creates an Item.

    `confirmed_date` is required and deliberately not defaulted to the OCR
    reading. Defaulting it would turn confirmation into a button someone
    presses without looking, which is exactly the failure this step exists
    to prevent. The client pre-fills the field; the person has to submit it.
    """
    confirmed_date: datetime
    date_label_type: DateLabelType = DateLabelType.SELL_BY
    name: Optional[str] = None
    category: Optional[str] = None
    shelf_id: Optional[str] = None
    batch_id: Optional[str] = None
    quantity: Optional[int] = Field(default=None, ge=1, le=999)
    # True when the person accepted the date the OCR proposed; False when
    # they typed a different one. Recorded rather than inferred so the
    # pipeline's real error rate is countable (FR-5.10, FR-11.5).
    accepted_ocr_date: bool = False


class IntakeRejectRequest(BaseModel):
    """Rejecting a scan never requires a reason (NFR-4.8.4) — a food-safety
    judgment must not wait on a form field."""
    notes: Optional[str] = None


class IntakeScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    upc: Optional[str]
    product_id: Optional[str]
    name_override: Optional[str]
    category_override: Optional[str]
    ocr_raw_text: Optional[str]
    ocr_confidence: Optional[float]
    date_confidence: Optional[float]
    detected_date: Optional[datetime]
    detected_label_type: DateLabelType
    date_candidates: Optional[list[dict[str, Any]]]
    image_url: Optional[str]
    status: ScanStatus
    shelf_id: Optional[str]
    quantity: int
    batch_id: Optional[str]
    notes: Optional[str]
    confirmed_at: Optional[datetime]
    confirmed_date: Optional[datetime]
    item_id: Optional[str]
    created_at: datetime

    # Joined in by the router so the confirmation screen can render a row
    # without a second request per scan.
    product_name: Optional[str] = None
    product_category: Optional[str] = None
    display_upc: Optional[str] = None
    # What the rules would produce for this scan, previewed before anyone
    # commits to it — so the deadlines are visible at the moment of the
    # decision rather than discovered afterwards.
    projected_donate_after: Optional[datetime] = None
    projected_discard_after: Optional[datetime] = None
    projected_auto_publish: Optional[bool] = None


# ---------- Pantry ----------

class PantryCreate(BaseModel):
    org_name: str
    ein: str
    address: Optional[str] = None
    phone: Optional[str] = None
    contact_email: EmailStr


class PantryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    org_name: str
    ein: str
    address: Optional[str]
    phone: Optional[str]
    contact_email: EmailStr
    verified: bool
    created_at: datetime


# ---------- Reservation ----------

# The hold used to be a flat window from the moment someone clicked Reserve,
# which told staff nothing about when anyone would actually arrive. Now the
# organization states a slot and the hold is derived from it. These three
# constants are the whole policy; crud.py imports them rather than
# re-deriving the arithmetic.

# How long after the promised slot the item stays held. A no-show frees the
# food half an hour after the time they gave us, not hours after they
# happened to click.
PICKUP_GRACE = timedelta(minutes=30)

# How far ahead a slot may be booked. Longer means an item sits RESERVED and
# invisible to every other organization for that long.
SCHEDULE_HORIZON = timedelta(hours=24)

# Absorbs clock drift between the organizer's browser and this server, plus
# the round trip. Without it, someone who picks "in one minute" and takes
# ninety seconds to submit is rejected for a reason they cannot see.
SUBMIT_SKEW = timedelta(minutes=2)


class ReservationCreate(BaseModel):
    """Note the absence of pantry_id: FR-2.3 requires the acting
    organization to come from the authenticated user's account, never from
    the request body. Accepting it here would let any coordinator reserve
    food in another organization's name."""
    item_id: str

    # AwareDatetime, not datetime. A naive value means a browser sent local
    # wall time without converting it, and reading that as UTC is a
    # seven-hour error in California that surfaces much later as a hold that
    # lapsed before it started. Requiring the offset turns a silent
    # wrong-answer bug into a loud 422 on the first request.
    scheduled_pickup_at: AwareDatetime

    @field_validator("scheduled_pickup_at")
    @classmethod
    def _to_naive_utc(cls, value: datetime) -> datetime:
        """Every DateTime column in this application is naive UTC. Normalize
        at the edge so nothing downstream has to know which convention the
        value it is holding came in as."""
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @model_validator(mode="after")
    def _within_booking_window(self):
        """FR-8.6. Field validators run first, so both sides of these
        comparisons are naive UTC.

        The permitted boundary goes into the message on purpose: if the
        browser's clock is wrong, the only way the person can tell is by
        seeing the time this server thinks it is.
        """
        now = datetime.utcnow()
        if self.scheduled_pickup_at < now - SUBMIT_SKEW:
            raise ValueError(
                "Pick a pickup time in the future — that one has already passed."
            )
        latest = now + SCHEDULE_HORIZON
        if self.scheduled_pickup_at > latest + SUBMIT_SKEW:
            raise ValueError(
                "Pickups can be booked up to 24 hours ahead — choose a time before "
                f"{latest.strftime('%Y-%m-%d %H:%M')} UTC."
            )
        return self


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    item_id: str
    pantry_id: str
    status: ReservationStatus
    reserved_at: datetime
    # Optional because reservations that predate scheduling keep NULL rather
    # than being back-dated with an invented time (see upgrade_schema.py).
    scheduled_pickup_at: Optional[datetime]
    hold_expires_at: datetime
    qr_code: Optional[str]
    picked_up_at: Optional[datetime]


class ReservationDetailOut(ReservationOut):
    """Reservation joined with the names both dashboards need to display,
    so neither has to fetch the whole item list to label a row."""
    item_name: Optional[str] = None
    item_category: Optional[str] = None
    item_sell_by_date: Optional[datetime] = None
    shelf_name: Optional[str] = None
    pantry_name: Optional[str] = None


# ---------- Donation records (FR-11.1) ----------

class DonationRecordOut(BaseModel):
    """The immutable snapshot written when a pickup is confirmed. There is
    deliberately no Create/Update schema — nothing ever writes to this table
    except crud.confirm_pickup, and nothing edits or deletes a row after."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    reservation_id: str
    item_name: str
    item_sku: Optional[str]
    item_category: Optional[str]
    sell_by_date_at_handoff: Optional[datetime]
    unit_value_at_handoff: Optional[Decimal]
    pantry_id: str
    pantry_name_at_handoff: str
    confirmed_by_user_id: str
    confirmed_at: datetime
