"""Application settings, loaded from the environment / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Optional


class Settings(BaseSettings):
    """Settings for the Helix Peek backend.

    ``ncbi_email`` has no default on purpose. NCBI rejects (and eventually
    blocks) traffic without a contact address, so an unset ``NCBI_EMAIL``
    should stop the app from booting rather than produce failures at request
    time. The service itself has not read NCBI since its gene route was
    retired (2026-11-20); the scripts and the resolver that import these
    settings still do.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ncbi_email: str

    # Unset, the catalog and every route that reads it is a 503. Supabase's
    # direct host (db.<ref>.supabase.co) resolves to IPv6 only, which Render
    # cannot reach, so this must be the pooler host and must carry
    # sslmode=require.
    database_url: Optional[str] = None

    database_pool_max_size: int = 4

    database_timeout_seconds: float = 10.0

    # The Supabase project, as the REST origin rather than the database host:
    # https://<ref>.supabase.co. Track blobs are served to clients from its
    # public storage endpoint, which they fetch directly -- this service builds
    # the URL and never proxies the bytes, so neither its memory nor its egress
    # is in the path of a 9 MB ClinVar snapshot.
    supabase_url: Optional[str] = None

    # There is deliberately no service_role key here. This service only ever
    # builds public storage URLs, and it reaches its rows through
    # DATABASE_URL, so it needs no Supabase credential at all -- `supabase_url`
    # above is a public project URL. The key that bypasses row level security
    # lives only in the environment of `tool/upload_tracks.py`, which runs from
    # a developer's machine. A deployed process that never holds it cannot leak
    # it.

    tracks_bucket: str = "tracks"

    models_bucket: str = "models"

    # Phase 6: a protein the catalog does not list, resolved on demand by the
    # resolver on Modal (`pipeline/resolver/`). A request is a row; the
    # resolver's schedule takes every queued one within five minutes, and this
    # URL -- the `wake` endpoint `modal deploy` prints -- starts it at once.
    # Unset, requests still resolve, on the schedule.
    modal_wake_url: Optional[str] = None

    # One of the Modal workspace's proxy auth tokens, which the wake endpoint
    # requires. Not a Supabase credential: it can start a sweep and nothing
    # else.
    modal_key: Optional[str] = None
    modal_secret: Optional[str] = None

    # The service has no auth, and a request is the one thing it does that
    # costs money (a GPU scores each protein), so a day's are capped: asks and
    # resumes alike, counted in `spend_ledger` (0015), where no discard can
    # take one back. 20 is the user's number of 2026-11-13; the default is it,
    # so the cap holds on a service whose environment does not say it.
    resolves_per_day: int = 20

    # The same, for one install (the app's own random id, `asker`): so one
    # phone, or a script, cannot take a whole day's builds. An ask with no id
    # counts against every other ask with none.
    resolves_per_install_per_day: int = 5

    # Proteins built on demand the project may hold, queued ones included.
    # Each is 1.3-2.8 MB in storage (measured 2026-11-13), and Supabase's free
    # project holds 1 GB, 147 MB of it the curated proteins'. A discard frees
    # its place.
    max_built_proteins: int = 200

    # Each client's requests a minute, and the whole service's (`guard.py`).
    # Generous for the app, which polls a build every few seconds and asks for
    # suggestions as its reader types; 0 turns a limit off.
    requests_per_minute: int = 240
    posts_per_minute: int = 30
    service_requests_per_minute: int = 3000

    # The largest request body the service reads. Every body it takes is a
    # small JSON object (a gene and an install's id).
    max_body_bytes: int = 16_384

    # Bytes the service may send in one UTC day, every answer's body and
    # headers counted (`guard.py`); past it, every route but the health checks
    # is a 503 until midnight UTC. Render's Hobby workspace includes 5 GB of
    # outbound bandwidth a month and bills each GB past it (or, with no card,
    # suspends the service for the rest of the month); 150 MB a day is 4.65 GB
    # in the longest month. The app's answers are kilobytes, and the tracks come
    # from Supabase's storage, never through here. Counted in this process, so a
    # restart starts the day's count again. 0 turns it off.
    response_bytes_per_day: int = 150_000_000


settings = Settings()
