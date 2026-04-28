
"""
Outreach Routes
---------------
GET  /api/outreach/{requirement_id}     → Get outreach log with WhatsApp deep links
POST /api/outreach/mark-sent/{log_id}  → Mark outreach as sent
POST /api/outreach/webhook             → WhatsApp webhook receiver (Phase 2)
"""

import hashlib
import hmac
import logging
from fastapi import APIRouter, HTTPException, Request
from core.config import settings
from core.db import get_db
from services.outreach import get_outreach_log, mark_outreach_sent
from services.response_collector import process_whatsapp_webhook, submit_vendor_response

router = APIRouter()
logger = logging.getLogger(__name__)


def _is_valid_whatsapp_signature(body: bytes, signature: str | None) -> bool:
    app_secret = settings.WHATSAPP_APP_SECRET
    if not app_secret or not signature or not signature.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


@router.get("/outreach/webhook")
async def verify_whatsapp_webhook(request: Request):
    """Meta webhook verification endpoint."""
    params = dict(request.query_params)
    if params.get("hub.verify_token") == settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN:
        return int(params.get("hub.challenge", 0))
    raise HTTPException(status_code=403, detail="Invalid verify token")


@router.get("/outreach/{requirement_id}")
async def get_outreach(requirement_id: int):
    """
    Get all prepared outreach messages with WhatsApp deep links.
    Use these links to manually send messages in Phase 1.
    """
    log = await get_outreach_log(requirement_id)
    if not log:
        raise HTTPException(
            status_code=404,
            detail="No outreach prepared for this requirement yet.",
        )

    # Group by batch
    batches = {}
    for entry in log:
        batch = entry.get("batch_number", 1)
        if batch not in batches:
            batches[batch] = []
        batches[batch].append(entry)

    return {
        "requirement_id": requirement_id,
        "total_messages": len(log),
        "batches": batches,
        "instructions": (
            "Phase 1 (Manual): Click each WhatsApp link to send the pre-filled message. "
            "Mark each as sent after dispatching. "
            "Send batch 1 first, wait 10-15 mins, then send batch 2."
        ),
    }


@router.post("/outreach/mark-sent/{log_id}")
async def mark_sent(log_id: int):
    """Mark a specific outreach message as sent (called after manual dispatch)."""
    async with get_db(write=True) as db:
        cursor = await db.execute(
            "SELECT id FROM outreach_log WHERE id = ?", (log_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Outreach log entry not found")

    await mark_outreach_sent(log_id)
    return {"log_id": log_id, "status": "marked_sent"}


@router.post("/outreach/webhook")
async def whatsapp_webhook(request: Request):
    """
    Phase 2: WhatsApp Business API webhook receiver.
    Meta will POST incoming messages here.
    Auto-matches sender phone to vendor and stores response.
    """
    # Webhook verification (GET) — Meta sends this to verify the endpoint
    raw_body = await request.body()
    if not settings.WHATSAPP_APP_SECRET:
        logger.warning("WhatsApp webhook called but WHATSAPP_APP_SECRET is not configured")
        return {
            "status": "not_configured",
            "message": "WhatsApp integration not enabled in this build",
        }
    if not _is_valid_whatsapp_signature(
        raw_body,
        request.headers.get("X-Hub-Signature-256"),
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = await request.json()
        entry_count = len(payload.get("entry", [])) if isinstance(payload, dict) else 0
        logger.info("Webhook received with %s entry records", entry_count)

        parsed = await process_whatsapp_webhook(payload)
        if not parsed:
            return {"status": "ignored"}

        phone = parsed["phone"]
        message = parsed["message"]

        async with get_db() as db:
            cursor = await db.execute(
                """SELECT v.id as vendor_id, v.requirement_id
                   FROM vendors v
                   WHERE v.phone = ?
                   ORDER BY v.id DESC""",
                (phone,),
            )
            rows = await cursor.fetchall()

        if not rows:
            logger.warning(f"Webhook: no vendor found for phone {phone}")
            return {"status": "vendor_not_found"}

        requirement_ids = {int(row["requirement_id"]) for row in rows}
        if len(requirement_ids) > 1:
            logger.warning("Webhook: ambiguous vendor match for phone %s", phone)
            return {"status": "ambiguous_vendor_match", "phone": phone}

        row = rows[0]
        response_id = await submit_vendor_response(
            vendor_id=row["vendor_id"],
            requirement_id=row["requirement_id"],
            raw_message=message,
        )

        logger.info(f"Webhook: auto-stored response {response_id} from {phone}")
        return {"status": "stored", "response_id": response_id}
    except Exception as exc:
        logger.warning("WhatsApp webhook ignored after processing error: %s", exc)
        return {
            "status": "error",
            "message": "WhatsApp webhook could not be processed cleanly",
        }
