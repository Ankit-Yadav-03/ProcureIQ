import pytest
from pydantic import ValidationError

from core.schemas import ExtractedPrice, Vendor
from core.utils import normalize_phone


def test_invalid_phone():
    with pytest.raises(ValidationError):
        Vendor(
            name="Vendor One",
            phone="12345",
            location="delhi",
            source="manual",
        )


@pytest.mark.parametrize(
    "raw_phone",
    ["+919876543210", "9876543210", "919876543210"],
)
def test_valid_phone_formats(raw_phone):
    normalized = normalize_phone(raw_phone)

    vendor = Vendor(
        name="Vendor One",
        phone=normalized,
        location="delhi",
        source="manual",
    )

    assert normalized == "+919876543210"
    assert vendor.phone == "+919876543210"


def test_negative_price():
    with pytest.raises(ValidationError):
        ExtractedPrice(
            vendor_id=1,
            price_per_unit=-100,
            unit="kg",
            delivery_days=2,
            gst_included=False,
            payment_terms="Net 30",
            confidence=0.8,
            is_valid=True,
        )


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        ExtractedPrice(
            vendor_id=1,
            price_per_unit=100,
            unit="kg",
            delivery_days=2,
            gst_included=False,
            payment_terms="Net 30",
            confidence=1.5,
            is_valid=True,
        )
