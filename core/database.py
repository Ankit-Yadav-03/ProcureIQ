"""
Database layer — SQLite with aiosqlite
All tables: requirements, vendors, outreach_log, vendor_responses, procurement_results
"""

import aiosqlite
import logging

logger = logging.getLogger(__name__)

DB_PATH = "data/procurement.db"


async def get_db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
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
                status TEXT DEFAULT 'pending'
            )
        """)

        await db.execute("""
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
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS outreach_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER REFERENCES vendors(id),
                requirement_id INTEGER REFERENCES requirements(id),
                message_text TEXT NOT NULL,
                channel TEXT DEFAULT 'whatsapp',
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'sent',
                batch_number INTEGER DEFAULT 1
            )
        """)

        await db.execute("""
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
                extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                confidence TEXT DEFAULT 'medium',
                is_valid INTEGER DEFAULT 1
            )
        """)

        await db.execute("""
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
        """)

        await db.execute("""
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
        """)

        await db.commit()
        logger.info("Database initialized successfully.")
