from __future__ import annotations

import json
import os
import re
from typing import Callable

from google import genai

from core.config import settings
from core.logger import get_logger


logger = get_logger(__name__)
_JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


class LLMUnavailableError(RuntimeError):
    pass


def _extract_json_payload(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).replace("```", "")
    cleaned = cleaned.strip()

    match = _JSON_BLOCK_PATTERN.search(cleaned)
    if not match:
        raise ValueError("No JSON object found in LLM response")

    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON must decode to an object")
    return payload


# ═══════════════════════════════════════════════════════════════════════
#  GEMINI  (google-genai SDK)
# ═══════════════════════════════════════════════════════════════════════

async def call_gemini(
    prompt: str,
    fallback_fn: Callable[[str], dict] | None = None,
) -> dict | None:
    """Call Gemini 2.5 Flash using the modern google-genai SDK."""
    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        logger.error("Gemini API key missing. Set GEMINI_API_KEY in environment.")
        if fallback_fn is not None:
            return fallback_fn(prompt)
        raise LLMUnavailableError("Gemini API key not configured.")

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
        )

        raw_response = response.text
        if not raw_response:
            raise ValueError("Empty response from Gemini")

        return _extract_json_payload(raw_response)

    except Exception as exc:
        logger.warning("Gemini request failed: %s: %s", type(exc).__name__, exc)
        if fallback_fn is not None:
            return fallback_fn(prompt)
        raise LLMUnavailableError(
            f"Gemini unavailable ({type(exc).__name__}): {exc}. "
            f"Check your API key and model name (gemini-2.5-flash)."
        ) from exc


# ═══════════════════════════════════════════════════════════════════════
#  UNIFIED ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════

async def call_llm(
    prompt: str,
    fallback_fn: Callable[[str], dict] | None = None,
) -> dict:
    return await call_gemini(prompt, fallback_fn)


async def parse_requirement(raw_input: str) -> dict:
    """
    Convert free-text procurement requirement → structured JSON.
    Has its own fallback using regex so a timeout never causes a 422.
    Falls back to regex extraction — good enough for standard formats
    like 'steel rod 5000kg delhi 100'.
    """
    prompt = f"""
Return only valid JSON. No explanation. No markdown.

Extract procurement requirement fields from the input text.

JSON schema:
{{
  "item": "product name in lowercase",
  "quantity": <number>,
  "unit": "kg or ton or piece or liter or meter — infer from context, default kg",
  "location": "city name in lowercase",
  "current_price": <number — price per unit in INR>,
  "category": "metals or chemicals or electronics or textiles or plastics or machinery or other"
}}

INPUT: {raw_input}
""".strip()

    def _regex_fallback(prompt_text: str) -> dict:
        """
        Pure regex fallback — no LLM needed.
        Handles formats like: 'steel rod 5000kg delhi 100'
        Logs a warning so the operator knows LLM was bypassed.
        """
        logger.warning(
            "Using regex fallback for requirement parsing — LLM timed out or unavailable"
        )
        text = raw_input.strip().lower()

        # Extract quantity + unit (e.g. 5000kg, 200 kg, 1.5 ton)
        qty_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(kg|ton|tonne|piece|pcs|liter|litre|meter|metre|box|quintal)?",
            text,
        )
        quantity = float(qty_match.group(1)) if qty_match else 0.0
        unit = qty_match.group(2) if (qty_match and qty_match.group(2)) else "kg"

        # Extract price — last standalone number not attached to a unit word
        # e.g. 'steel rod 5000kg delhi 100' → 100
        price_matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?!\s*(?:kg|ton|piece|pcs|liter|meter))", text)
        # Filter out the quantity value itself
        price_candidates = [float(p) for p in price_matches if float(p) != quantity]
        current_price = price_candidates[-1] if price_candidates else 0.0

        # Extract location — single word after quantity block that looks like a city
        # Remove digits and units to isolate text tokens
        tokens = re.sub(r"\d+(?:\.\d+)?\s*(?:kg|ton|tonne|piece|pcs|liter|meter|box)?", "", text)
        words = [w.strip() for w in tokens.split() if len(w.strip()) > 2]
        location = words[-1] if words else "unknown"

        # Extract item — everything before the first number
        item_match = re.match(r"^([a-z\s]+?)(?=\d)", text)
        item = item_match.group(1).strip() if item_match else words[0] if words else "unknown"

        return {
            "item": item,
            "quantity": quantity,
            "unit": unit,
            "location": location,
            "current_price": current_price,
            "category": "other",
        }

    return await call_llm(prompt, fallback_fn=_regex_fallback)


