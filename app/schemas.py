"""
Pydantic schemas — these define the JSON shape of the API, separate from
the SQLAlchemy models in models.py. Keeping them separate means we can
change the DB schema without automatically changing what the app/OCR
pipeline sends and receives, and vice versa.
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, ConfigDict

from .models import ItemStatus, ReservationStatus


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
    batch_id: Optional[str] = None
    category: Optional[str] = None
    sell_by_date: Optional[datetime] = None
    arrival_date: Optional[datetime] = None
    shelf_id: Optional[str] = None
    image_url: Optional[str] = None


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
    batch_id: Optional[str]
    category: Optional[str]
    sell_by_date: Optional[datetime]
    arrival_date: Optional[datetime]
    shelf_id: Optional[str]
    status: ItemStatus
    ocr_raw_text: Optional[str]
    ocr_confidence: Optional[float]
    sku_match_confirmed: bool
    image_url: Optional[str]
    created_at: datetime
    updated_at: datetime


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

class ReservationCreate(BaseModel):
    item_id: str
    pantry_id: str
    hold_minutes: int = 180  # default 3-hour holding window, per Section 13.3


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    item_id: str
    pantry_id: str
    status: ReservationStatus
    reserved_at: datetime
    hold_expires_at: datetime
    qr_code: Optional[str]
    picked_up_at: Optional[datetime]
