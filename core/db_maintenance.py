from __future__ import annotations

import asyncio
import os
from pathlib import Path

from core.config import settings
from core.db import DB_PATH, get_db
from core.logger import get_logger


logger = get_logger(__name__)
_MAINTENANCE_LOCK = asyncio.Lock()


def _db_size() -> int:
    candidate_paths = [Path(settings.DB_PATH), DB_PATH]
    for path in candidate_paths:
        if path.exists():
            return os.path.getsize(path)
    return 0


async def run_maintenance() -> None:
    if _MAINTENANCE_LOCK.locked():
        logger.info("Maintenance already running, skipping")
        return

    async with _MAINTENANCE_LOCK:
        size_before = _db_size()
        logger.info("DB maintenance starting, size before=%s bytes", size_before)

        async with get_db(write=True) as db:
            await db.execute("BEGIN")
            try:
                responses_cursor = await db.execute(
                    """
                    DELETE FROM vendor_responses
                    WHERE received_at < datetime('now', ?)
                    """,
                    (f"-{settings.MAINTENANCE_RETENTION_RESPONSES_DAYS} days",),
                )
                outreach_cursor = await db.execute(
                    """
                    DELETE FROM outreach_log
                    WHERE created_at < datetime('now', ?)
                    """,
                    (f"-{settings.MAINTENANCE_RETENTION_OUTREACH_DAYS} days",),
                )
                failed_cursor = await db.execute(
                    """
                    DELETE FROM requirements
                    WHERE status IN ('failed', 'discovery_error', 'no_vendors_found', 'partial')
                      AND created_at < datetime('now', ?)
                    """,
                    (f"-{settings.MAINTENANCE_RETENTION_FAILED_DAYS} days",),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

            await db.execute("VACUUM")
            await db.commit()

        size_after = _db_size()
        logger.info(
            "DB maintenance complete: vendor_responses=%s outreach_log=%s requirements=%s size_after=%s bytes",
            responses_cursor.rowcount,
            outreach_cursor.rowcount,
            failed_cursor.rowcount,
            size_after,
        )


async def _maintenance_loop() -> None:
    while True:
        await run_maintenance()
        await asyncio.sleep(86400)
