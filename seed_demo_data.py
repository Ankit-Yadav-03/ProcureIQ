from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.config import settings


BASE_DIR = Path(__file__).resolve().parent


def _db_path() -> Path:
    configured = Path(settings.DB_PATH)
    if not configured.is_absolute():
        configured = BASE_DIR / configured
    return configured


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
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
        );

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
        );

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
        );

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
        );

        CREATE TABLE IF NOT EXISTS negotiations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            requirement_id INTEGER NOT NULL,
            generated_message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            vendor_counter_price REAL,
            final_price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def main() -> None:
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        _ensure_schema(conn)
        existing = conn.execute(
            """
            SELECT id FROM requirements
            WHERE item = ? AND quantity = ? AND unit = ? AND location = ?
            """,
            ("Steel Rod", 5000, "kg", "Delhi"),
        ).fetchone()
        if existing:
            requirement_id = existing["id"]
            print("Demo data already exists.")
            vendor_rows_existing = conn.execute(
                """
                SELECT id FROM vendors
                WHERE requirement_id = ?
                ORDER BY id
                LIMIT 5
                """
                ,
                (requirement_id,),
            ).fetchall()
            vendor_ids = [row["id"] for row in vendor_rows_existing]
        else:
            cursor = conn.execute(
                """
                INSERT INTO requirements
                (raw_input, item, quantity, unit, location, current_price, category, status, last_successful_stage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Steel Rod 5000kg Delhi 500000",
                    "Steel Rod",
                    5000,
                    "kg",
                    "Delhi",
                    100.0,
                    "metals",
                    "roi_ready",
                    "reporting",
                ),
            )
            requirement_id = cursor.lastrowid

            vendor_rows = [
                ("Sharma Steel Pvt Ltd", "9876543210", "Narela Industrial Area", "indiamart", 4.7),
                ("Delhi Iron Works", "9876543211", "Okhla Phase II", "tradeindia", 4.4),
                ("Bharat Steel Traders", "9876543212", "Wazirpur Industrial Area", "indiamart", 4.5),
                ("Metro Metal Supply Co", "9876543213", "Mayapuri Phase I", "tradeindia", 4.1),
                ("Northern Alloy House", "9876543214", "Bawana Industrial Area", "indiamart", 4.3),
                ("Capital Steel Depot", "9876543215", "Rohtak Road Industrial Area", "tradeindia", 3.9),
                ("Hindustan Rod Suppliers", "9876543216", "Lawrence Road", "indiamart", 4.6),
                ("R.K. Industrial Steels", "9876543217", "Patparganj Industrial Area", "tradeindia", 3.8),
            ]
            vendor_ids: list[int] = []
            for name, phone, location, source, rating in vendor_rows:
                vendor_cursor = conn.execute(
                    """
                    INSERT INTO vendors
                    (requirement_id, name, phone, location, source, profile_url, rating, contact_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        requirement_id,
                        name,
                        phone,
                        location,
                        source,
                        f"https://example.com/vendors/{phone}",
                        rating,
                        "responded" if len(vendor_ids) < 5 else "discovered",
                    ),
                )
                vendor_ids.append(vendor_cursor.lastrowid)

            response_rows = [
                (0, "We can supply steel rod at Rs 88 per kg, delivery in 3 days.", 88.0, 3, "high"),
                (1, "Best rate Rs 92/kg plus GST. Dispatch within 4 days.", 92.0, 4, "high"),
                (2, "Current offer is Rs 96 per kg for 5000 kg quantity.", 96.0, 5, "medium"),
                (3, "Steel rod available at Rs 101 per kg, payment advance.", 101.0, 6, "medium"),
                (4, "Quote: Rs 105 per kg, delivery 7 days, GST extra.", 105.0, 7, "medium"),
            ]
            for vendor_index, message, price, delivery_days, confidence in response_rows:
                conn.execute(
                    """
                    INSERT INTO vendor_responses
                    (vendor_id, requirement_id, raw_message, price, delivery_days, gst_included,
                     min_quantity, payment_terms, confidence, is_valid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vendor_ids[vendor_index],
                        requirement_id,
                        message,
                        price,
                        delivery_days,
                        0,
                        5000,
                        "advance",
                        confidence,
                        1,
                    ),
                )

            report_json = json.dumps(
                {
                    "executive_report": (
                        "Best quote is Rs 88.00/kg from Sharma Steel Pvt Ltd, "
                        "with 10.7% savings versus the market average."
                    )
                }
            )
            conn.execute(
                """
                INSERT INTO procurement_results
                (requirement_id, best_vendor_id, best_price, avg_price, min_price, median_price,
                 total_savings, savings_pct, vendor_count, response_count, confidence, report_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    requirement_id,
                    vendor_ids[0],
                    88.0,
                    98.5,
                    88.0,
                    96.0,
                    52500.0,
                    10.7,
                    5,
                    5,
                    "high",
                    report_json,
                ),
            )

            print(f"Demo data seeded successfully. Requirement ID: {requirement_id}")

        existing_negotiations = conn.execute(
            "SELECT COUNT(*) FROM negotiations WHERE requirement_id = ?",
            (requirement_id,),
        ).fetchone()[0]
        if existing_negotiations > 0:
            print("Negotiation demo data already exists. Skipping.")
        else:
            negotiation_message = (
                "We have received competing quotes at Rs.88/kg for this requirement. "
                "Can you offer a better rate for 5000kg with delivery within 7 days?"
            )
            negotiation_rows = [
                ("accepted", 90.0, 88.0),
                ("counter_received", 94.0, None),
                ("message_sent", None, None),
                ("message_sent", None, None),
                ("pending", None, None),
            ]
            for vendor_id, (status, counter_price, final_price) in zip(
                vendor_ids[:5],
                negotiation_rows,
            ):
                conn.execute(
                    """
                    INSERT INTO negotiations
                    (vendor_id, requirement_id, generated_message, status, vendor_counter_price, final_price)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vendor_id,
                        requirement_id,
                        negotiation_message,
                        status,
                        counter_price,
                        final_price,
                    ),
                )
            print("Negotiation demo data seeded.")

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
