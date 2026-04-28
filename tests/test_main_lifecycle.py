import asyncio

from fastapi.testclient import TestClient

import main


def test_lifespan_schedules_and_cancels_maintenance(monkeypatch):
    events: list[str] = []
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_init_db():
        events.append("init")

    async def fake_close_db():
        events.append("close")

    async def fake_maintenance_loop():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(main, "init_db", fake_init_db)
    monkeypatch.setattr(main, "close_db", fake_close_db)
    monkeypatch.setattr(main, "_maintenance_loop", fake_maintenance_loop)

    with TestClient(main.app):
        assert started.is_set()
        assert hasattr(main.app.state, "maintenance_task")
        assert not main.app.state.maintenance_task.done()

    assert cancelled.is_set()
    assert events == ["init", "close"]
