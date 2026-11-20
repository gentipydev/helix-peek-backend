"""The connection pool for the database.

Deliberately sync psycopg rather than asyncpg. Every route is a sync ``def``,
which FastAPI hands to a threadpool; a sync pool used from that same thread
keeps one concurrency model in the service instead of two.

The pool is optional on purpose. ``DATABASE_URL`` unset means no pool, and the
routes that read the database answer 503 rather than fail to boot -- so a
database that is missing, asleep or unreachable is reported, not fatal.
``/health/db`` is where that condition is made visible.

``open=False`` plus an explicit ``open()`` in the lifespan keeps a bad
connection string from being dialled at import time, where the failure would
crash the worker before it could serve the endpoint that explains it.
"""

from typing import Optional

from psycopg_pool import ConnectionPool

from .config import settings

# One pool per worker process, created at startup. None when unconfigured.
pool: Optional[ConnectionPool] = None


def open_pool() -> None:
    """Create the pool, if a database is configured. Safe to call twice."""
    global pool
    if pool is not None or not settings.database_url:
        return
    pool = ConnectionPool(
        settings.database_url,
        min_size=0,
        max_size=settings.database_pool_max_size,
        timeout=settings.database_timeout_seconds,
        # A free instance is 0.1 CPU; waiting on a dead host should fail fast
        # rather than pile up connections behind a stalled TCP handshake.
        kwargs={"connect_timeout": int(settings.database_timeout_seconds)},
        open=False,
    )
    pool.open()


def close_pool() -> None:
    """Release every connection at shutdown."""
    global pool
    if pool is None:
        return
    pool.close()
    pool = None


def server_version() -> str:
    """Round-trip one query, returning what the server says it is.

    Raises whatever psycopg raises; the caller decides how to report it.
    """
    if pool is None:
        raise RuntimeError("DATABASE_URL is not set.")
    with pool.connection() as conn:
        return conn.execute("select version()").fetchone()[0]
