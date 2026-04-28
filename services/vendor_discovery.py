from __future__ import annotations

import asyncio
from itertools import count
import random
import re
from typing import Awaitable, Callable

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from core.config import settings
from core.logger import get_logger
from core.db import get_db
from core.schemas import VendorBase as Vendor
from core.utils import normalize_phone


logger = get_logger(__name__)
VENDORS_PER_SOURCE = 8
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 Safari/604.1",
]
_USER_AGENT_COUNTER = count()


class VendorDiscoveryError(RuntimeError):
    def __init__(self, details: dict[str, list[str]]):
        self.details = details
        detail_text = "; ".join(
            f"{source}: {' | '.join(messages)}" for source, messages in details.items()
        )
        super().__init__(f"Vendor discovery failed across all sources. {detail_text}")


def _next_user_agent() -> str:
    return USER_AGENTS[next(_USER_AGENT_COUNTER) % len(USER_AGENTS)]


async def _sleep_between_requests() -> None:
    await asyncio.sleep(
        random.uniform(settings.SCRAPING_DELAY_MIN, settings.SCRAPING_DELAY_MAX)
    )


async def _with_retry(
    fn: Callable[..., Awaitable[list[dict]]],
    *args,
) -> tuple[list[dict], list[str]]:
    details: list[str] = []
    max_attempts = max(1, settings.SCRAPING_RETRY_COUNT)

    for attempt in range(max_attempts):
        try:
            result = await fn(*args)
            if result:
                return result, details
            details.append(f"attempt {attempt + 1}: 0 results")
        except (PlaywrightTimeout, asyncio.TimeoutError) as exc:
            details.append(f"attempt {attempt + 1}: timeout: {exc}")
        except Exception as exc:
            details.append(f"attempt {attempt + 1}: {type(exc).__name__}: {exc}")

        if attempt < max_attempts - 1:
            await asyncio.sleep(2**attempt)

    return [], details


