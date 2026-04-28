import hashlib
import hmac
import json

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

import core.db as db_module
from routes.outreach import router
from core.config import settings


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


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


def _signed_body(payload: dict, secret: str) -> tuple[bytes, str]:
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return body, signature


async def _insert_requirement_and_vendor(conn, *, phone: str) -> tuple[int, int]:
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
            f"Vendor {requirement_id}",
            phone,
            "delhi",
            "manual",
            None,
            None,
            "pending",
        ),
    )
    await conn.commit()
    return requirement_id, vendor_cursor.lastrowid


def test_verify_whatsapp_webhook_returns_challenge():
    client = _build_client()

    response = client.get(
        "/api/outreach/webhook",
        params={
            "hub.verify_token": "procurement_webhook_token",
            "hub.challenge": "123",
        },
    )

    assert response.status_code == 200
    assert response.json() == 123


def test_verify_whatsapp_webhook_rejects_invalid_token():
    client = _build_client()

    response = client.get(
        "/api/outreach/webhook",
        params={
            "hub.verify_token": "wrong-token",
            "hub.challenge": "123",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid verify token"}


def test_whatsapp_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "test-secret")
    client = _build_client()
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{"from": "919876543210", "text": {"body": "Rs 450 per kg"}}]}}]}]
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/api/outreach/webhook",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid webhook signature"}


@pytest.mark.asyncio
async def test_whatsapp_webhook_rejects_ambiguous_phone_match(in_memory_db, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "test-secret")
    await _insert_requirement_and_vendor(in_memory_db, phone="+919876543210")
    await _insert_requirement_and_vendor(in_memory_db, phone="+919876543210")

    client = _build_client()
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{"from": "919876543210", "text": {"body": "Rs 450 per kg"}}]}}]}]
    }
    body, signature = _signed_body(payload, "test-secret")

    response = client.post(
        "/api/outreach/webhook",
        content=body,
        headers={
            "content-type": "application/json",
            "X-Hub-Signature-256": signature,
        },
    )

    cursor = await in_memory_db.execute("SELECT COUNT(*) AS count FROM vendor_responses")
    row = await cursor.fetchone()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ambiguous_vendor_match",
        "phone": "+919876543210",
    }
    assert row["count"] == 0


@pytest.mark.asyncio
async def test_whatsapp_webhook_stores_response_for_unique_phone(in_memory_db, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "test-secret")
    requirement_id, vendor_id = await _insert_requirement_and_vendor(
        in_memory_db,
        phone="+919876543210",
    )

    client = _build_client()
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{"from": "919876543210", "text": {"body": "Rs 450 per kg"}}]}}]}]
    }
    body, signature = _signed_body(payload, "test-secret")

    response = client.post(
        "/api/outreach/webhook",
        content=body,
        headers={
            "content-type": "application/json",
            "X-Hub-Signature-256": signature,
        },
    )

    cursor = await in_memory_db.execute(
        "SELECT requirement_id, vendor_id, raw_message FROM vendor_responses"
    )
    row = await cursor.fetchone()

    assert response.status_code == 200
    assert response.json()["status"] == "stored"
    assert row["requirement_id"] == requirement_id
    assert row["vendor_id"] == vendor_id
    assert row["raw_message"] == "Rs 450 per kg"
