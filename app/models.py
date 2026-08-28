"""
Core schema: Shelf, Item, Pantry, Reservation.

This is the Week 1 deliverable from the build checklist — everything else
(OCR pipeline, reservation logic, app screens) reads from and writes to
these four tables, so changes here should be made deliberately and
communicated to the whole team.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, DateTime, ForeignKey, Enum as SAEnum, Boolean, Text
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


class Item(Base):
    __tablename__ = "items"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    sku = Column(String, nullable=True, index=True)      # null until OCR/DB match resolves it
    name = Column(String, nullable=False)
    batch_id = Column(String, nullable=True)
    category = Column(String, nullable=True)

    sell_by_date = Column(DateTime, nullable=True)
    arrival_date = Column(DateTime, nullable=True)

    shelf_id = Column(UUID(as_uuid=False), ForeignKey("shelves.id"), nullable=True)
    shelf = relationship("Shelf", back_populates="items")

    status = Column(SAEnum(ItemStatus), default=ItemStatus.IN_STOCK, nullable=False)

    # OCR / CV confidence pipeline (Section 9.3 of the proposal)
    ocr_raw_text = Column(Text, nullable=True)
    ocr_confidence = Column(Float, nullable=True)      # 0.0 - 1.0
    sku_match_confirmed = Column(Boolean, default=False)

    image_url = Column(String, nullable=True)  # pointer to stored frame, not raw footage

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    reservations = relationship("Reservation", back_populates="item")


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


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    item_id = Column(UUID(as_uuid=False), ForeignKey("items.id"), nullable=False)
    pantry_id = Column(UUID(as_uuid=False), ForeignKey("pantries.id"), nullable=False)

    status = Column(SAEnum(ReservationStatus), default=ReservationStatus.PENDING, nullable=False)

    reserved_at = Column(DateTime, default=datetime.utcnow)
    hold_expires_at = Column(DateTime, nullable=False)   # reserved_at + holding window (e.g. 3h)

    qr_code = Column(String, nullable=True, unique=True)
    picked_up_at = Column(DateTime, nullable=True)

    item = relationship("Item", back_populates="reservations")
    pantry = relationship("Pantry", back_populates="reservations")
