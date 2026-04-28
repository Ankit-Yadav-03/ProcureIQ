"""
Web-based price scraper for TradeIndia, IndiaMART, and other B2B portals.
Extracts product prices directly from website listings and product pages.
Uses Playwright for JS-heavy sites and Qwen LLM for price validation.
"""

from __future__ import annotations

import asyncio
import random
import re
from itertools import count
from typing import Awaitable, Callable, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from core.config import settings
from core.logger import get_logger
from services.llm_client import call_llm


logger = get_logger(__name__)

PRICE_PATTERN = re.compile(
    r"(?:₹\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*(?:rs|rupees|/-)?)",
    re.IGNORECASE,
)
UNIT_PATTERN = re.compile(
    r"\b(kg|ton|pcs|units|liters|ltr|mt|piece|pieces|liter|tonne|bag|box|drum)\b",
    re.IGNORECASE,
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 Safari/604.1",
]
_USER_AGENT_COUNTER = count()


def _next_user_agent() -> str:
    return USER_AGENTS[next(_USER_AGENT_COUNTER) % len(USER_AGENTS)]


async def _sleep_between_requests() -> None:
    await asyncio.sleep(
        random.uniform(settings.SCRAPING_DELAY_MIN, settings.SCRAPING_DELAY_MAX)
    )


async def _text_or_none(element) -> str | None:
    if element is None:
        return None
    try:
        value = await element.inner_text()
        cleaned = str(value).strip()
        return cleaned or None
    except Exception:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    numeric = re.sub(r"[^\d.]", "", value.replace(",", ""))
    return float(numeric) if numeric else None


def _extract_price(text: str) -> float | None:
    """Extract first price from text using regex."""
    if not text:
        return None
    match = PRICE_PATTERN.search(text)
    if match:
        return _to_float(match.group(0))
    return None


def _extract_unit(text: str) -> str | None:
    """Extract unit from text using regex."""
    if not text:
        return None
    match = UNIT_PATTERN.search(text.lower())
    if match:
        return match.group(1).lower()
    return None


async def _validate_price_with_llm(
    item: str,
    product_text: str,
    extracted_price: float | None,
) -> dict:
    """Use Qwen LLM to validate and extract prices from product text."""
    prompt = f"""
Extract product pricing information. Return only valid JSON with keys:
"price_per_unit", "unit", "currency", "confidence", "is_valid", "notes"

Rules:
- price_per_unit must be numeric in INR
- confidence: float between 0.0-1.0
- currency: should be 'INR'
- is_valid: true only if price seems genuine for the item

ITEM: {item}
PRODUCT_TEXT:
{product_text}
REGEX_EXTRACTED_PRICE: {extracted_price}
""".strip()

    try:
        result = await call_llm(prompt)
        result["confidence"] = float(result.get("confidence", 0.5))
        result["is_valid"] = bool(result.get("is_valid", extracted_price is not None))
        return result
    except Exception as exc:
        logger.warning(f"LLM price validation failed: {exc}, using regex result")
        return {
            "price_per_unit": extracted_price,
            "unit": _extract_unit(product_text),
            "currency": "INR",
            "confidence": 0.4 if extracted_price else 0.0,
            "is_valid": extracted_price is not None,
            "notes": "regex-extracted",
        }


async def scrape_indiamart_prices(
    browser,
    item: str,
    location: str,
) -> list[dict]:
    """
    Scrape product listings with prices from IndiaMART.
    
    Returns:
        List of dicts with: name, price, unit, url, location, confidence, source
    """
    products: list[dict] = []
    query = f"{item} {location}".replace(" ", "+")
    url = f"https://www.indiamart.com/search.mp?ss={query}"
    
    context = await browser.new_context(
        user_agent=_next_user_agent(),
        viewport={"width": 1280, "height": 800},
    )
    page = await context.new_page()

    try:
        await _sleep_between_requests()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector("body")

        # IndiaMART product cards
        cards = await page.query_selector_all(
            "[class*='product'], [class*='listing'], [class*='card']"
        )

        for card in cards[:8]:  # Limit to first 8 products
            try:
                # Extract product name
                name_elem = await card.query_selector("h2, h3, [class*='title']")
                name = await _text_or_none(name_elem)
                if not name:
                    continue

                # Extract price text
                price_elem = await card.query_selector(
                    "[class*='price'], [class*='amount'], span"
                )
                price_text = await _text_or_none(price_elem)
                
                # Extract product URL
                url_elem = await card.query_selector("a")
                product_url = (
                    await url_elem.get_attribute("href") if url_elem else None
                )
                if product_url and not product_url.startswith("http"):
                    product_url = f"https://www.indiamart.com{product_url}"

                # Extract price
                price = _extract_price(price_text) if price_text else None
                unit = _extract_unit(f"{name} {price_text}".lower())

                # Validate with LLM
                validation = await _validate_price_with_llm(
                    item, f"{name} {price_text}".strip(), price
                )

                if validation.get("is_valid"):
                    products.append({
                        "name": name,
                        "price": validation.get("price_per_unit"),
                        "unit": validation.get("unit") or unit or "unit",
                        "url": product_url,
                        "location": location,
                        "confidence": validation.get("confidence", 0.5),
                        "source": "indiamart",
                        "raw_price_text": price_text,
                    })

            except Exception as exc:
                logger.debug(f"Error scraping IndiaMART card: {exc}")
                continue

        return products

    except PlaywrightTimeout as exc:
        logger.error(f"IndiaMART scraping timeout: {exc}")
        return []
    except Exception as exc:
        logger.error(f"IndiaMART scraping failed: {exc}")
        return []
    finally:
        await context.close()


