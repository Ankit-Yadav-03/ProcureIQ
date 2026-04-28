from __future__ import annotations

import asyncio
import random
import urllib.parse
from typing import List

import httpx

from core.config import settings
from core.logger import get_logger
from core.db import get_db
from core.schemas import Vendor
from core.utils import normalize_phone
from services.llm_client import generate_outreach_message


logger = get_logger(__name__)
BATCH_SIZE = 5


def _normalized_vendor_phone(vendor: dict) -> str | None:
    phone = vendor.get("phone")
    if not phone:
        return None

    try:
        validated = Vendor.model_validate(
            {
                "name": vendor.get("name", "Vendor"),
                "phone": normalize_phone(phone),
                "location": vendor.get("location", "unknown"),
                "rating": vendor.get("rating"),
                "source": vendor.get("source", "googlemaps"),
            }
        )
        return validated.phone
    except Exception as exc:
        logger.warning("Skipping invalid vendor phone for outreach: %s", exc)
        return None


def _build_whatsapp_link(phone: str, message: str) -> str:
    encoded = urllib.parse.quote(message)
    normalized_phone = normalize_phone(phone)
    return f"https://wa.me/{normalized_phone.replace('+', '')}?text={encoded}"


async def _generate_message_for_vendor(
    item: str,
    quantity: float,
    unit: str,
    location: str,
    vendor_name: str,
) -> str:
    try:
        base_message = await generate_outreach_message(item, quantity, unit, location)
        return f"Namaste {vendor_name.split()[0]},\n\n{base_message}"
    except Exception as exc:
        logger.warning("LLM unavailable for message generation: %s. Using template.", exc)
        return _default_message_template(item, quantity, unit, location, vendor_name)


def _default_message_template(
    item: str,
    quantity: float,
    unit: str,
    location: str,
    vendor_name: str,
) -> str:
    qty_str = f"{int(quantity) if quantity.is_integer() else quantity} {unit}"
    return (
        f"Namaste {vendor_name.split()[0]} ji,\n\n"
        f"Hum {location.title()} se hain. Humein {qty_str} {item} ki urgent zaroorat hai.\n"
        f"Ye regular requirement hai hamare liye.\n\n"
        f"Kripya best rate aur delivery timeline share karein.\n\n"
        "Dhanyawad."
    )


async def prepare_outreach_batch(
    requirement_id: int,
    vendors: List[dict],
    item: str,
    quantity: float,
    unit: str,
    location: str,
) -> List[dict]:
    prepared_records = []
    batch_number = 1

    for i, vendor in enumerate(vendors):
        if i > 0 and i % BATCH_SIZE == 0:
            batch_number += 1
            batch_delay = random.uniform(8, 15)
            logger.info(
                "Waiting %.2f seconds before preparing outreach batch %s",
                batch_delay,
                batch_number,
            )
            await asyncio.sleep(batch_delay)

        message = await _generate_message_for_vendor(
            item=item,
            quantity=quantity,
            unit=unit,
            location=location,
            vendor_name=vendor.get("name", "Vendor"),
        )

        phone = _normalized_vendor_phone(vendor)
        prepared_records.append(
            {
                "vendor": vendor,
                "message": message,
                "vendor_phone": phone,
                "wa_link": _build_whatsapp_link(phone, message) if phone else None,
                "batch_number": batch_number,
            }
        )

    outreach_records = []
    async with get_db(write=True) as db:
        await db.execute("BEGIN")
        try:
            for record in prepared_records:
                vendor = record["vendor"]
                cursor = await db.execute(
                    """INSERT INTO outreach_log
                       (vendor_id, requirement_id, message_text, channel, status, batch_number)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        vendor["id"],
                        requirement_id,
                        record["message"],
                        "whatsapp",
                        "prepared",
                        record["batch_number"],
                    ),
                )
                outreach_records.append(
                    {
                        "log_id": cursor.lastrowid,
                        "vendor_id": vendor["id"],
                        "vendor_name": vendor["name"],
                        "vendor_phone": record["vendor_phone"],
                        "message": record["message"],
                        "wa_link": record["wa_link"],
                        "batch_number": record["batch_number"],
                        "status": "prepared",
                    }
                )
                logger.info(
                    "Prepared message for %s (batch %s)",
                    vendor["name"],
                    record["batch_number"],
                )

            await db.execute(
                "UPDATE requirements SET status = 'outreach_ready' WHERE id = ?",
                (requirement_id,),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return outreach_records


async def mark_outreach_sent(log_id: int):
    async with get_db(write=True) as db:
        await db.execute("BEGIN")
        try:
            await db.execute(
                "UPDATE outreach_log SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?",
                (log_id,),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def send_via_whatsapp_api(
    phone: str,
    message: str,
    vendor_id: int,
    requirement_id: int,
    log_id: int,
    api_token: str,
    phone_number_id: str,
) -> bool:
    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    try:
        normalized_phone = normalize_phone(phone)
    except ValueError as exc:
        logger.error("WhatsApp API invalid phone %s: %s", phone, exc)
        return False

    payload = {
        "messaging_product": "whatsapp",
        "to": normalized_phone.replace("+", ""),
        "type": "text",
        "text": {"body": message},
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                await mark_outreach_sent(log_id)
                logger.info("WhatsApp API: sent to %s", normalized_phone)
                return True
            logger.error("WhatsApp API error %s: %s", resp.status_code, resp.text)
            return False
    except Exception as exc:
        logger.error("WhatsApp API exception: %s", exc)
        return False


async def get_outreach_log(requirement_id: int) -> List[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT
                   o.id as log_id,
                   o.vendor_id,
                   o.requirement_id,
                   o.message_text as message,
                   o.channel,
                   o.status,
                   o.batch_number,
                   v.name as vendor_name,
                   v.phone as vendor_phone
               FROM outreach_log o
               JOIN vendors v ON o.vendor_id = v.id
               WHERE o.requirement_id = ?
               ORDER BY o.batch_number, o.id""",
            (requirement_id,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            record = dict(row)
            phone = record.get("vendor_phone")
            message = record.get("message", "")
            if phone:
                record["wa_link"] = _build_whatsapp_link(phone, message)
            else:
                record["wa_link"] = None
            result.append(record)
        return result
