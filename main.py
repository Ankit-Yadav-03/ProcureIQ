"""
ProcureIQ - Main Entry Point
Real vendor negotiation engine for B2B procurement
"""

import sys
import asyncio
import logging

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.db import close_db, init_db
from core.db_maintenance import _maintenance_loop
from routes.procurement import router as procurement_router
from routes.vendors import router as vendors_router
from routes.outreach import router as outreach_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database...")
    await init_db()
    app.state.maintenance_task = asyncio.create_task(_maintenance_loop())
    logger.info("ProcureIQ started.")
    yield
    maintenance_task = getattr(app.state, "maintenance_task", None)
    if maintenance_task is not None:
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
    await close_db()
    logger.info("Shutting down.")


app = FastAPI(
    title="ProcureIQ",
    description="Real vendor negotiation engine — no fake data, no simulation.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(procurement_router, prefix="/api", tags=["Procurement"])
app.include_router(vendors_router, prefix="/api", tags=["Vendors"])
app.include_router(outreach_router, prefix="/api", tags=["Outreach"])

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-procurement-agent"}


# Serve frontend files at root so relative paths (e.g. style.css) work
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