async def extract_price_from_response(raw_message: str, item: str) -> dict:
    """
    Extract structured pricing data from a vendor's raw reply.
    Fallback returns a low-confidence null-price dict so the pipeline
    marks the response invalid rather than crashing.
    """
    prompt = f"""
Return only valid JSON. No explanation. No markdown.

Extract pricing from vendor reply.

JSON schema:
{{
  "price": <number in INR per unit, exclude GST if mentioned separately, or null if not found>,
  "delivery_days": <number or null>,
  "gst_included": <true or false>,
  "min_quantity": <number or null>,
  "payment_terms": "<string or null>",
  "confidence": "high or medium or low"
}}

ITEM: {item}
VENDOR REPLY: {raw_message}
""".strip()

    def _fallback(_: str) -> dict:
        logger.warning(
            "LLM unavailable for price extraction — returning null-price low-confidence record"
        )
        return {
            "price": None,
            "delivery_days": None,
            "gst_included": False,
            "min_quantity": None,
            "payment_terms": None,
            "confidence": "low",
        }

    return await call_llm(prompt, fallback_fn=_fallback)


async def check_gemini_health() -> bool:
    """Ping Gemini with a tiny test prompt to verify the key works."""
    api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return False
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents="Reply with the word 'ok' only.",
        )
        return "ok" in response.text.lower()
    except Exception:
        return False


async def check_llm_health() -> bool:
    """Unified health check — Gemini only."""
    return await check_gemini_health()


async def generate_outreach_message(
    item: str,
    quantity: float,
    unit: str,
    location: str,
) -> str:
    prompt = f"""
Return only valid JSON with one key: "message".
Write a short, professional Hindi/Hinglish WhatsApp procurement outreach message.
Keep it under 60 words.

ITEM: {item}
QUANTITY: {quantity}
UNIT: {unit}
LOCATION: {location}
""".strip()

    def _fallback(_: str) -> dict:
        qty = int(quantity) if float(quantity).is_integer() else quantity
        return {
            "message": (
                f"Namaste, humein {qty} {unit} {item} ki requirement hai in {location}. "
                "Please best rate aur delivery timeline share karein."
            )
        }

    result = await call_llm(prompt, fallback_fn=_fallback)
    return str(result.get("message", "")).strip()


async def generate_final_report(
    item: str,
    quantity: float,
    unit: str,
    current_price: float,
    best_price: float,
    benchmark: dict,
    best_vendor_name: str,
    response_count: int,
) -> str:
    prompt = f"""
Return only valid JSON with one key: "report".
Write a concise plain-text procurement executive summary under 100 words.

ITEM: {item}
QUANTITY: {quantity}
UNIT: {unit}
CURRENT_PRICE: {current_price}
BEST_PRICE: {best_price}
BEST_VENDOR_NAME: {best_vendor_name}
AVG_PRICE: {benchmark.get("avg_price", 0)}
MEDIAN_PRICE: {benchmark.get("median_price", 0)}
RESPONSE_COUNT: {response_count}
""".strip()

    def _fallback(_: str) -> dict:
        savings_per_unit = current_price - best_price
        total_savings = savings_per_unit * quantity
        return {
            "report": (
                f"Best quote for {item} is Rs {best_price:.2f}/{unit} from {best_vendor_name}. "
                f"Current price is Rs {current_price:.2f}/{unit}, with estimated savings of "
                f"Rs {total_savings:,.2f} across {response_count} responses."
            )
        }

    result = await call_llm(prompt, fallback_fn=_fallback)
    return str(result.get("report", "")).strip()
