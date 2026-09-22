"""
Core schema: Shelf, Item, Pantry, Reservation, plus the intake pipeline
(Product, ExpirationRule, IntakeScan).

Everything else (OCR pipeline, reservation logic, app screens) reads from
and writes to these tables, so changes here should be made deliberately and
communicated to the whole team.

Intake flow, in the order the tables are used:

    UPC scan ─▶ Product (catalog lookup)
                   │
    label photo ─▶ IntakeScan (OCR date read, held pending)
                   │
    staff confirm ─▶ Item (dates + deadlines resolved by ExpirationRule)
                   │
    scheduler ────▶ Item.status advances automatically as deadlines pass
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, ForeignKey, Enum as SAEnum,
    Boolean, Text, JSON, Numeric
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .database import Base


def gen_uuid():
    return str(uuid.uuid4())


class ItemStatus(str, enum.Enum):
    IN_STOCK = "in_stock"           # normal shelf item, before sell-by
    NEAR_EXPIRY = "near_expiry"     # flagged by AI as approaching sell-by date
    NEEDS_REVIEW = "needs_review"   # OCR/CV confidence < threshold, waiting on employee
    AVAILABLE = "available"         # past sell-by, safe, uploaded to donation network
    RESERVED = "reserved"           # a pantry has reserved it
    PICKED_UP = "picked_up"         # donation completed
    EXPIRED_HOLD = "expired_hold"   # reservation hold window lapsed, food not picked up
    DISCARDED = "discarded"         # removed, not usable


class ReservationStatus(str, enum.Enum):
    PENDING = "pending"
    PICKED_UP = "picked_up"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class DateLabelType(str, enum.Enum):
    """
    What the printed date on the package actually means. The distinction is
    not cosmetic: a sell-by date is a retail rotation deadline and food is
    usually fine past it (that is the entire premise of the product), while
    a use-by date is a safety limit that must never be crossed on a donated
    item (NFR-4.8.2).
    """
    SELL_BY = "sell_by"
    USE_BY = "use_by"
    BEST_BY = "best_by"
    PACKED_ON = "packed_on"
    UNKNOWN = "unknown"


class DateSource(str, enum.Enum):
    """Where an item's dates came from, so a review decision stays auditable."""
    OCR = "ocr"                  # read off the label and accepted by a human
    MANUAL = "manual"            # a person typed it
    SHELF_LIFE = "shelf_life"    # estimated from the catalog's shelf life
    UNKNOWN = "unknown"


class ScanStatus(str, enum.Enum):
    PENDING = "pending"        # waiting on the staff confirmation step
    CONFIRMED = "confirmed"    # a person accepted it and an Item now exists
    REJECTED = "rejected"      # a person threw it out at the dock


class UserRole(str, enum.Enum):
    """
    FR-2.1: every user holds exactly one role. STAFF and MANAGER are store
    roles; ORG_COORDINATOR is the pantry-side "Organizer" and is the only
    role that carries a pantry_id (FR-2.2).
    """
    STAFF = "staff"
    MANAGER = "manager"
    ADMIN = "admin"
    ORG_COORDINATOR = "org_coordinator"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=True)

    role = Column(SAEnum(UserRole), nullable=False)

    # Set for ORG_COORDINATOR only. Store roles leave this null (FR-2.2).
    # This is the single source of truth for which org a coordinator acts
    # as — never a client-supplied value (FR-2.3).
    pantry_id = Column(UUID(as_uuid=False), ForeignKey("pantries.id"), nullable=True)
    pantry = relationship("Pantry", back_populates="users")

    is_active = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Shelf(Base):
    __tablename__ = "shelves"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)          # e.g. "Shelf A - Dairy"
    location = Column(String, nullable=True)        # store / aisle description
    camera_id = Column(String, nullable=True)
    current_temperature_c = Column(Float, nullable=True)
    current_humidity_pct = Column(Float, nullable=True)
    last_reading_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("Item", back_populates="shelf")


