"""
Pydantic schemas for request/response validation
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


PHONE_PATTERN = re.compile(r"^\+91[6-9]\d{9}$")


def _sanitize_utf8_text(value: Any, *, max_chars: int) -> Any:
    if value is None:
        return value

    if isinstance(value, bytes):
        cleaned = value.decode("utf-8", errors="ignore")
    else:
        cleaned = str(value).encode("utf-8", errors="ignore").decode("utf-8")

    return cleaned[:max_chars]


class ValidatedModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True)


class ProcurementRequest(BaseModel):
    input_text: str = Field(..., description="Raw procurement requirement text")


class ParsedRequirement(ValidatedModel):
    item: str
    quantity: float
    unit: str = "kg"
    location: str
    current_price: float
    category: Optional[str] = None

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("quantity must be greater than 0")
        return value

    @field_validator("current_price")
    @classmethod
    def validate_current_price(cls, value: float) -> float:
        if value < 0:
            raise ValueError("current_price must be non-negative")
        return value


class VendorBase(BaseModel):
    name: str
    phone: Optional[str] = None
    location: Optional[str] = None
    source: Optional[str] = None
    profile_url: Optional[str] = None
    rating: Optional[float] = None


class Vendor(ValidatedModel):
    name: str
    phone: str
    location: str
    rating: float | None = None
    source: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not PHONE_PATTERN.fullmatch(value):
            raise ValueError("phone must match +91XXXXXXXXXX and start with digits 6-9")
        return value


class VendorOut(VendorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    contact_status: str
    discovered_at: Optional[str] = None


class OutreachMessage(BaseModel):
    vendor_id: int
    requirement_id: int
    message_text: str
    channel: str = "whatsapp"
    batch_number: int = 1


class OutreachLogOut(BaseModel):
    id: int
    vendor_id: int
    requirement_id: int
    message_text: str
    channel: str
    sent_at: str
    status: str


class VendorResponse(ValidatedModel):
    vendor_id: int
    requirement_id: int
    raw_message: str
    received_at: datetime

    @field_validator("raw_message", mode="before")
    @classmethod
    def clean_raw_message(cls, value: Any) -> Any:
        return _sanitize_utf8_text(value, max_chars=1000)


class VendorResponseInput(BaseModel):
    vendor_id: int
    requirement_id: int
    raw_message: str


class VendorResponseOut(BaseModel):
    id: int
    vendor_id: int
    requirement_id: int
    raw_message: str
    price: Optional[float] = None
    delivery_days: Optional[int] = None
    gst_included: bool = False
    payment_terms: Optional[str] = None
    confidence: str = "medium"
    is_valid: bool = True


class ExtractedPrice(ValidatedModel):
    vendor_id: int
    price_per_unit: float
    unit: str
    delivery_days: int | None = None
    gst_included: bool
    payment_terms: str | None = None
    confidence: float
    is_valid: bool

    @field_validator("price_per_unit")
    @classmethod
    def validate_price_per_unit(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("price_per_unit must be greater than 0")
        return value

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value


class BenchmarkResult(BaseModel):
    min_price: float
    avg_price: float
    median_price: float
    response_count: int


class ROIResult(BaseModel):
    current_price: float
    best_price: float
    savings_per_unit: float
    total_savings: float
    savings_pct: float
    quantity: float


class PricingResult(ValidatedModel):
    requirement_id: int
    min_price: float
    avg_price: float
    median_price: float
    best_vendor_id: int
    savings_per_unit: float
    savings_total: float
    confidence_score: float
    status: Literal["complete", "partial", "insufficient_data"]
    response_count: int


class ProcurementResult(BaseModel):
    requirement_id: int
    item: str
    quantity: float
    location: str
    current_price: float
    best_price: Optional[float] = None
    best_vendor: Optional[VendorOut] = None
    avg_price: Optional[float] = None
    min_price: Optional[float] = None
    median_price: Optional[float] = None
    total_savings: Optional[float] = None
    savings_pct: Optional[float] = None
    vendor_count: int = 0
    response_count: int = 0
    confidence: str = "low"
    vendors: List[VendorOut] = []
    responses: List[VendorResponseOut] = []
    status: str = "in_progress"
