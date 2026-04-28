"""
Procurement Routes — Main pipeline endpoints
POST /api/analyze     → Full pipeline (parse → discover → prepare outreach)
POST /api/roi/{id}    → Run ROI analysis (after responses collected)
GET  /api/status/{id} → Pipeline status
GET  /api/result/{id} → Get final result
"""

import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from core.schemas import ProcurementRequest, ProcurementResult
from core.config import settings
from core.db import get_db
from services.parser import parse_and_store, get_requirement
from services.vendor_discovery import discover_vendors, get_vendors_for_requirement
from services.outreach import prepare_outreach_batch
from services.response_collector import get_all_responses
from services.roi import run_roi_analysis, get_latest_result
from services.llm_client import call_llm, check_llm_health

router = APIRouter()
logger = logging.getLogger(__name__)


class NegotiateRequest(BaseModel):
    requirement_id: int
    best_competing_price: float
    quantity: int
    unit: str


@router.get("/health/llm")
async def check_llm():
    """Check if the Gemini LLM is available."""
    is_up = await check_llm_health()
    return {
        "llm_running": is_up,
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "message": "Ready" if is_up else "Gemini is not responding — check GEMINI_API_KEY",
    }


@router.post("/analyze")
async def analyze(request: ProcurementRequest, background_tasks: BackgroundTasks):
    """
    Step 1→3: Parse requirement, discover vendors, prepare outreach.
    Returns immediately with requirement_id and vendor list.
    Outreach prep runs in background.
    """
    if not request.input_text.strip():
        raise HTTPException(status_code=400, detail="input_text cannot be empty")

    # Step 2: Parse
    try:
        requirement, req_id = await parse_and_store(request.input_text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Step 3: Vendor discovery (run in background — can take time)
    # We use a sync wrapper to run it in a new thread with a fresh Proactor event loop!
    background_tasks.add_task(
        _sync_run_discovery_and_outreach,
        req_id,
        requirement.item,
        requirement.location,
        requirement.quantity,
        requirement.unit,
    )

    return {
        "requirement_id": req_id,
        "parsed": {
            "item": requirement.item,
            "quantity": requirement.quantity,
            "unit": requirement.unit,
            "location": requirement.location,
            "current_price": requirement.current_price,
            "category": requirement.category,
        },
        "status": "discovery_started",
        "message": (
            "Vendor discovery running in background. "
            f"Poll GET /api/status/{req_id} to check progress."
        ),
    }


def _sync_run_discovery_and_outreach(
    req_id: int,
    item: str,
    location: str,
    quantity: float,
    unit: str,
):
    """Sync wrapper to run Playwright safely in a fresh WindowsProactorEventLoop."""
    import asyncio
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _run_discovery_and_outreach(req_id, item, location, quantity, unit)
        )
    finally:
        loop.close()