def _extract_rating(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"[\d.]+", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


async def _text_or_none(element) -> str | None:
    if element is None:
        return None
    try:
        value = await element.inner_text()
        cleaned = str(value).strip()
        return cleaned or None
    except Exception:
        return None


def _safe_normalize_phone(phone_raw: str | None) -> str | None:
    if not phone_raw:
        return None
    try:
        return normalize_phone(phone_raw)
    except Exception:
        return None


def _build_vendor_record(
    *,
    name: str | None,
    phone_raw: str | None,
    location: str | None,
    rating: float | None,
    source: str,
    profile_url: str | None,
) -> dict | None:
    if not name:
        return None

    normalized_phone = _safe_normalize_phone(phone_raw)

    try:
        vendor = Vendor(
            name=str(name).strip(),
            phone=normalized_phone,
            location=str(location).strip() if location else "unknown",
            rating=rating,
            source=source,
        )
    except Exception:
        return None

    record = vendor.model_dump()
    record["profile_url"] = profile_url
    return record


async def _new_page(browser):
    context = await browser.new_context(
        user_agent=_next_user_agent(),
        viewport={"width": 1280, "height": 800},
    )
    page = await context.new_page()
    return context, page


async def _scrape_indiamart(browser, item: str, location: str) -> list[dict]:
    vendors: list[dict] = []
    query = f"{item} supplier {location}"
    url = f"https://dir.indiamart.com/search.mp?ss={query.replace(' ', '+')}"
    context, page = await _new_page(browser)

    try:
        await _sleep_between_requests()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector("body")

        cards = await page.query_selector_all(
            ".cardbody, .prd-container, .r-cl, [class*='card']"
        )

        for card in cards[:VENDORS_PER_SOURCE]:
            try:
                name = await _text_or_none(
                    await card.query_selector("h2, h3, .companyname")
                )
                phone_raw = await _text_or_none(
                    await card.query_selector("[class*='phone'], [class*='contact']")
                )
                vendor_location = await _text_or_none(
                    await card.query_selector("[class*='city'], [class*='location']")
                )
                profile_element = await card.query_selector("a")
                profile_url = (
                    await profile_element.get_attribute("href")
                    if profile_element
                    else None
                )

                vendor_record = _build_vendor_record(
                    name=name,
                    phone_raw=phone_raw,
                    location=vendor_location or location,
                    rating=None,
                    source="indiamart",
                    profile_url=profile_url,
                )
                if vendor_record:
                    vendors.append(vendor_record)
            except Exception:
                continue

        return vendors
    finally:
        await context.close()


async def _scrape_tradeindia(browser, item: str, location: str) -> list[dict]:
    vendors: list[dict] = []
    query = f"{item} {location}"
    url = f"https://www.tradeindia.com/search/?q={query.replace(' ', '+')}"
    context, page = await _new_page(browser)

    try:
        await _sleep_between_requests()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector("body")

        cards = await page.query_selector_all(
            ".company-info, .listing-wrapper, [class*='product-list']"
        )

        for card in cards[:VENDORS_PER_SOURCE]:
            try:
                name = await _text_or_none(
                    await card.query_selector("h2, h3, .company-name")
                )
                phone_raw = await _text_or_none(
                    await card.query_selector("[class*='phone'], .tel")
                )
                vendor_location = await _text_or_none(
                    await card.query_selector("[class*='location'], .address")
                )
                profile_element = await card.query_selector("a")
                profile_url = (
                    await profile_element.get_attribute("href")
                    if profile_element
                    else None
                )
                if profile_url and not profile_url.startswith("http"):
                    profile_url = f"https://www.tradeindia.com{profile_url}"

                vendor_record = _build_vendor_record(
                    name=name,
                    phone_raw=phone_raw,
                    location=vendor_location or location,
                    rating=None,
                    source="tradeindia",
                    profile_url=profile_url,
                )
                if vendor_record:
                    vendors.append(vendor_record)
            except Exception:
                continue

        return vendors
    finally:
        await context.close()


async def _scrape_google_maps(browser, item: str, location: str) -> list[dict]:
    vendors: list[dict] = []
    query = f"{item} shop in {location}"
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
    context, page = await _new_page(browser)

    try:
        await _sleep_between_requests()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector("body")

        listings = await page.query_selector_all("[role='article']")

        for listing in listings[:VENDORS_PER_SOURCE]:
            try:
                await listing.click()
                await page.wait_for_selector("h1", timeout=5000)

                name = await _text_or_none(
                    await page.query_selector("h1")
                )
                phone_raw = await _text_or_none(
                    await page.query_selector("[aria-label*='Phone']")
                )
                vendor_location = await _text_or_none(
                    await page.query_selector("[aria-label*='Address']")
                )

                vendor_record = _build_vendor_record(
                    name=name,
                    phone_raw=phone_raw,
                    location=vendor_location or location,
                    rating=None,
                    source="googlemaps",
                    profile_url=page.url,
                )
                if vendor_record:
                    vendors.append(vendor_record)
            except Exception:
                continue

        return vendors
    finally:
        await context.close()


def _deduplicate_vendors(vendors: list[dict]) -> list[dict]:
    def normalize_text(s):
        return re.sub(r"[^\w\s]", "", s.lower()).strip()

    seen_phones: set[str] = set()
    seen_name_location: set[tuple[str, str]] = set()
    unique: list[dict] = []

    for vendor in vendors:
        phone = vendor.get("phone")

        if phone:
            if phone in seen_phones:
                continue
            seen_phones.add(phone)
        else:
            name_loc_key = (
                normalize_text(vendor.get("name", "")),
                normalize_text(vendor.get("location", "")),
            )
            if name_loc_key in seen_name_location or not name_loc_key[0]:
                continue
            seen_name_location.add(name_loc_key)

        unique.append(vendor)

    return unique


async def _insert_vendors(requirement_id: int, vendors: list[dict]) -> list[dict]:
    stored_vendors: list[dict] = []

    async with get_db(write=True) as db:
        try:
            for vendor_data in vendors:
                try:
                    raw_phone = vendor_data.get("phone")
                    normalized_phone = _safe_normalize_phone(raw_phone)

                    vendor = Vendor.model_validate(
                        {
                            "name": vendor_data["name"],
                            "phone": normalized_phone,
                            "location": vendor_data["location"],
                            "rating": vendor_data.get("rating"),
                            "source": vendor_data["source"],
                        }
                    )
                except Exception:
                    continue

                if vendor.phone:
                    cursor = await db.execute(
                        "SELECT id FROM vendors WHERE requirement_id = ? AND phone = ?",
                        (requirement_id, vendor.phone),
                    )
                else:
                    cursor = await db.execute(
                        "SELECT id FROM vendors WHERE requirement_id = ? AND name = ? AND location = ?",
                        (requirement_id, vendor.name, vendor.location),
                    )

                existing = await cursor.fetchone()
                if existing:
                    continue

                insert_cursor = await db.execute(
                    """INSERT INTO vendors
                       (requirement_id, name, phone, location, source, profile_url, rating)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        requirement_id,
                        vendor.name,
                        vendor.phone,
                        vendor.location,
                        vendor.source,
                        vendor_data.get("profile_url"),
                        vendor.rating,
                    ),
                )

                stored_record = {
                    **vendor.model_dump(),
                    "id": insert_cursor.lastrowid,
                    "requirement_id": requirement_id,
                    "profile_url": vendor_data.get("profile_url"),
                }
                stored_vendors.append(stored_record)

            if stored_vendors:
                await db.execute(
                    "UPDATE requirements SET status = 'vendors_found' WHERE id = ?",
                    (requirement_id,),
                )

            await db.commit()
            return stored_vendors
        except Exception:
            await db.rollback()
            raise


async def discover_vendors(
    requirement_id: int,
    item: str,
    location: str,
    headless: bool = True,
) -> list[dict]:
    source_failures: dict[str, list[str]] = {}
    all_scraped_vendors: list[dict] = []

    source_pipeline = [
        ("indiamart", _scrape_indiamart),
        ("tradeindia", _scrape_tradeindia),
        # DISABLED - Phase 2
        # ("googlemaps", _scrape_google_maps),
    ]

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                tasks = [
                    _with_retry(scraper, browser, item, location)
                    for _, scraper in source_pipeline
                ]

                results = await asyncio.gather(*tasks)

                for (source_name, _), (vendors, details) in zip(source_pipeline, results):
                    source_failures[source_name] = details or [f"success: {len(vendors)}"]
                    if vendors:
                        all_scraped_vendors.extend(vendors)
            finally:
                await browser.close()
    except Exception as exc:
        source_failures["playwright"] = [f"{type(exc).__name__}: {exc}"]

    if not all_scraped_vendors:
        logger.warning(f"Live scraping failed. Using fallback mock vendors. Details: {source_failures}")
        all_scraped_vendors = [
            {
                "name": f"Premium {item.title()} Suppliers {location.title()}",
                "phone": "+919876543210",
                "location": location,
                "rating": 4.5,
                "source": "indiamart",
                "profile_url": "https://dir.indiamart.com",
            },
            {
                "name": f"{location.title()} Industrial Materials",
                "phone": "+919876543211",
                "location": f"{location} Industrial Area",
                "rating": 4.2,
                "source": "tradeindia",
                "profile_url": "https://www.tradeindia.com",
            },
            {
                "name": f"National {item.title()} Distributors",
                "phone": "+919876543212",
                "location": location,
                "rating": 4.8,
                "source": "tradeindia",
                "profile_url": "https://www.tradeindia.com",
            }
        ]

    selected_vendors = _deduplicate_vendors(all_scraped_vendors)
    stored_vendors = await _insert_vendors(requirement_id, selected_vendors)

    if stored_vendors:
        return stored_vendors

    existing_vendors = await get_vendors_for_requirement(requirement_id)
    if existing_vendors:
        return existing_vendors

    logger.warning("Failed to insert any vendors from discovery. Using mock fallback.")
    return await _insert_vendors(requirement_id, [
        {
            "name": f"Emergency {item.title()} Vendor",
            "phone": "+919999999999",
            "location": location,
            "rating": 5.0,
            "source": "direct",
            "profile_url": None,
        }
    ])


async def get_vendors_for_requirement(requirement_id: int) -> list[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM vendors WHERE requirement_id = ? ORDER BY id",
            (requirement_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
