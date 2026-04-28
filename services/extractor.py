from __future__ import annotations

import re
from typing import List

from core.db import get_db
from core.schemas import ExtractedPrice
from core.logger import get_logger


logger = get_logger(__name__)
PRICE_PATTERN = re.compile(
    r"(?:₹\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*(?:rs|rupees|/-)?)",
    re.IGNORECASE,
)
DELIVERY_PATTERN = re.compile(
    r"(\d+)\s*(?:working\s+days?|days?)\b(?=[^.]{0,40}\b(?:delivery|dispatch)\b)",
    re.IGNORECASE,
)
UNIT_PATTERN = re.compile(
    r"\b(kg|ton|pcs|units|liters|ltr|mt|piece|pieces|liter|tonne)\b",
    re.IGNORECASE,
)
GST_PATTERN = re.compile(
    r"(?:gst\s+included|with\s+gst|inclusive of gst|\+\s*gst)",
    re.IGNORECASE,
)
PAYMENT_TERMS_PATTERN = re.compile(
    r"(?:payment\s+terms?|terms?)\s*(?::|[-–])\s*([^,.\n]+)",
    re.IGNORECASE,
)
PROMPT_MESSAGE_PATTERN = re.compile(r"MESSAGE_START\s*(.*?)\s*MESSAGE_END", re.DOTALL)


def _extract_message_text(text: str) -> str:
    match = PROMPT_MESSAGE_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    numeric = re.sub(r"[^\d.]", "", value.replace(",", ""))
    return float(numeric) if numeric else None


def _coerce_confidence(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)

    normalized = str(value or "").strip().lower()
    if normalized == "high":
        return 0.9
    if normalized == "medium":
        return 0.6
    if normalized == "low":
        return 0.3
    return 0.0


def _normalize_unit(value: object) -> str:
    normalized = str(value or "unit").strip().lower()
    normalized = normalized.replace("per ", "").strip()
    normalized = re.sub(r"^(?:rs|inr)\s*/\s*", "", normalized)
    normalized = normalized.replace("/-", "").strip()

    compound_match = re.search(
        r"(kg|ton|pcs|units|liters|ltr|mt|piece|pieces|liter|tonne)\b",
        normalized,
    )
    if compound_match:
        normalized = compound_match.group(1)

    unit_map = {
        "piece": "pcs",
        "pieces": "pcs",
        "liter": "liters",
        "tonne": "ton",
    }
    return unit_map.get(normalized, normalized or "unit")


def _regex_extract(message: str) -> dict:
    source_message = _extract_message_text(message)
    lowered = source_message.lower()

    price_match = PRICE_PATTERN.search(source_message)
    price = _to_float(price_match.group(0)) if price_match else None

    delivery_match = DELIVERY_PATTERN.search(lowered)
    delivery_days = int(delivery_match.group(1)) if delivery_match else None

    unit_match = UNIT_PATTERN.search(lowered)
    unit = _normalize_unit(unit_match.group(1) if unit_match else "unit")

    # Better GST detection
    gst_included = bool(GST_PATTERN.search(source_message)) or "gst included" in lowered or "with gst" in lowered
    
    # Extract payment terms if present
    payment_match = PAYMENT_TERMS_PATTERN.search(source_message)
    payment_terms = payment_match.group(1).strip() if payment_match else None

    # Improved confidence scoring for regex extraction
    confidence = 0.5 if price else 0.0
    if unit_match:
        confidence += 0.1
    if delivery_match:
        confidence += 0.1
    if payment_terms:
        confidence += 0.05
    confidence = min(0.8, confidence)  # Cap at 0.8 for regex-only extraction

    return {
        "price": price,
        "unit": unit,
        "delivery_days": delivery_days,
        "gst_included": gst_included,
        "payment_terms": payment_terms,
        "confidence": confidence,
        "is_valid": price is not None,
    }


