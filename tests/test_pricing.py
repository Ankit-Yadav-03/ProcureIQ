from services.pricing import run_full_analysis


def test_run_full_analysis_compares_prices_in_requirement_unit():
    analysis = run_full_analysis(
        current_price=50000,
        quantity=2,
        unit="ton",
        clean_responses=[
            {
                "vendor_id": 1,
                "vendor_name": "Vendor One",
                "normalized_price": 48.0,
                "delivery_days": 3,
                "confidence": "high",
            },
            {
                "vendor_id": 2,
                "vendor_name": "Vendor Two",
                "normalized_price": 49.0,
                "delivery_days": 2,
                "confidence": "medium",
            },
        ],
    )

    assert analysis["benchmark"]["min_price"] == 48000.0
    assert analysis["roi"]["best_price"] == 48000.0
    assert analysis["roi"]["total_savings"] == 4000.0
    assert analysis["roi"]["savings_pct"] == 4.0
    assert analysis["best_vendor_response"]["vendor_id"] == 1


def test_run_full_analysis_prefers_higher_numeric_confidence_on_tie():
    analysis = run_full_analysis(
        current_price=100,
        quantity=10,
        clean_responses=[
            {
                "vendor_id": 1,
                "vendor_name": "Low Confidence",
                "normalized_price": 95.0,
                "delivery_days": 2,
                "confidence": 0.3,
            },
            {
                "vendor_id": 2,
                "vendor_name": "High Confidence",
                "normalized_price": 95.0,
                "delivery_days": 2,
                "confidence": 0.9,
            },
        ],
    )

    assert analysis["best_vendor_response"]["vendor_id"] == 2
