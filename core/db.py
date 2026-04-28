from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from core.config import settings
from core.logger import get_logger


logger = get_logger(__name__)
_CONNECTION: aiosqlite.Connection | None = None
_INIT_LOCK = asyncio.Lock()
WRITE_LOCK = asyncio.Lock()
BASE_DIR = Path(__file__).resolve().parent
LEGACY_DB_PATH = BASE_DIR / "data" / "procurement.db"


def _resolve_db_path() -> Path:
    configured = Path(settings.DB_PATH)
    if not configured.is_absolute():
        configured = BASE_DIR / configured
    if configured.exists() or not LEGACY_DB_PATH.exists():
        return configured
    logger.info(
        "Configured DB path %s not found, using existing legacy DB at %s",
        configured,
        LEGACY_DB_PATH,
    )
    return LEGACY_DB_PATH


DB_PATH = _resolve_db_path()


async def _ensure_column(
    conn: aiosqlite.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    existing = {row["name"] for row in rows}
    if column not in existing:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def _ensure_insert_timestamp_trigger(
    conn: aiosqlite.Connection,
    *,
    table: str,
    column: str,
) -> None:
    trigger_name = f"trg_{table}_{column}_default"
    await conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS {trigger_name}
        AFTER INSERT ON {table}
        FOR EACH ROW
        WHEN NEW.{column} IS NULL
        BEGIN
            UPDATE {table}
            SET {column} = CURRENT_TIMESTAMP
            WHERE id = NEW.id;
        END
        """
    )


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_input TEXT NOT NULL,
            item TEXT NOT NULL,
            quantity REAL NOT NULL,
            location TEXT NOT NULL,
            current_price REAL NOT NULL,
            unit TEXT DEFAULT 'kg',
            category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending',
            last_successful_stage TEXT,
            error_message TEXT
        )
        """
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_id INTEGER REFERENCES requirements(id),
            name TEXT NOT NULL,
            phone TEXT,
            location TEXT,
            source TEXT,
            profile_url TEXT,
            rating REAL,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            contact_status TEXT DEFAULT 'pending'
        )
        """
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outreach_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER REFERENCES vendors(id),
            requirement_id INTEGER REFERENCES requirements(id),
            message_text TEXT NOT NULL,
            channel TEXT DEFAULT 'whatsapp',
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'sent',
            batch_number INTEGER DEFAULT 1
        )
        """
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER REFERENCES vendors(id),
            requirement_id INTEGER REFERENCES requirements(id),
            raw_message TEXT NOT NULL,
            price REAL,
            delivery_days INTEGER,
            gst_included INTEGER DEFAULT 0,
            min_quantity REAL,
            payment_terms TEXT,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confidence TEXT DEFAULT 'medium',
            is_valid INTEGER DEFAULT 1
        )
        """
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS procurement_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_id INTEGER REFERENCES requirements(id),
            best_vendor_id INTEGER REFERENCES vendors(id),
            best_price REAL,
            avg_price REAL,
            min_price REAL,
            median_price REAL,
            total_savings REAL,
            savings_pct REAL,
            vendor_count INTEGER,
            response_count INTEGER,
            confidence TEXT,
            report_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS negotiations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL REFERENCES vendors(id),
            requirement_id INTEGER NOT NULL REFERENCES requirements(id),
            generated_message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            vendor_counter_price REAL,
            final_price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    await _ensure_column(conn, "requirements", "last_successful_stage", "TEXT")
    await _ensure_column(conn, "requirements", "error_message", "TEXT")
    await _ensure_column(conn, "outreach_log", "created_at", "TIMESTAMP")
    await _ensure_column(conn, "vendor_responses", "received_at", "TIMESTAMP")
    await conn.execute(
        """
        UPDATE outreach_log
        SET created_at = COALESCE(created_at, sent_at, CURRENT_TIMESTAMP)
        WHERE created_at IS NULL
        """
    )
    await conn.execute(
        """
        UPDATE vendor_responses
        SET received_at = COALESCE(received_at, extracted_at, CURRENT_TIMESTAMP)
        WHERE received_at IS NULL
        """
    )
    await _ensure_insert_timestamp_trigger(
        conn,
        table="outreach_log",
        column="created_at",
    )
    await _ensure_insert_timestamp_trigger(
        conn,
        table="vendor_responses",
        column="received_at",
    )

    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vendors_requirement
        ON vendors(requirement_id)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_responses_vendor
        ON vendor_responses(vendor_id)
        """
    )
    await conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vendors_phone_req
        ON vendors(requirement_id, phone)
        """
    )


async def init_db() -> None:
    await _get_connection()


async def _get_connection() -> aiosqlite.Connection:
    global _CONNECTION
    if _CONNECTION is not None:
        return _CONNECTION

    async with _INIT_LOCK:
        if _CONNECTION is not None:
            return _CONNECTION

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await _ensure_schema(conn)
        await conn.commit()
        _CONNECTION = conn
        logger.info("Database initialized at %s", DB_PATH)
        return conn


async def close_db() -> None:
    global _CONNECTION
    if _CONNECTION is not None:
        await _CONNECTION.close()
        _CONNECTION = None


@asynccontextmanager
async def get_db(*, write: bool = False):
    conn = await _get_connection()
    if write:
        async with WRITE_LOCK:
            yield conn
    else:
        yield conn
