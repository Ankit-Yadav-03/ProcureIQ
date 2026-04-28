from __future__ import annotations

from typing import List, Optional

from core.db import get_db
from core.logger import get_logger
from core.utils import normalize_phone, truncate_utf8


logger = get_logger(__name__)


async def submit_vendor_response(
    vendor_id: int,
    requirement_id: int,
    raw_message: str,
) -> int | None:
    cleaned_message = truncate_utf8(raw_message, 1000).strip()
    if not cleaned_message:
        raise ValueError("raw_message cannot be empty after truncation")

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, requirement_id FROM vendors WHERE id = ?",
            (vendor_id,),
        )
        vendor_row = await cursor.fetchone()
    if not vendor_row:
        raise ValueError(f"Vendor {vendor_id} not found")
    if int(vendor_row["requirement_id"]) != requirement_id:
        raise ValueError(
            f"Vendor {vendor_id} does not belong to requirement {requirement_id}"
        )

    async with get_db(write=True) as db:
        await db.execute("BEGIN")
        try:
            duplicate_cursor = await db.execute(
                """
                SELECT id FROM vendor_responses
                WHERE vendor_id = ?
                  AND substr(raw_message, 1, 100) = substr(?, 1, 100)
                """,
                (vendor_id, cleaned_message),
            )
            duplicate_row = await duplicate_cursor.fetchone()
            if duplicate_row:
                logger.warning("Duplicate response skipped")
                await db.rollback()
                return None

            cursor = await db.execute(
                "SELECT id FROM vendors WHERE id = ? AND requirement_id = ?",
                (vendor_id, requirement_id),
            )
            if not await cursor.fetchone():
                raise ValueError(
                    f"Vendor {vendor_id} not found for requirement {requirement_id}"
                )

            insert_cursor = await db.execute(
                """
                INSERT INTO vendor_responses
                (vendor_id, requirement_id, raw_message, received_at, is_valid)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1)
                """,
                (vendor_id, requirement_id, cleaned_message),
            )
            await db.execute(
                "UPDATE vendors SET contact_status = 'responded' WHERE id = ?",
                (vendor_id,),
            )
            await db.commit()
            response_id = insert_cursor.lastrowid
            logger.info(
                "Response received from vendor %s, response ID: %s",
                vendor_id,
                response_id,
            )
            return response_id
        except Exception:
            await db.rollback()
            raise


async def get_raw_responses(requirement_id: int) -> List[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT r.*, v.name as vendor_name
            FROM vendor_responses r
            JOIN vendors v ON r.vendor_id = v.id
            WHERE r.requirement_id = ?
            ORDER BY r.id
            """,
            (requirement_id,),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_all_responses(requirement_id: int) -> List[dict]:
    return await get_raw_responses(requirement_id)


async def process_whatsapp_webhook(payload: dict) -> Optional[dict]:
    try:
        entry = payload.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return None

        msg = messages[0]
        from_phone = msg.get("from", "")
        text = truncate_utf8(msg.get("text", {}).get("body", ""), 1000).strip()

        if not text:
            return None

        return {
            "phone": normalize_phone(from_phone),
            "message": text,
        }
    except Exception as exc:
        logger.error("Webhook parse error: %s", exc)
        return None


async def match_response_to_vendor(
    phone: str,
    requirement_id: int,
) -> Optional[int]:
    try:
        normalized_phone = normalize_phone(phone)
    except ValueError:
        return None

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM vendors WHERE phone = ? AND requirement_id = ?",
            (normalized_phone, requirement_id),
        )
        row = await cursor.fetchone()
    return row["id"] if row else None
