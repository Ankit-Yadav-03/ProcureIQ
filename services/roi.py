from __future__ import annotations

import json
from typing import Optional

from core.db import get_db
from core.logger import get_logger
from services.cleaner import clean_responses
from services.extractor import extract_all_prices, get_extracted_prices
from services.llm_client import generate_final_report
from services.pricing import run_full_analysis


logger = get_logger(__name__)
PIPELINE_STAGES = (
    "parsing",
    "discovery",
    "outreach",
    "collection",
    "extraction",
    "cleaning",
    "pricing",
    "reporting",
)
STATUS_TO_STAGE = {
    "parsing_done": "parsing",
    "discovery_started": "parsing",
    "vendors_found": "discovery",
    "no_vendors_found": "discovery",
    "discovery_error": "discovery",
    "outreach_ready": "outreach",
    "partial": None,
    "complete": "reporting",
}


async def _get_last_successful_stage(requirement_id: int) -> str | None:
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT status, last_successful_stage
            FROM requirements
            WHERE id = ?
            """,
            (requirement_id,),
        )
        row = await cursor.fetchone()

    if not row:
        return None

    stage = row["last_successful_stage"]
    if stage in PIPELINE_STAGES:
        return stage

    return STATUS_TO_STAGE.get(row["status"])


async def _update_requirement_state(
    requirement_id: int,
    *,
    status: str,
    last_successful_stage: str | None,
    error_message: str | None,
) -> None:
    async with get_db(write=True) as db:
        await db.execute("BEGIN")
        try:
            await db.execute(
                """
                UPDATE requirements
                SET status = ?, last_successful_stage = ?, error_message = ?
                WHERE id = ?
                """,
                (status, last_successful_stage, error_message, requirement_id),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def run_roi_analysis(
    requirement_id: int,
    item: str,
    quantity: float,
    unit: str,
    current_price: float,
) -> dict:
    logger.info("Starting ROI analysis for requirement %s", requirement_id)

    last_successful_stage = await _get_last_successful_stage(requirement_id) or "outreach"
    current_stage = "collection"

    try:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM vendor_responses WHERE requirement_id = ?",
                (requirement_id,),
            )
            row = await cursor.fetchone()
        if not row or int(row["count"]) == 0:
            raise ValueError("No vendor responses received yet. Submit vendor replies first.")
        last_successful_stage = "collection"

        current_stage = "extraction"
        await extract_all_prices(requirement_id, item)
        extracted = await get_extracted_prices(requirement_id)
        if not extracted:
            raise ValueError("No valid extracted prices available for analysis.")
        last_successful_stage = "extraction"

        current_stage = "cleaning"
        clean, rejected = clean_responses(extracted)
        logger.info("Clean responses: %s, Rejected: %s", len(clean), len(rejected))
        if not clean:
            raise ValueError(f"All {len(rejected)} responses were invalid or outliers.")
        last_successful_stage = "cleaning"

        current_stage = "pricing"
        analysis = run_full_analysis(
            current_price=current_price,
            quantity=quantity,
            clean_responses=clean,
            unit=unit,
        )
        if "error" in analysis:
            raise ValueError(str(analysis["error"]))
        last_successful_stage = "pricing"

        current_stage = "reporting"
        best_vendor_response = analysis["best_vendor_response"]
        best_vendor_name = best_vendor_response.get("vendor_name", "Unknown Vendor")
        benchmark = analysis["benchmark"]
        roi = analysis["roi"]
        exec_report = await generate_final_report(
            item=item,
            quantity=quantity,
            unit=unit,
            current_price=current_price,
            best_price=roi["best_price"],
            benchmark=benchmark,
            best_vendor_name=best_vendor_name,
            response_count=benchmark["response_count"],
        )
        last_successful_stage = "reporting"

        report_json = json.dumps(
            {
                "analysis": analysis,
                "executive_report": exec_report,
                "clean_response_count": len(clean),
                "rejected_count": len(rejected),
            }
        )
        best_vendor_id = best_vendor_response.get("vendor_id")

        async with get_db(write=True) as db:
            await db.execute("BEGIN")
            try:
                cursor = await db.execute(
                    """
                    INSERT INTO procurement_results
                    (requirement_id, best_vendor_id, best_price, avg_price, min_price,
                     median_price, total_savings, savings_pct, vendor_count, response_count,
                     confidence, report_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        requirement_id,
                        best_vendor_id,
                        roi["best_price"],
                        benchmark["avg_price"],
                        benchmark["min_price"],
                        benchmark["median_price"],
                        roi["total_savings"],
                        roi["savings_pct"],
                        len(extracted),
                        benchmark["response_count"],
                        analysis["confidence"],
                        report_json,
                    ),
                )
                await db.execute(
                    """
                    UPDATE requirements
                    SET status = 'complete',
                        last_successful_stage = ?,
                        error_message = NULL
                    WHERE id = ?
                    """,
                    (last_successful_stage, requirement_id),
                )
                await db.commit()
                result_id = cursor.lastrowid
            except Exception:
                await db.rollback()
                raise

        logger.info("ROI result stored with ID: %s", result_id)
        return {
            "requirement_id": requirement_id,
            "item": item,
            "quantity": quantity,
            "unit": unit,
            "current_price": current_price,
            "best_price": roi["best_price"],
            "best_vendor_id": best_vendor_id,
            "best_vendor_name": best_vendor_name,
            "avg_price": benchmark["avg_price"],
            "min_price": benchmark["min_price"],
            "median_price": benchmark["median_price"],
            "total_savings": roi["total_savings"],
            "savings_pct": roi["savings_pct"],
            "annual_savings_estimate": roi["annual_savings_estimate"],
            "vendor_count": len(extracted),
            "response_count": benchmark["response_count"],
            "confidence": analysis["confidence"],
            "executive_report": exec_report,
            "clean_responses": clean,
            "rejected_responses": rejected,
            "status": "complete",
        }
    except Exception as exc:
        logger.exception(
            "ROI analysis failed at stage %s for requirement %s",
            current_stage,
            requirement_id,
        )
        try:
            await _update_requirement_state(
                requirement_id,
                status="partial",
                last_successful_stage=last_successful_stage,
                error_message=str(exc),
            )
        except Exception:
            logger.exception(
                "Failed to persist partial ROI state for requirement %s",
                requirement_id,
            )

        return {
            "requirement_id": requirement_id,
            "status": "partial",
            "last_successful_stage": last_successful_stage,
            "failed_stage": current_stage,
            "error": str(exc),
        }


async def get_latest_result(requirement_id: int) -> Optional[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM procurement_results
            WHERE requirement_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (requirement_id,),
        )
        row = await cursor.fetchone()

    if not row:
        return None

    result = dict(row)
    if result.get("report_json"):
        result["report_data"] = json.loads(result["report_json"])
    return result
