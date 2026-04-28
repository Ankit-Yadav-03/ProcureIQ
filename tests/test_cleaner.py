from core.schemas import PricingResult
from services.cleaner import clean_responses


def test_outlier_removal():
    responses = [
        {"vendor_id": 1, "price": 100, "unit": "kg", "gst_included": False},
        {"vendor_id": 2, "price": 102, "unit": "kg", "gst_included": False},
        {"vendor_id": 3, "price": 98, "unit": "kg", "gst_included": False},
        {"vendor_id": 4, "price": 101, "unit": "kg", "gst_included": False},
        {"vendor_id": 5, "price": 5000, "unit": "kg", "gst_included": False},
    ]

    clean, rejected = clean_responses(responses)

    assert len(clean) == 4
    assert {row["vendor_id"] for row in clean} == {1, 2, 3, 4}
    assert len(rejected) == 1
    assert rejected[0]["vendor_id"] == 5
    assert rejected[0]["rejection_reason"] == "outlier_price"


def test_all_outliers():
    responses = [
        {"vendor_id": 1, "price": 0, "unit": "kg", "gst_included": False},
        {"vendor_id": 2, "price": -10, "unit": "kg", "gst_included": False},
        {"vendor_id": 3, "price": None, "unit": "kg", "gst_included": False},
    ]

    clean, rejected = clean_responses(responses)
    status = "insufficient_data" if not clean else "complete"

    result = PricingResult(
        requirement_id=1,
        min_price=0,
        avg_price=0,
        median_price=0,
        best_vendor_id=0,
        savings_per_unit=0,
        savings_total=0,
        confidence_score=0,
        status=status,
        response_count=len(clean),
    )

    assert clean == []
    assert len(rejected) == 3
    assert result.status == "insufficient_data"


def test_unit_normalization():
    responses = [
        {"vendor_id": 1, "price": 0.001, "unit": "gram", "gst_included": False},
    ]

    clean, rejected = clean_responses(responses)

    assert rejected == []
    assert len(clean) == 1
    assert clean[0]["normalized_price"] == 1.0


def test_numeric_low_confidence_is_flagged():
    responses = [
        {
            "vendor_id": 1,
            "price": 100,
            "unit": "kg",
            "gst_included": False,
            "confidence": 0.3,
        },
    ]

    clean, rejected = clean_responses(responses)

    assert rejected == []
    assert clean[0]["flagged"] is True