async def _run_discovery_and_outreach(
    req_id: int,
    item: str,
    location: str,
    quantity: float,
    unit: str,
):
    """Background task: discover vendors + prepare outreach messages."""
    try:
        vendors = await discover_vendors(req_id, item, location)
        if not vendors:
            logger.warning(f"No vendors found for requirement {req_id}")
            async with get_db(write=True) as db:
                await db.execute("BEGIN")
                try:
                    await db.execute(
                        "UPDATE requirements SET status = 'no_vendors_found' WHERE id = ?",
                        (req_id,),
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
            return

        req = await get_requirement(req_id)
        await prepare_outreach_batch(
            requirement_id=req_id,
            vendors=vendors,
            item=item,
            quantity=quantity,
            unit=unit,
            location=req["location"],
        )
        logger.info(f"Discovery + outreach prep complete for requirement {req_id}")
    except Exception as e:
        logger.error(f"Background discovery failed for {req_id}: {e}")
        async with get_db(write=True) as db:
            await db.execute("BEGIN")
            try:
                await db.execute(
                    "UPDATE requirements SET status = 'discovery_error' WHERE id = ?",
                    (req_id,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise


@router.get("/status/{requirement_id}")
async def get_status(requirement_id: int):
    """Get current pipeline status for a requirement."""
    req = await get_requirement(requirement_id)
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")

    vendors = await get_vendors_for_requirement(requirement_id)
    responses = await get_all_responses(requirement_id)
    result = await get_latest_result(requirement_id)

    responded_count = sum(1 for v in vendors if v.get("contact_status") == "responded")

    return {
        "requirement_id": requirement_id,
        "status": req["status"],
        "item": req["item"],
        "quantity": req["quantity"],
        "unit": req.get("unit", "kg"),
        "location": req["location"],
        "current_price": req["current_price"],
        "vendors_found": len(vendors),
        "vendors_responded": responded_count,
        "responses_received": len(responses),
        "roi_calculated": result is not None,
        "vendors": [
            {
                "vendor_id": v["id"],
                "vendor_name": v["name"],
                "id": v["id"],
                "name": v["name"],
                "phone": v["phone"],
                "source": v["source"],
                "location": v["location"],
                "rating": v["rating"],
                "profile_url": v["profile_url"],
                "status": v["contact_status"],
                "contact_status": v["contact_status"],
            }
            for v in vendors
        ],
    }


@router.get("/roi/{requirement_id}")
async def get_roi(requirement_id: int):
    """Get the latest ROI result for frontend polling."""
    result = await get_latest_result(requirement_id)
    if not result:
        raise HTTPException(status_code=404, detail="No ROI result found")

    best_vendor_name = None
    best_vendor_id = result.get("best_vendor_id")
    if best_vendor_id:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT name FROM vendors WHERE id = ?",
                (best_vendor_id,),
            )
            row = await cursor.fetchone()
            if row:
                best_vendor_name = row["name"]

    return {
        "requirement_id": requirement_id,
        "best_price": result.get("best_price"),
        "best_vendor_id": best_vendor_id,
        "best_vendor_name": best_vendor_name,
        "avg_price": result.get("avg_price"),
        "min_price": result.get("min_price"),
        "median_price": result.get("median_price"),
        "total_savings": result.get("total_savings"),
        "savings_pct": result.get("savings_pct"),
        "savings_amount": result.get("total_savings"),
        "confidence": result.get("confidence"),
        "vendor_count": result.get("vendor_count") or 0,
        "response_count": result.get("response_count") or 0,
        "status": "roi_ready",
    }


@router.post("/roi/{requirement_id}")
async def calculate_roi(requirement_id: int):
    """
    Step 6→10: Extract prices, clean, benchmark, compute ROI, generate report.
    Call this after vendor responses have been submitted.
    """
    req = await get_requirement(requirement_id)
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")

    responses = await get_all_responses(requirement_id)
    if not responses:
        raise HTTPException(
            status_code=400,
            detail=(
                "No vendor responses found. "
                "Submit responses via POST /api/vendors/response first."
            ),
        )

    try:
        result = await run_roi_analysis(
            requirement_id=requirement_id,
            item=req["item"],
            quantity=req["quantity"],
            unit=req.get("unit", "kg"),
            current_price=req["current_price"],
        )
        if result.get("status") == "partial":
            raise HTTPException(status_code=500, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ROI analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/result/{requirement_id}")
async def get_result(requirement_id: int):
    """Get the stored ROI result for a requirement."""
    result = await get_latest_result(requirement_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail="No result found. Run POST /api/roi/{id} first.",
        )
    return result


@router.get("/requirements")
async def list_requirements():
    """List all procurement requirements."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM requirements ORDER BY created_at DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


@router.post("/negotiate/{vendor_id}")
async def negotiate_vendor(vendor_id: int, request: NegotiateRequest):
    """
    Generate a smart, data-driven negotiation message for a vendor.
    Queries actual vendor response prices and market data from the database.
    """
    async with get_db() as db:
        vendor_cursor = await db.execute(
            "SELECT * FROM vendors WHERE id = ?",
            (vendor_id,),
        )
        vendor = await vendor_cursor.fetchone()
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")

        req_cursor = await db.execute(
            "SELECT * FROM requirements WHERE id = ?",
            (request.requirement_id,),
        )
        requirement = await req_cursor.fetchone()
        if not requirement:
            raise HTTPException(status_code=404, detail="Requirement not found")

        # ── Pull this vendor's quoted price ───────────────────────────
        vendor_price_cursor = await db.execute(
            """
            SELECT price, raw_message
            FROM vendor_responses
            WHERE vendor_id = ? AND requirement_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (vendor_id, request.requirement_id),
        )
        vendor_price_row = await vendor_price_cursor.fetchone()
        vendor_quoted_price = vendor_price_row["price"] if vendor_price_row else None

        # ── Pull ALL valid responses to find best competing price ─────
        all_responses_cursor = await db.execute(
            """
            SELECT vendor_id, price, raw_message
            FROM vendor_responses
            WHERE requirement_id = ? AND price IS NOT NULL AND price > 0 AND is_valid = 1
            ORDER BY price ASC
            """,
            (request.requirement_id,),
        )
        all_responses = await all_responses_cursor.fetchall()

    # Compute market stats from real data
    competing_prices = [r["price"] for r in all_responses if r["vendor_id"] != vendor_id]
    best_competing_price = min(competing_prices) if competing_prices else None

    all_valid_prices = [r["price"] for r in all_responses]
    market_avg = sum(all_valid_prices) / len(all_valid_prices) if all_valid_prices else None

    # If no responses yet, fall back to frontend-provided value (for demo/seed data)
    if best_competing_price is None:
        best_competing_price = request.best_competing_price

    vendor_name = vendor["name"]
    item_name = requirement["item"]

    # Build a rich, data-driven prompt
    prompt_parts = [
        f"Generate a short professional negotiation WhatsApp message to a vendor named {vendor_name}",
        f"for {request.quantity} {request.unit} of {item_name}.",
    ]

    if vendor_quoted_price:
        prompt_parts.append(
            f"They previously quoted Rs.{vendor_quoted_price} per {request.unit}."
        )

    if best_competing_price and best_competing_price > 0:
        prompt_parts.append(
            f"We have a competing quote of Rs.{best_competing_price} per {request.unit}."
        )

    if market_avg:
        prompt_parts.append(
            f"The market average for this item is Rs.{market_avg:.2f} per {request.unit}."
        )

    prompt_parts.extend([
        "Ask them to match or beat the best competing price.",
        "Mention that this is a regular bulk order and we are comparing multiple vendors.",
        "Keep it under 4 sentences. Be polite but firm and direct.",
        "Do not add any preamble or explanation. Output only the message itself.",
    ])

    prompt = "\n".join(prompt_parts)

    # Fallback message (works even if LLM is down)
    fallback_lines = [
        f"Namaste {vendor_name} ji,",
        f"We are procuring {request.quantity} {request.unit} of {item_name} and comparing quotes from multiple vendors.",
    ]
    if best_competing_price and best_competing_price > 0:
        fallback_lines.append(
            f"We have received a competing quote of Rs.{best_competing_price}/{request.unit}."
        )
    fallback_lines.extend([
        "Can you offer your best price for this regular bulk order?",
        "Please share your most competitive rate for prompt confirmation.",
    ])
    fallback_message = "\n".join(fallback_lines)

    def _fallback(_: str) -> dict:
        return {"message": fallback_message}

    try:
        llm_result = await call_llm(prompt, fallback_fn=_fallback)
        generated_message = str((llm_result or {}).get("message") or fallback_message).strip()
    except Exception:
        generated_message = fallback_message

    async with get_db(write=True) as db:
        await db.execute("BEGIN")
        try:
            await db.execute(
                """
                INSERT INTO negotiations
                (vendor_id, requirement_id, generated_message, status, vendor_counter_price, final_price)
                VALUES (?, ?, ?, 'message_sent', NULL, NULL)
                """,
                (vendor_id, request.requirement_id, generated_message),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return {
        "vendor_id": vendor_id,
        "message": generated_message,
        "status": "message_sent",
        "vendor_quoted_price": vendor_quoted_price,
        "best_competing_price": best_competing_price,
        "market_avg": market_avg,
    }


@router.get("/negotiations/{requirement_id}")
async def get_negotiations(requirement_id: int):
    """List negotiation status rows for a requirement."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT n.id, n.vendor_id, v.name AS vendor_name, n.status,
                   n.generated_message, n.vendor_counter_price, n.final_price
            FROM negotiations n
            JOIN vendors v ON n.vendor_id = v.id
            WHERE n.requirement_id = ?
            ORDER BY n.id
            """,
            (requirement_id,),
        )
        rows = await cursor.fetchall()

    return [
        {
            "vendor_id": int(row["vendor_id"]),
            "vendor_name": row["vendor_name"],
            "status": row["status"],
            "generated_message": row["generated_message"],
            "vendor_counter_price": row["vendor_counter_price"],
            "final_price": row["final_price"],
        }
        for row in rows
    ]


@router.get("/best-deal/{requirement_id}")
async def get_best_deal(requirement_id: int):
    """Return the best accepted negotiated deal for a requirement."""
    async with get_db() as db:
        deal_cursor = await db.execute(
            """
            SELECT n.vendor_id, v.name AS vendor_name, n.final_price
            FROM negotiations n
            JOIN vendors v ON n.vendor_id = v.id
            WHERE n.requirement_id = ?
              AND (n.status = 'accepted' OR n.final_price IS NOT NULL)
              AND n.final_price IS NOT NULL
            ORDER BY n.final_price ASC
            LIMIT 1
            """,
            (requirement_id,),
        )
        deal = await deal_cursor.fetchone()

        if deal:
            market_cursor = await db.execute(
                """
                SELECT avg_price
                FROM procurement_results
                WHERE requirement_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (requirement_id,),
            )
            market_row = await market_cursor.fetchone()
            avg_market_price = market_row["avg_price"] if market_row else None

            response_cursor = await db.execute(
                """
                SELECT price
                FROM vendor_responses
                WHERE requirement_id = ? AND vendor_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (requirement_id, deal["vendor_id"]),
            )
            response_row = await response_cursor.fetchone()
            original_quoted_price = response_row["price"] if response_row else None

            savings_vs_market = None
            if avg_market_price:
                savings_vs_market = round(
                    ((avg_market_price - deal["final_price"]) / avg_market_price) * 100,
                    2,
                )

            return {
                "status": "deal_found",
                "vendor_name": deal["vendor_name"],
                "final_price": deal["final_price"],
                "original_quoted_price": original_quoted_price,
                "savings_vs_market": savings_vs_market,
            }

        count_cursor = await db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('message_sent', 'counter_received') THEN 1 ELSE 0 END) AS active
            FROM negotiations
            WHERE requirement_id = ?
            """,
            (requirement_id,),
        )
        count_row = await count_cursor.fetchone()

    total = int(count_row["total"] or 0)
    if total:
        active = int(count_row["active"] or 0)
        return {
            "status": "negotiation_in_progress",
            "message": (
                f"Negotiation in progress. {active} vendor(s) contacted, "
                "awaiting responses."
            ),
        }

    return {
        "status": "not_started",
        "message": "No negotiations initiated yet.",
    }


@router.get("/metrics")
async def get_metrics():
    """Return summary metrics for the dashboard."""
    async with get_db() as db:
        total_cursor = await db.execute("SELECT COUNT(*) AS count FROM requirements")
        total_row = await total_cursor.fetchone()

        savings_cursor = await db.execute(
            "SELECT AVG(savings_pct) AS avg_savings_percent FROM procurement_results"
        )
        savings_row = await savings_cursor.fetchone()

        success_cursor = await db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('roi_ready', 'complete', 'completed') THEN 1 ELSE 0 END) AS successful
            FROM requirements
            """
        )
        success_row = await success_cursor.fetchone()

        vendors_cursor = await db.execute("SELECT COUNT(*) AS count FROM vendors")
        vendors_row = await vendors_cursor.fetchone()

    total_requirements = int(total_row["count"] or 0)
    successful = int(success_row["successful"] or 0)
    total_for_success = int(success_row["total"] or 0)
    success_rate = (
        (successful / total_for_success) * 100
        if total_for_success
        else 0.0
    )

    return {
        "total_requirements": total_requirements,
        "avg_savings_percent": float(savings_row["avg_savings_percent"] or 0),
        "success_rate": success_rate,
        "total_vendors_discovered": int(vendors_row["count"] or 0),
    }