class Product(Base):
    """
    The UPC catalog — one row per barcode the store handles.

    A UPC scan resolves to a row here, which is what makes intake fast:
    the scanner supplies identity (name, brand, category) so the person at
    the dock only has to confirm the one thing a barcode cannot carry, the
    date. Barcodes encode *which product this is*, never *how old this
    particular unit is*.

    Rows are created by the catalog import, by an optional external lookup
    (app/upc.py), or by staff the first time an unknown code is scanned.
    """
    __tablename__ = "products"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)

    # Normalized to 13 digits (GTIN-13) so a UPC-A read and the same code
    # read as an EAN-13 land on one row. See app/upc.py:normalize.
    upc = Column(String(14), nullable=False, unique=True, index=True)

    name = Column(String, nullable=False)
    brand = Column(String, nullable=True)
    category = Column(String, nullable=True, index=True)
    sku = Column(String, nullable=True, index=True)

    # Used when a unit arrives with no legible printed date: the estimated
    # date is arrival + this many days (FR-5.6 fallback).
    default_shelf_life_days = Column(Integer, nullable=True)

    # What kind of date this product's packaging carries, used to interpret
    # a bare date with no keyword next to it.
    date_label_type = Column(
        SAEnum(DateLabelType), default=DateLabelType.SELL_BY, nullable=False
    )

    # NFR-4.8.6: infant formula and similar categories are never published
    # automatically, no matter how clean the read was.
    donation_restricted = Column(Boolean, default=False, nullable=False)

    # Per-unit value used to value a donated unit of this product for tax
    # records (FR-11.1). Nullable: a product with no value set simply
    # produces items with no value, rather than blocking intake.
    unit_value = Column(Numeric(10, 2), nullable=True)

    source = Column(String, nullable=True)   # "catalog" | "external" | "staff"

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ExpirationRule(Base):
    """
    The automatic expiration policy, one row per category.

    These four numbers are what drive every unattended status change. They
    live in a table rather than in code so a manager can tune dairy without
    a deploy, and so the reasoning behind a given transition can be read
    back afterwards.

    The category "*" is the fallback used for anything with no rule of its
    own; app/expiration.py seeds it on startup so the sweep always has
    something to apply.
    """
    __tablename__ = "expiration_rules"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    category = Column(String, nullable=False, unique=True, index=True)

    # IN_STOCK -> NEAR_EXPIRY this many hours before the sell-by date.
    near_expiry_hours = Column(Integer, default=48, nullable=False)

    # NEAR_EXPIRY -> AVAILABLE this many hours after the sell-by date.
    # Negative publishes early; 0 publishes the moment the date passes.
    publish_offset_hours = Column(Integer, default=0, nullable=False)

    # The safety margin past the sell-by date, used only when the package
    # carries no use-by date. A use-by date always wins over this.
    discard_after_hours = Column(Integer, default=48, nullable=False)

    # False means the category still reaches NEAR_EXPIRY on its own but a
    # person has to press publish — for anything needing a judgment call.
    auto_publish = Column(Boolean, default=True, nullable=False)

    # Fallback for products with no default_shelf_life_days of their own.
    default_shelf_life_days = Column(Integer, nullable=True)

    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Item(Base):
    __tablename__ = "items"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    sku = Column(String, nullable=True, index=True)      # null until OCR/DB match resolves it
    name = Column(String, nullable=False)
    batch_id = Column(String, nullable=True)
    category = Column(String, nullable=True, index=True)

    # Set when the unit came in through the UPC scanner. Both are kept: the
    # FK gives the catalog record as it is now, the raw code survives the
    # product row being edited or deleted.
    upc = Column(String(14), nullable=True, index=True)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id"), nullable=True)
    product = relationship("Product")

    sell_by_date = Column(DateTime, nullable=True, index=True)
    # NFR-4.8.2: a hard safety limit, distinct from the retail date. When
    # present it caps discard_after — nothing is donated past it.
    use_by_date = Column(DateTime, nullable=True)
    arrival_date = Column(DateTime, nullable=True)

    date_label_type = Column(
        SAEnum(DateLabelType), default=DateLabelType.UNKNOWN, nullable=False
    )
    date_source = Column(SAEnum(DateSource), default=DateSource.UNKNOWN, nullable=False)

    # Deadlines written by app/expiration.py when the dates are set, so the
    # background sweep is one indexed comparison instead of a rule join per
    # row, and so staff can see exactly when each transition will fire.
    donate_after = Column(DateTime, nullable=True, index=True)
    discard_after = Column(DateTime, nullable=True, index=True)

    # Inherited from Product.unit_value at intake (same "catalog fills gaps"
    # rule as category/sku above) — the value this unit is worth when it's
    # eventually donated (FR-11.1).
    unit_value = Column(Numeric(10, 2), nullable=True)

    shelf_id = Column(UUID(as_uuid=False), ForeignKey("shelves.id"), nullable=True)
    shelf = relationship("Shelf", back_populates="items")

    status = Column(SAEnum(ItemStatus), default=ItemStatus.IN_STOCK, nullable=False, index=True)

    # OCR / CV confidence pipeline (Section 9.3 of the proposal)
    ocr_raw_text = Column(Text, nullable=True)
    ocr_confidence = Column(Float, nullable=True)      # 0.0 - 1.0
    sku_match_confirmed = Column(Boolean, default=False)

    image_url = Column(String, nullable=True)  # pointer to stored frame, not raw footage

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    reservations = relationship("Reservation", back_populates="item")


