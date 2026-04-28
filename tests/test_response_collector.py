import aiosqlite
import pytest
import pytest_asyncio

import core.db as db_module
from services.response_collector import submit_vendor_response


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


async def _insert_requirement_and_vendor(conn: aiosqlite.Connection) -> tuple[int, int]:
    requirement_cursor = await conn.execute(
        """
        INSERT INTO requirements
        (raw_input, item, quantity, location, current_price, unit, category, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Need 10 kg copper wire in Delhi at Rs 500 per kg",
            "copper wire",
            10,
            "delhi",
            500,
            "kg",
            "metals",
            "outreach_ready",
        ),
    )
    requirement_id = requirement_cursor.lastrowid

    vendor_cursor = await conn.execute(
        """
        INSERT INTO vendors
        (requirement_id, name, phone, location, source, profile_url, rating, contact_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            requirement_id,
            "Vendor One",
            "+919876543210",
            "delhi",
            "manual",
            None,
            None,
            "pending",
        ),
    )
    await conn.commit()
    return requirement_id, vendor_cursor.lastrowid


@pytest.mark.asyncio
async def test_duplicate_rejected(in_memory_db, caplog):
    requirement_id, vendor_id = await _insert_requirement_and_vendor(in_memory_db)

    first_response_id = await submit_vendor_response(
        vendor_id=vendor_id,
        requirement_id=requirement_id,
        raw_message="Rs 450 per kg, delivery in 2 days",
    )
    second_response_id = await submit_vendor_response(
        vendor_id=vendor_id,
        requirement_id=requirement_id,
        raw_message="Rs 450 per kg, delivery in 2 days",
    )

    cursor = await in_memory_db.execute(
        "SELECT COUNT(*) AS count FROM vendor_responses WHERE vendor_id = ?",
        (vendor_id,),
    )
    row = await cursor.fetchone()

    assert first_response_id is not None
    assert second_response_id is None
    assert row["count"] == 1
    assert "Duplicate response skipped" in caplog.text


@pytest.mark.asyncio
async def test_invalid_vendor_id(in_memory_db):
    requirement_id, _ = await _insert_requirement_and_vendor(in_memory_db)

    with pytest.raises(ValueError, match="Vendor 999 not found"):
        await submit_vendor_response(
            vendor_id=999,
            requirement_id=requirement_id,
            raw_message="Rs 450 per kg",
        )


@pytest.mark.asyncio
async def test_empty_message(in_memory_db):
    requirement_id, vendor_id = await _insert_requirement_and_vendor(in_memory_db)

    with pytest.raises(ValueError, match="raw_message cannot be empty"):
        await submit_vendor_response(
            vendor_id=vendor_id,
            requirement_id=requirement_id,
            raw_message="   ",
        )
