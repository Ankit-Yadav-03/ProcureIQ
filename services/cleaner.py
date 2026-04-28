"""
Data Cleaner — Step 7 of pipeline
Removes outlier prices, normalizes units, validates response integrity.
"""

import logging
import statistics
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Price is outlier if it deviates more than this factor from median
OUTLIER_THRESHOLD = 2.5
UNIT_CONVERSIONS = {
    "ton": 1 / 1000,
    "tonne": 1 / 1000,
    "quintal": 1 / 100,
    "gram": 1000,
    "kg": 1,
    "piece": 1,
    "pcs": 1,
    "liter": 1,
    "meter": 1,
    "box": 1,
}


def _is_low_confidence(value: object) -> bool:
    if isinstance(value, (int, float)):
        return float(value) <= 0.3
    return str(value or "").strip().lower() == "low"


def remove_outliers(prices: List[float]) -> Tuple[List[float], List[float]]:
    """
    Remove statistical outliers using IQR + median deviation method.
    Returns (clean_prices, removed_prices).
    
    Uses IQR method for small samples (< 10), 
    median absolute deviation for larger ones.
    """
    if len(prices) <= 2:
        return prices, []

    if len(prices) < 10:
        # IQR method for small samples
        sorted_p = sorted(prices)
        q1 = statistics.median(sorted_p[: len(sorted_p) // 2])
        q3 = statistics.median(sorted_p[len(sorted_p) // 2 :])
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
    else:
        # MAD method for larger samples
        med = statistics.median(prices)
        deviations = [abs(p - med) for p in prices]
        mad = statistics.median(deviations)
        lower = med - OUTLIER_THRESHOLD * mad
        upper = med + OUTLIER_THRESHOLD * mad

    clean = [p for p in prices if lower <= p <= upper]
    removed = [p for p in prices if p < lower or p > upper]

    if removed:
        logger.info(f"Removed outlier prices: {removed} (bounds: {lower:.2f}–{upper:.2f})")

    return clean, removed


def normalize_to_base_unit(price: float, unit: str, gst_included: bool) -> float:
    """
    Normalize price to base unit (per kg/piece etc).
    Strips GST if included (assume 18% GST unless otherwise).
    """
    # Unit conversions to base (all in kg equivalent for weight items)
    unit_conversions = {
        "ton": 1 / 1000,    # ₹/ton → ₹/kg
        "tonne": 1 / 1000,
        "quintal": 1 / 100,
        "gram": 1000,        # ₹/gram → ₹/kg
        "kg": 1,
        "piece": 1,
        "pcs": 1,
        "liter": 1,
        "meter": 1,
        "box": 1,
    }

    conversion = unit_conversions.get(unit.lower(), 1)
    normalized = price * conversion

    # Strip GST if included (18% standard)
    if gst_included:
        normalized = normalized / 1.18

    return round(normalized, 4)


def convert_from_base_unit(price: float, unit: str) -> float:
    """Convert a base-unit price into the requested unit."""
    conversion = UNIT_CONVERSIONS.get(unit.lower(), 1)
    if conversion == 0:
        return round(price, 4)
    return round(price / conversion, 4)


def clean_responses(responses: List[dict]) -> Tuple[List[dict], List[dict]]:
    """
    Full cleaning pipeline for extracted vendor responses.
    
    Steps:
    1. Remove null/zero prices
    2. Normalize prices to base unit
    3. Remove outliers
    4. Flag low-confidence extractions
    
    Returns (valid_responses, rejected_responses)
    """
    valid = []
    rejected = []

    # Step 1: Remove null/zero prices
    for r in responses:
        price = r.get("price")
        if not price or price <= 0:
            r["rejection_reason"] = "null_or_zero_price"
            rejected.append(r)
            continue

        # Step 2: Normalize
        unit = r.get("unit", "kg")
        gst_included = bool(r.get("gst_included", False))
        r["normalized_price"] = normalize_to_base_unit(price, unit, gst_included)
        valid.append(r)

    if not valid:
        return [], rejected

    # Step 3: Outlier removal on normalized prices
    normalized_prices = [r["normalized_price"] for r in valid]
    clean_prices, removed_prices = remove_outliers(normalized_prices)

    final_valid = []
    for r in valid:
        if r["normalized_price"] in clean_prices:
            final_valid.append(r)
        else:
            r["rejection_reason"] = "outlier_price"
            rejected.append(r)

    # Step 4: Flag low confidence (don't remove, just flag)
    for r in final_valid:
        if _is_low_confidence(r.get("confidence")):
            r["flagged"] = True
            logger.warning(
                f"Low-confidence extraction for vendor {r.get('vendor_id')}: ₹{r['normalized_price']}"
            )

    logger.info(
        f"Cleaning complete: {len(final_valid)} valid, {len(rejected)} rejected"
    )
    return final_valid, rejected