class IntakeScan(Base):
    """
    One pass of a unit across the intake station, held until a person
    confirms it.

    This is deliberately not an Item. Nothing a scanner or an OCR engine
    produces enters inventory on its own — the row sits here, showing what
    the barcode resolved to and what the camera thought the date said, and
    becomes an Item only when someone presses confirm. A rejected scan
    stays as a record of what was thrown out and why.

    It doubles as the OCR accuracy log FR-5.10 asks for: every read is
    here with its confidence, what the machine proposed, and what the
    person actually accepted.
    """
    __tablename__ = "intake_scans"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)

    # ---- UPC scanner ----
    upc = Column(String(14), nullable=True, index=True)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id"), nullable=True)
    product = relationship("Product")
    # Filled in by staff when the barcode resolved to nothing, or when they
    # override what the catalog said.
    name_override = Column(String, nullable=True)
    category_override = Column(String, nullable=True)

    # ---- OCR date scanner ----
    ocr_raw_text = Column(Text, nullable=True)
    ocr_confidence = Column(Float, nullable=True)        # mean word confidence
    date_confidence = Column(Float, nullable=True)       # min confidence over the date tokens (FR-5.8)
    detected_date = Column(DateTime, nullable=True)      # the candidate the parser chose
    detected_label_type = Column(
        SAEnum(DateLabelType), default=DateLabelType.UNKNOWN, nullable=False
    )
    # Every candidate the parser saw, kept so a reviewer can pick a
    # different one and so ambiguous labels can be studied later.
    date_candidates = Column(JSON, nullable=True)
    image_url = Column(String, nullable=True)

    # ---- Manual confirmation ----
    status = Column(SAEnum(ScanStatus), default=ScanStatus.PENDING, nullable=False, index=True)
    shelf_id = Column(UUID(as_uuid=False), ForeignKey("shelves.id"), nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    batch_id = Column(String, nullable=True)
    notes = Column(String, nullable=True)

    scanned_by_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    confirmed_by_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    # The date the person actually accepted, which is what the Item gets.
    # Differs from detected_date exactly when OCR was wrong — the gap
    # between the two columns is the pipeline's error rate.
    confirmed_date = Column(DateTime, nullable=True)

    item_id = Column(UUID(as_uuid=False), ForeignKey("items.id"), nullable=True)
    item = relationship("Item")

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Pantry(Base):
    __tablename__ = "pantries"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    org_name = Column(String, nullable=False)
    ein = Column(String, nullable=False)
    address = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    contact_email = Column(String, nullable=False, unique=True)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    reservations = relationship("Reservation", back_populates="pantry")
    users = relationship("User", back_populates="pantry")


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    item_id = Column(UUID(as_uuid=False), ForeignKey("items.id"), nullable=False)
    pantry_id = Column(UUID(as_uuid=False), ForeignKey("pantries.id"), nullable=False)

    # Indexed because the expiry sweep filters on both columns every minute
    # (NFR-4.6.3).
    status = Column(SAEnum(ReservationStatus), default=ReservationStatus.PENDING, nullable=False, index=True)

    reserved_at = Column(DateTime, default=datetime.utcnow)

    # When the organization said it would arrive (FR-8.6). Nullable at the
    # database layer even though the API requires it: SQLite cannot add a
    # NOT NULL column to an existing table without a default, and
    # reservations that reached a terminal state before this column existed
    # are deliberately left NULL rather than back-dated with a time nobody
    # ever promised. Do not tighten this without reading
    # scripts/upgrade_schema.py first.
    scheduled_pickup_at = Column(DateTime, nullable=True, index=True)

    hold_expires_at = Column(DateTime, nullable=False, index=True)   # scheduled_pickup_at + PICKUP_GRACE

    qr_code = Column(String, nullable=True, unique=True)
    picked_up_at = Column(DateTime, nullable=True)

    item = relationship("Item", back_populates="reservations")
    pantry = relationship("Pantry", back_populates="reservations")


class DonationRecord(Base):
    """
    The immutable audit trail FR-11.1 asks for: one row per completed
    donation, written the instant a pickup is confirmed
    (crud.confirm_pickup) and never updated or deleted afterward.

    Fields are snapshotted at handoff time rather than joined live, on
    purpose — an Item's name, category or value can be edited later (a
    catalog correction, a price change), and a Pantry's name can change,
    but what this record certifies is what was true the moment the
    donation happened. That's also what Good Samaritan Act documentation
    (NFR-4.8.1) and a tax record both need: a fact that doesn't move.
    """
    __tablename__ = "donation_records"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)

    reservation_id = Column(
        UUID(as_uuid=False), ForeignKey("reservations.id"), nullable=False, unique=True
    )
    reservation = relationship("Reservation")

    item_name = Column(String, nullable=False)
    item_sku = Column(String, nullable=True)
    item_category = Column(String, nullable=True)
    sell_by_date_at_handoff = Column(DateTime, nullable=True)

    # The item's unit_value at the moment of handoff — what this donation
    # is worth for tax purposes. Nullable: items with no catalog value
    # produce a record with no value, rather than blocking the donation.
    unit_value_at_handoff = Column(Numeric(10, 2), nullable=True)

    pantry_id = Column(UUID(as_uuid=False), ForeignKey("pantries.id"), nullable=False, index=True)
    pantry_name_at_handoff = Column(String, nullable=False)

    confirmed_by_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    confirmed_at = Column(DateTime, nullable=False, index=True)
