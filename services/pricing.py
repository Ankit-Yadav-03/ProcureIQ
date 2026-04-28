"""
Benchmark Engine + ROI Calculator — Steps 8 & 9
Computes market statistics and calculates actual savings vs current supplier.
"""

import logging
import statistics
from typing import List, Optional

from services.cleaner import convert_from_base_unit

logger = logging.getLogger(__name__)


def _confidence_rank(value: object) -> int:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric >= 0.8:
            return 3
        if numeric >= 0.5:
            return 2
        return 1

    normalized = str(value or "").strip().lower()
    return {"high": 3, "medium": 2, "low": 1}.get(normalized, 1)


def compute_benchmark(prices: List[float]) -> dict:
    """
    Compute market price statistics from clean vendor prices.
    
    Returns:
        min_price, max_price, avg_price, median_price, std_dev, response_count
    """
    if not prices:
        raise ValueError("Cannot benchmark with zero prices. No valid vendor responses.")

    return {
        "min_price": round(min(prices), 2),
        "max_price": round(max(prices), 2),
        "avg_price": round(statistics.mean(prices), 2),
        "median_price": round(statistics.median(prices), 2),
        "std_dev": round(statistics.stdev(prices), 2) if len(prices) > 1 else 0.0,
        "response_count": len(prices),
    }


def compute_roi(
    current_price: float,
    best_price: float,
    quantity: float,
) -> dict:
    """
    Calculate actual savings vs client's current supplier.
    
    Formula:
        Savings = (Current Price - Best Vendor Price) × Quantity
    
    Returns full ROI breakdown.
    """
    if current_price <= 0:
        return {
            "current_price": 0.0,
            "best_price": round(best_price, 2),
            "savings_per_unit": 0.0,
            "total_savings": 0.0,
            "savings_pct": 0.0,
            "quantity": quantity,
            "is_saving": False,
            "annual_savings_estimate": 0.0,
        }
    if best_price <= 0:
        raise ValueError("Best vendor price must be > 0")

    savings_per_unit = current_price - best_price
    total_savings = savings_per_unit * quantity
    savings_pct = (savings_per_unit / current_price) * 100

    return {
        "current_price": round(current_price, 2),
        "best_price": round(best_price, 2),
        "savings_per_unit": round(savings_per_unit, 2),
        "total_savings": round(total_savings, 2),
        "savings_pct": round(savings_pct, 2),
        "quantity": quantity,
        "is_saving": savings_per_unit > 0,
        "annual_savings_estimate": round(total_savings * 12, 2),  # assumes monthly order
    }


def determine_confidence(
    response_count: int,
    savings_pct: float,
    std_dev: float,
    avg_price: float,
) -> str:
    """
    Determine confidence level of the ROI claim.
    
    Factors:
    - Response count (more = better)
    - Price variance (low std dev = more consistent market)
    - Savings % (extreme outliers reduce confidence)
    """
    # Response count score
    if response_count >= 5:
        count_score = 2
    elif response_count >= 3:
        count_score = 1
    else:
        count_score = 0

    # Variance score (coefficient of variation)
    if avg_price > 0:
        cv = (std_dev / avg_price) * 100
        if cv < 10:
            variance_score = 2
        elif cv < 25:
            variance_score = 1
        else:
            variance_score = 0
    else:
        variance_score = 0

    # Savings sanity check (>30% savings is suspicious without many quotes)
    if savings_pct > 30 and response_count < 5:
        savings_score = -1  # penalty
    elif savings_pct > 0:
        savings_score = 1
    else:
        savings_score = 0

    total = count_score + variance_score + savings_score

    if total >= 4:
        return "high"
    elif total >= 2:
        return "medium"
    else:
        return "low"


def find_best_vendor(clean_responses: List[dict], requirement_unit: str = "kg") -> Optional[dict]:
    """
    Select best vendor based on:
    1. Lowest normalized price (primary)
    2. Fewer delivery days (tiebreaker)
    3. Higher confidence (tiebreaker)
    """
    if not clean_responses:
        return None

    def sort_key(r):
        return (
            convert_from_base_unit(
                r.get("normalized_price", float("inf")),
                requirement_unit,
            ),
            r.get("delivery_days") or 999,
            -_confidence_rank(r.get("confidence", "low")),
        )

    sorted_responses = sorted(clean_responses, key=sort_key)
    return sorted_responses[0]


def run_full_analysis(
    current_price: float,
    quantity: float,
    clean_responses: List[dict],
    unit: str = "kg",
) -> dict:
    """
    Run complete benchmark + ROI analysis on clean vendor responses.
    Returns full analysis dict ready for storage and API response.
    """
    if not clean_responses:
        return {
            "error": "No valid vendor responses to analyze",
            "response_count": 0,
            "confidence": "none",
        }

    comparable_responses = []
    for response in clean_responses:
        comparable_responses.append(
            {
                **response,
                "comparable_price": convert_from_base_unit(
                    response["normalized_price"],
                    unit,
                ),
            }
        )

    prices = [r["comparable_price"] for r in comparable_responses]
    benchmark = compute_benchmark(prices)
    best_vendor_response = find_best_vendor(comparable_responses, requirement_unit=unit)
    best_price = best_vendor_response["comparable_price"]

    roi = compute_roi(current_price, best_price, quantity)
    confidence = determine_confidence(
        response_count=benchmark["response_count"],
        savings_pct=roi["savings_pct"],
        std_dev=benchmark["std_dev"],
        avg_price=benchmark["avg_price"],
    )

    return {
        "benchmark": benchmark,
        "roi": roi,
        "best_vendor_response": best_vendor_response,
        "confidence": confidence,
        "analysis_summary": {
            "vendors_responded": benchmark["response_count"],
            "market_min": benchmark["min_price"],
            "market_avg": benchmark["avg_price"],
            "market_median": benchmark["median_price"],
            "best_price": best_price,
            "best_vendor_id": best_vendor_response.get("vendor_id"),
            "total_savings": roi["total_savings"],
            "savings_pct": roi["savings_pct"],
            "confidence": confidence,
        },
    }
