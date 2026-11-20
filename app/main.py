"""FastAPI application wiring."""

import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional, Tuple

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings
from .guard import Guard
from .router import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Hold the database pool open for the life of the worker.

    Opening here rather than at import time means a wrong or unreachable
    DATABASE_URL surfaces on /health/db instead of killing the worker during
    startup, when nothing can report why.
    """
    db.open_pool()
    try:
        yield
    finally:
        db.close_pool()


# No /docs, /redoc or /openapi.json: the service answers one app, whose
# routes are in its repository, and a page that maps every route for anyone
# who asks is bytes sent for no reader.
app = FastAPI(
    title="Helix Peek Backend",
    description="The catalog, its tracks, and builds on demand.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Each request's body and rate (`guard.py`). Added before CORS, so it runs
# inside it: a refusal still carries CORS's headers to a browser.
app.add_middleware(Guard)

# Any origin, and never credentials: the service has no cookies or accounts,
# so a page elsewhere can read only what anyone can.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict:
    """Liveness check that does not spend an NCBI request."""
    return {"status": "ok"}


# How long one answer of `/health/db` stands: the keepalive asks once a day,
# and a flood of asks costs one round trip in this long, not one each.
HEALTH_DB_SECONDS = 30.0

_health_lock = threading.Lock()
_health_said: Optional[Tuple[float, dict]] = None


def forget_health() -> None:
    """Drop the standing answer of `/health/db` (for tests)."""
    global _health_said
    with _health_lock:
        _health_said = None


def _health_db_now() -> dict:
    if not settings.database_url:
        return {"database": "unconfigured"}
    try:
        return {"database": "ok", "server": db.server_version()}
    except Exception as exc:
        logger.exception("Database unreachable")
        return {"database": "error", "error": type(exc).__name__}


@app.get("/health/db")
def health_db() -> dict:
    """Readiness check for the database: one real round trip, at most every
    `HEALTH_DB_SECONDS`; within that, the last answer stands.

    Separate from /health so the liveness probe stays free. A failure here is
    reported, not raised, so the check itself always answers.

    The endpoint is public, and psycopg spells the host, port and user out in
    its connection errors -- so the message goes to the log, where operating
    the service can read it, and only the exception class comes back over HTTP.
    """
    global _health_said
    with _health_lock:
        now = time.monotonic()
        if _health_said is not None and now - _health_said[0] < HEALTH_DB_SECONDS:
            return dict(_health_said[1])
        said = _health_db_now()
        _health_said = (now, said)
        return dict(said)
