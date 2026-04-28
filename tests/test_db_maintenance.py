import aiosqlite
import pytest
import pytest_asyncio

import core.db as db_module
from core.db_maintenance import run_maintenance


@pytest_asyncio.fixture
async def in_memory_db(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    await db_module._ensure_schema(conn)
    await conn.commit()

    monkeypatch.setattr(db_module, "_CONNECTION", conn)
    yield conn
    monkeypatch.setattr(db_module, "_CONNECTION", None)
    await conn.close()


@pytest.mark.asyncio
async def test_run_maintenance_deletes_stale_partial_requirements(in_memory_db):
    await in_memory_db.execute(
        """
        INSERT INTO requirements
        (raw_input, item, quantity, location, current_price, unit, category, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '-90 days'))
        """,
        (
            "Need steel",
            "steel",
            10,
            "delhi",
            100,
            "kg",
            "metals",
            "partial",
        ),
    )
    await in_memory_db.execute(
        """
        INSERT INTO requirements
        (raw_input, item, quantity, location, current_price, unit, category, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '-90 days'))
        """,
        (
            "Need copper",
            "copper",
            5,
            "mumbai",
            200,
            "kg",
            "metals",
            "complete",
        ),
    )
    await in_memory_db.commit()

    await run_maintenance()

    cursor = await in_memory_db.execute(
        "SELECT status FROM requirements ORDER BY id"
    )
    rows = await cursor.fetchall()

    assert [row["status"] for row in rows] == ["complete"]
