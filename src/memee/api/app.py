"""FastAPI application — Memee Dashboard & API."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from memee.api.routes.dashboard import router as dashboard_router
from memee.api.routes.api_v1 import router as api_router

app = FastAPI(
    title="Memee",
    description="Institutional Memory for AI Agent Companies",
    version="0.1.0",
)

app.include_router(dashboard_router)
app.include_router(api_router, prefix="/api/v1")