async def scrape_tradeindia_prices(
    browser,
    item: str,
    location: str,
) -> list[dict]:
    """
    Scrape product listings with prices from TradeIndia.
    
    Returns:
        List of dicts with: name, price, unit, url, location, confidence, source
    """
    products: list[dict] = []
    query = f"{item} {location}".replace(" ", "+")
    url = f"https://www.tradeindia.com/search/?q={query}"
    
    context = await browser.new_context(
        user_agent=_next_user_agent(),
        viewport={"width": 1280, "height": 800},
    )
    page = await context.new_page()

    try:
        await _sleep_between_requests()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector("body")

        # TradeIndia product cards
        cards = await page.query_selector_all(
            "[class*='company'], [class*='listing'], [class*='product']"
        )

        for card in cards[:8]:
            try:
                # Extract product/company name
                name_elem = await card.query_selector("h2, h3, [class*='title'], [class*='name']")
                name = await _text_or_none(name_elem)
                if not name:
                    continue

                # Extract price text - TradeIndia often shows price in specific divs
                price_elem = await card.query_selector(
                    "[class*='price'], [class*='cost'], [class*='amount']"
                )
                price_text = await _text_or_none(price_elem)
                
                # If no direct price, extract from card text
                if not price_text:
                    card_text = await _text_or_none(card)
                    price_match = PRICE_PATTERN.search(card_text or "")
                    price_text = price_match.group(0) if price_match else None

                # Extract product URL
                url_elem = await card.query_selector("a")
                product_url = (
                    await url_elem.get_attribute("href") if url_elem else None
                )
                if product_url and not product_url.startswith("http"):
                    product_url = f"https://www.tradeindia.com{product_url}"

                # Extract price
                price = _extract_price(price_text) if price_text else None
                unit = _extract_unit(f"{name} {price_text}".lower())

                # Validate with LLM
                validation = await _validate_price_with_llm(
                    item, f"{name} {price_text}".strip(), price
                )

                if validation.get("is_valid"):
                    products.append({
                        "name": name,
                        "price": validation.get("price_per_unit"),
                        "unit": validation.get("unit") or unit or "unit",
                        "url": product_url,
                        "location": location,
                        "confidence": validation.get("confidence", 0.5),
                        "source": "tradeindia",
                        "raw_price_text": price_text,
                    })

            except Exception as exc:
                logger.debug(f"Error scraping TradeIndia card: {exc}")
                continue

        return products

    except PlaywrightTimeout as exc:
        logger.error(f"TradeIndia scraping timeout: {exc}")
        return []
    except Exception as exc:
        logger.error(f"TradeIndia scraping failed: {exc}")
        return []
    finally:
        await context.close()


async def scrape_website_prices(
    item: str,
    location: str,
    sources: list[str] | None = None,
) -> dict[str, list[dict]]:
    """
    Scrape prices from multiple B2B websites.
    
    Args:
        item: Product/item name to search
        location: City/location for search
        sources: List of sources to scrape. Default: ['tradeindia', 'indiamart']
    
    Returns:
        Dict mapping source name to list of scraped products with prices
    """
    if sources is None:
        sources = ["tradeindia", "indiamart"]

    results: dict[str, list[dict]] = {}
    
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        
        try:
            if "tradeindia" in sources:
                logger.info(f"Scraping TradeIndia for {item} in {location}")
                results["tradeindia"] = await scrape_tradeindia_prices(
                    browser, item, location
                )
            
            if "indiamart" in sources:
                logger.info(f"Scraping IndiaMART for {item} in {location}")
                results["indiamart"] = await scrape_indiamart_prices(
                    browser, item, location
                )
        
        finally:
            await browser.close()
    
    return results


async def get_best_web_price(
    item: str,
    location: str,
    min_confidence: float = 0.5,
) -> dict | None:
    """
    Get the best (lowest) price from web scraping.
    
    Args:
        item: Product/item name
        location: City/location
        min_confidence: Minimum confidence threshold (0.0-1.0)
    
    Returns:
        Best product dict or None if no valid prices found
    """
    all_products = await scrape_website_prices(item, location)
    
    valid_products = [
        p for p in (all_products.get("tradeindia", []) + all_products.get("indiamart", []))
        if p.get("price") and p.get("confidence", 0) >= min_confidence
    ]
    
    if not valid_products:
        return None
    
    return min(valid_products, key=lambda x: x["price"])
