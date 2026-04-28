from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.procurement as procurement_routes


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(procurement_routes.router, prefix="/api")
    return TestClient(app)


def test_calculate_roi_returns_http_500_for_partial_result(monkeypatch):
    async def fake_get_requirement(requirement_id: int):
        return {
            "id": requirement_id,
            "item": "steel rods",
            "quantity": 10,
            "unit": "kg",
            "current_price": 100,
        }

    async def fake_get_all_responses(requirement_id: int):
        return [{"id": 1}]

    async def fake_run_roi_analysis(**kwargs):
        return {
            "requirement_id": kwargs["requirement_id"],
            "status": "partial",
            "failed_stage": "pricing",
            "error": "benchmark failed",
        }

    monkeypatch.setattr(procurement_routes, "get_requirement", fake_get_requirement)
    monkeypatch.setattr(procurement_routes, "get_all_responses", fake_get_all_responses)
    monkeypatch.setattr(procurement_routes, "run_roi_analysis", fake_run_roi_analysis)

    client = _build_client()
    response = client.post("/api/roi/1")

    assert response.status_code == 500
    assert response.json()["detail"]["status"] == "partial"
    assert response.json()["detail"]["failed_stage"] == "pricing"