def _normalize_extraction(data: dict) -> dict:
    price_per_unit = _to_float(str(data.get("price_per_unit"))) if data.get("price_per_unit") is not None else None
    if price_per_unit is None:
        price_per_unit = _to_float(str(data.get("price"))) if data.get("price") is not None else None

    normalized = {
        "vendor_id": int(data["vendor_id"]),
        "price_per_unit": price_per_unit,
        "unit": _normalize_unit(data.get("unit")),
        "delivery_days": int(data["delivery_days"]) if data.get("delivery_days") is not None else None,
        "gst_included": bool(data.get("gst_included", False)),
        "payment_terms": (
            str(data["payment_terms"]).strip() if data.get("payment_terms") not in (None, "") else None
        ),
        "confidence": _coerce_confidence(data.get("confidence")),
        "is_valid": bool(data.get("is_valid", price_per_unit is not None)),
    }

    if normalized["confidence"] < 0.5:
        normalized["is_valid"] = False
        logger.warning(
            "Marking extraction invalid for vendor %s because confidence %.2f is below threshold",
            normalized["vendor_id"],
            normalized["confidence"],
        )

    if normalized["price_per_unit"] is None:
        raise ValueError("Could not extract valid price from response")

    return normalized


async def _extract_single_response(vendor_id: int, item: str, raw_message: str) -> ExtractedPrice:
    """
    Extract vendor pricing information from response message.
    Uses efficient regex-based extraction.
    
    Confidence scoring:
    - 0.5 base: price found
    - +0.1: unit found
    - +0.1: delivery days found
    - +0.05: payment terms found
    - Max: 0.8 (for regex-only extraction)
    """
    extracted = _regex_extract(raw_message)
    extracted["vendor_id"] = vendor_id
    
    normalized = _normalize_extraction(extracted)
    return ExtractedPrice.model_validate(normalized)


async def extract_all_prices(requirement_id: int, item: str) -> List[dict]:
    extraction_results: List[dict] = []
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT * FROM vendor_responses
               WHERE requirement_id = ? AND price IS NULL AND is_valid = 1""",
            (requirement_id,),
        )
        raw_responses = await cursor.fetchall()

    if not raw_responses:
        logger.info("No unextracted responses for requirement %s", requirement_id)
        return []

    for row in raw_responses:
        response_id = row["id"]
        raw_message = row["raw_message"]

        try:
            extracted = await _extract_single_response(row["vendor_id"], item, raw_message)
            async with get_db(write=True) as db:
                await db.execute("BEGIN")
                try:
                    await db.execute(
                        """UPDATE vendor_responses SET
                           price = ?, delivery_days = ?, gst_included = ?,
                           payment_terms = ?, confidence = ?, is_valid = ?,
                           extracted_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (
                            extracted.price_per_unit,
                            extracted.delivery_days,
                            1 if extracted.gst_included else 0,
                            extracted.payment_terms,
                            extracted.confidence,
                            1 if extracted.is_valid else 0,
                            response_id,
                        ),
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

            extraction_results.append(
                {
                    "response_id": response_id,
                    "vendor_id": extracted.vendor_id,
                    "price": extracted.price_per_unit,
                    "unit": extracted.unit,
                    "delivery_days": extracted.delivery_days,
                    "gst_included": extracted.gst_included,
                    "confidence": extracted.confidence,
                    "is_valid": extracted.is_valid,
                }
            )
        except Exception as exc:
            logger.error("Extraction failed for response %s: %s", response_id, exc)
            async with get_db(write=True) as db:
                await db.execute("BEGIN")
                try:
                    await db.execute(
                        """UPDATE vendor_responses SET
                           is_valid = 0,
                           confidence = ?,
                           extracted_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (0.0, response_id),
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

            extraction_results.append(
                {
                    "response_id": response_id,
                    "vendor_id": row["vendor_id"],
                    "price": None,
                    "is_valid": False,
                    "error": str(exc),
                }
            )

    logger.info("Extraction complete: %s responses processed", len(extraction_results))
    return extraction_results


async def get_extracted_prices(requirement_id: int) -> List[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT r.*, v.name as vendor_name, v.location as vendor_location
               FROM vendor_responses r
               JOIN vendors v ON r.vendor_id = v.id
               WHERE r.requirement_id = ? AND r.is_valid = 1 AND r.price IS NOT NULL
               ORDER BY r.price ASC""",
            (requirement_id,),
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            record = dict(row)
            try:
                record["confidence"] = float(record["confidence"]) if record.get("confidence") is not None else None
            except (TypeError, ValueError):
                pass
            record["gst_included"] = bool(record.get("gst_included", 0))
            results.append(record)
        return results
