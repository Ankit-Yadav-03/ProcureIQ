from __future__ import annotations

import re

from core.db import get_db
from core.schemas import ParsedRequirement
from core.logger import get_logger
from core.utils import strip_html, truncate_utf8
from services.llm_client import call_llm


logger = get_logger(__name__)
KNOWN_CITIES = [
    "delhi",
    "mumbai",
    "chennai",
    "bangalore",
    "hyderabad",
    "pune",
    "kolkata",
    "ahmedabad",
    "surat",
    "jaipur",
]
NUMERIC_ONLY_PATTERN = re.compile(r"[\d\s.,]+")
QUANTITY_PATTERN = re.compile(
    r"(?P<quantity>\d+\.?\d*)\s*(?P<unit>kg|ton|tonne|pcs|units|liters|ltr|mt|piece|meter)\b",
    re.IGNORECASE,
)
# Matches: ₹95, 95rs, 95 rupees, OR bare trailing number like "steel rod 5000kg delhi 100"
PRICE_PATTERN = re.compile(
    r"(?:₹\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:rs|rupees))",
    re.IGNORECASE,
)
PROMPT_INPUT_PATTERN = re.compile(r"INPUT_START\s*(.*?)\s*INPUT_END", re.DOTALL)
WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9/-]*")
# Matches standalone numbers NOT immediately followed by a unit word
_BARE_NUMBER_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)(?!\s*(?:kg|ton|tonne|pcs|units|liters|ltr|mt|piece|meter))\b",
    re.IGNORECASE,
)


def _extract_source_text(text: str) -> str:
    match = PROMPT_INPUT_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _to_float(value: str) -> float:
    numeric = re.sub(r"[^\d.]", "", value)
    return float(numeric) if numeric else 0.0


def _guess_item(text: str) -> str:
    tokens = WORD_PATTERN.findall(text.lower())
    filtered = [
        token
        for token in tokens
        if token not in KNOWN_CITIES
        and token not in {"kg", "ton", "tonne", "pcs", "units", "liters", "ltr", "mt", "rs", "rupees", "piece", "meter"}
    ]
    item_tokens = filtered[:3] or tokens[:3]
    return " ".join(item_tokens).strip() or "unknown item"


def _regex_parse(text: str) -> dict:
    source_text = truncate_utf8(strip_html(_extract_source_text(text)), 2000).strip()
    lowered = source_text.lower()

    quantity = 1.0
    unit = "units"
    quantity_match = QUANTITY_PATTERN.search(lowered)
    if quantity_match:
        quantity = float(quantity_match.group("quantity"))
        unit = quantity_match.group("unit").lower()

    # First try explicit currency patterns (₹95, 95rs, 95 rupees)
    price_match = PRICE_PATTERN.search(source_text)
    if price_match:
        # group(1) = ₹ format, group(2) = rs/rupees format
        raw = price_match.group(1) or price_match.group(2)
        current_price = _to_float(raw)
    else:
        # Fallback: find all standalone numbers not attached to a unit word,
        # exclude the quantity value, take the last one.
        # Covers bare formats like "steel rod 5000kg delhi 100"
        all_numbers = _BARE_NUMBER_PATTERN.findall(lowered)
        price_candidates = [float(n) for n in all_numbers if float(n) != quantity]
        current_price = price_candidates[-1] if price_candidates else 0.0

    is_valid = current_price > 0

    location = "unknown"
    for city in KNOWN_CITIES:
        if city in lowered:
            location = city
            break

    return {
        "item": _guess_item(source_text),
        "quantity": quantity,
        "unit": unit,
        "location": location,
        "current_price": current_price,
        "category": None,
        "is_valid": is_valid,
        "fallback_used": True,
    }


def _prepare_input(raw_input: str) -> str:
    cleaned = truncate_utf8(strip_html(raw_input), 2000).strip()
    if len(cleaned) < 10:
        raise ValueError("Input must be at least 10 characters long")
    if NUMERIC_ONLY_PATTERN.fullmatch(cleaned):
        raise ValueError("Input must contain descriptive text, not just numbers")
    return cleaned


def _normalize_requirement(data: dict) -> dict:
    return {
        "item": str(data.get("item", "")).strip().lower(),
        "quantity": data.get("quantity", 1.0),
        "unit": str(data.get("unit", "units")).strip().lower(),
        "location": str(data.get("location", "unknown")).strip().lower(),
        "current_price": data.get("current_price", 0.0),
        "category": (
            str(data["category"]).strip().lower()
            if data.get("category") not in (None, "")
            else None
        ),
    }


async def _parse_requirement(raw_input: str) -> ParsedRequirement:
    cleaned_input = _prepare_input(raw_input)
    prompt = f"""
Extract the procurement requirement and return only valid JSON with these keys:
"item", "quantity", "unit", "location", "current_price", "category".

Rules:
- Use lowercase strings for item, unit, and location.
- current_price must be the current supplier price per unit in INR.
- quantity must be numeric.
- category may be null if unknown.

INPUT_START
{cleaned_input}
INPUT_END
""".strip()

    parsed_data = await call_llm(prompt, fallback_fn=_regex_parse)
    if parsed_data is None:
        parsed_data = _regex_parse(prompt)

    if float(parsed_data.get("current_price", 0) or 0) == 0:
        raise ValueError("Could not extract price from requirement")

    requirement = ParsedRequirement.model_validate(_normalize_requirement(parsed_data))
    if parsed_data.get("fallback_used"):
        logger.info("Requirement parsing used regex fallback for input: %s", cleaned_input)
    return requirement


async def parse_and_store(raw_input: str) -> tuple[ParsedRequirement, int]:
    requirement = await _parse_requirement(raw_input)
    async with get_db(write=True) as db:
        await db.execute("BEGIN")
        try:
            cursor = await db.execute(
                """INSERT INTO requirements
                   (raw_input, item, quantity, location, current_price, unit, category, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'parsing_done')""",
                (
                    raw_input,
                    requirement.item,
                    requirement.quantity,
                    requirement.location,
                    requirement.current_price,
                    requirement.unit,
                    requirement.category,
                ),
            )
            await db.commit()
            requirement_id = cursor.lastrowid
        except Exception:
            await db.rollback()
            raise

    logger.info("Requirement stored with ID: %s", requirement_id)
    return requirement, requirement_id


async def get_requirement(requirement_id: int) -> dict | None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM requirements WHERE id = ?",
            (requirement_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
