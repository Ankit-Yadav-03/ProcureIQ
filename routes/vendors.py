"""
Vendor Routes
GET  /api/vendors/{requirement_id}     → List vendors
POST /api/vendors/response             → Submit vendor response (manual entry)
GET  /api/vendors/responses/{req_id}  → List all responses
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.db import get_db
from services.vendor_discovery import get_vendors_for_requirement
from services.response_collector import submit_vendor_response, get_all_responses

router = APIRouter()
logger = logging.getLogger(__name__)


def _serialize_vendor(vendor: dict) -> dict:
    return {
        "vendor_id": vendor["id"],
        "vendor_name": vendor["name"],
        "id": vendor["id"],
        "requirement_id": vendor["requirement_id"],
        "name": vendor["name"],
        "phone": vendor["phone"],
        "source": vendor["source"],
        "location": vendor["location"],
        "rating": vendor["rating"],
        "profile_url": vendor["profile_url"],
        "discovered_at": vendor["discovered_at"],
        "status": vendor["contact_status"],
        "contact_status": vendor["contact_status"],
    }


class ResponseSubmission(BaseModel):
    vendor_id: int
    requirement_id: int
    raw_message: str


@router.get("/vendors/{requirement_id}")
async def list_vendors(requirement_id: int):
    vendors = await get_vendors_for_requirement(requirement_id)
    if not vendors:
        return {"vendors": [], "count": 0}
    return {"vendors": [_serialize_vendor(v) for v in vendors], "count": len(vendors)}


@router.post("/vendors/response")
async def submit_response(payload: ResponseSubmission):
    """
    Manual response entry endpoint.
    Use this to submit vendor replies received via WhatsApp/phone.
    The raw_message can be any text — LLM will extract price from it.
    """
    if not payload.raw_message.strip():
        raise HTTPException(status_code=400, detail="raw_message cannot be empty")

    # Verify vendor exists
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM vendors WHERE id = ? AND requirement_id = ?",
            (payload.vendor_id, payload.requirement_id),
        )
        if not await cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail=f"Vendor {payload.vendor_id} not found for requirement {payload.requirement_id}",
            )

    response_id = await submit_vendor_response(
        vendor_id=payload.vendor_id,
        requirement_id=payload.requirement_id,
        raw_message=payload.raw_message,
    )

    return {
        "response_id": response_id,
        "vendor_id": payload.vendor_id,
        "requirement_id": payload.requirement_id,
        "message": "Response received. Run POST /api/roi/{id} to extract prices and compute ROI.",
    }


@router.get("/vendors/responses/{requirement_id}")
async def list_responses(requirement_id: int):
    responses = await get_all_responses(requirement_id)
    return {"responses": responses, "count": len(responses)}


@router.post("/vendors/mock-response")
async def submit_mock_response(payload: ResponseSubmission):
    """
    Test endpoint only — for local testing without real vendor outreach.
    Same as /response but labeled explicitly.
    """
    return await submit_response(payload)
