"""FastAPI application — Memee Dashboard & API."""

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from memee.api.routes.dashboard import router as dashboard_router
from memee.api.routes.api_v1 import router as api_router

logger = logging.getLogger("memee.api")

app = FastAPI(
    title="Memee",
    description="Institutional Memory for AI Agent Companies",
    version="0.1.0",
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Global catch-all: log full traceback server-side, return clean JSON.

    Without this, any engine exception bubbling out of a route returns a
    500 with the entire Python traceback as the response body — leaking
    internals to the client.
    """
    logger.error(
        "unhandled in %s: %s\n%s",
        request.url.path,
        exc,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": str(exc)},
    )


app.include_router(dashboard_router)
app.include_router(api_router, prefix="/api/v1")
