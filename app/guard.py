"""What every request is held to before a route sees it.

- Its body: none the service reads is more than a small JSON object (a gene
  and an install's id), so a body past `MAX_BODY_BYTES` is a 413, read no
  further. Without this a client could make the service hold any size of
  body in memory while it parses it.
- How often: each client `REQUESTS_PER_MINUTE` requests a minute, of which
  `POSTS_PER_MINUTE` may be POSTs, and the whole service
  `SERVICE_REQUESTS_PER_MINUTE`. Past one, a 429 with `Retry-After`. The
  health checks count against their client's minute, never the service's, so
  a flood elsewhere never fails the keep-alive, which pings them.
- How much: every answer's bytes, body and headers, refusals included, are
  counted for the UTC day, and past `RESPONSE_BYTES_PER_DAY` every route but
  the health checks is a 503 until midnight UTC. Render bills outbound bytes
  past the month's allowance (or suspends the service, with no card), and no
  minute limit bounds what many addresses at once can draw; this does.

There are no accounts, so a client is the address it asks from: Cloudflare's
`CF-Connecting-IP` where it is there, else the first address in
`X-Forwarded-For`, else the socket's peer. Through Render, Cloudflare sets the
first and refuses a request that sends its own (403, its error 1000; probed
2026-11-20), so a client's limit holds per real address; the other two are for
a service run without it. Many addresses at once are what the service's limit
and the day's bytes are for, and what costs money is capped in the database
(`resolves.py`, `budget.py`), not here. The counts are this process's, kept for a minute,
and the day's bytes for the day: a restart starts both again.

And every answer says, as an API's should, not to sniff its type or frame it.
"""

import json
import logging
import threading
import time
from typing import Callable, Dict, Optional

from .config import settings

logger = logging.getLogger(__name__)

# The keep-alive pings both (`.github/workflows/keepalive.yml`): never held to
# the service's minute or the day's bytes, only to their client's minute.
EXEMPT = frozenset(("/health", "/health/db"))

DAY_SECONDS = 86_400

_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)

_WITH_BODY = frozenset(("POST", "PUT", "PATCH"))


class Minute:
    """Requests counted by key in the current minute (a fixed window)."""

    def __init__(self, keys_at_most: int = 50_000):
        self._lock = threading.Lock()
        self._minute: Optional[int] = None
        self._counts: Dict[str, int] = {}
        self._keys_at_most = keys_at_most

    def take(self, key: str, limit: int) -> bool:
        """Count one request for `key`, where its minute has room. 0 is no limit.

        Past `keys_at_most` keys in one minute, a new key is let through
        uncounted rather than kept: many addresses at once are what the
        service's own limit is for, and memory stays bounded."""
        if not limit:
            return True
        minute = int(time.monotonic() // 60)
        with self._lock:
            if minute != self._minute:
                self._minute = minute
                self._counts.clear()
            count = self._counts.get(key)
            if count is None:
                if len(self._counts) >= self._keys_at_most:
                    return True
                count = 0
            if count >= limit:
                return False
            self._counts[key] = count + 1
            return True

    def clear(self) -> None:
        with self._lock:
            self._counts.clear()
            self._minute = None


class Day:
    """Bytes sent in the current UTC day (days since the epoch, by `clock`)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._lock = threading.Lock()
        self._clock = clock
        self._day: Optional[int] = None
        self._sent = 0
        self._said = False

    def _roll(self) -> None:
        today = int(self._clock() // DAY_SECONDS)
        if today != self._day:
            self._day = today
            self._sent = 0
            self._said = False

    def add(self, count: int) -> None:
        with self._lock:
            self._roll()
            self._sent += count

    def sent(self) -> int:
        with self._lock:
            self._roll()
            return self._sent

    def first_refusal(self) -> bool:
        """Whether this is the day's first refusal, which is logged."""
        with self._lock:
            self._roll()
            said, self._said = self._said, True
            return not said

    def seconds_left(self) -> int:
        """Seconds to the next UTC midnight, at least one."""
        return max(1, DAY_SECONDS - int(self._clock()) % DAY_SECONDS)

    def clear(self) -> None:
        with self._lock:
            self._day = None
            self._sent = 0
            self._said = False


clients = Minute()
posts = Minute()
service = Minute()
day = Day()


def reset() -> None:
    """Forget every count (for tests)."""
    for window in (clients, posts, service, day):
        window.clear()


# What the server and the edge add to every answer after the app has sent its
# own headers: uvicorn's `date` and `server`, Render's `rndr-id` and
# `x-render-origin-server`, Cloudflare's `cf-ray`, `cf-cache-status`, `alt-svc`.
# Measured 2026-11-20: a `/health` answer carried 375 bytes of headers, about
# 150 of them the app's.
EDGE_BYTES = 250


def _header_bytes(message) -> int:
    """About what a response's status line and headers take on the wire."""
    return EDGE_BYTES + 17 + 2 + sum(len(key) + len(value) + 4
                                     for key, value in message.get("headers") or ())


def _header(scope, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return None


def client_of(scope) -> str:
    """The address a request is counted under (see the module's docstring)."""
    said = _header(scope, b"cf-connecting-ip")
    if said and said.strip():
        return said.strip()
    forwarded = _header(scope, b"x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    peer = scope.get("client")
    return peer[0] if peer else "unknown"


async def _refuse(send, status: int, detail: str, retry_after: Optional[int] = None) -> None:
    body = json.dumps({"detail": detail}).encode()
    headers = [(b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()), *_HEADERS]
    if retry_after is not None:
        headers.append((b"retry-after", str(retry_after).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class Guard:
    """The ASGI middleware that holds every request to the above."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Every byte the service sends counts against the day, refusals too.
        async def counted(message):
            if message["type"] == "http.response.start":
                day.add(_header_bytes(message))
            elif message["type"] == "http.response.body":
                day.add(len(message.get("body", b"")))
            await send(message)

        health = scope.get("path") in EXEMPT
        who = client_of(scope)
        busy = None
        if not health and not service.take("*", settings.service_requests_per_minute):
            busy = "The service is answering as many requests as it can. Ask again in a minute."
        elif not clients.take(who, settings.requests_per_minute):
            busy = "Too many requests from one place. Ask again in a minute."
        elif (scope.get("method") in _WITH_BODY
              and not posts.take(who, settings.posts_per_minute)):
            busy = "Too many requests from one place. Ask again in a minute."
        if busy is not None:
            await _refuse(counted, 429, busy, retry_after=60)
            return

        allowed = settings.response_bytes_per_day
        if not health and allowed and day.sent() >= allowed:
            if day.first_refusal():
                logger.warning("Sent %d bytes today, past RESPONSE_BYTES_PER_DAY (%d): every "
                               "route but the health checks is a 503 until midnight UTC",
                               day.sent(), allowed)
            await _refuse(counted, 503,
                          "The service has sent all it may today. Ask again after midnight UTC.",
                          retry_after=day.seconds_left())
            return

        if scope.get("method") in _WITH_BODY:
            limit = settings.max_body_bytes
            declared = _header(scope, b"content-length")
            if declared is not None:
                try:
                    too_long = int(declared) > limit
                except ValueError:
                    await _refuse(counted, 400, "Content-Length is not a number.")
                    return
                if too_long:
                    await _refuse(counted, 413,
                                  "The request body is larger than this service reads.")
                    return
            # Read whole, and never past the limit, whatever the header said
            # (or where none did); the route is handed it as it came.
            body = b""
            more = True
            while more:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body += message.get("body", b"")
                if len(body) > limit:
                    await _refuse(counted, 413,
                                  "The request body is larger than this service reads.")
                    return
                more = message.get("more_body", False)
            replay = [{"type": "http.request", "body": body, "more_body": False}]

            async def receive_again():
                if replay:
                    return replay.pop()
                return await receive()

            downstream = receive_again
        else:
            downstream = receive

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                held = {key.lower() for key, _ in message.get("headers") or ()}
                message = {**message, "headers": [
                    *(message.get("headers") or ()),
                    *(pair for pair in _HEADERS if pair[0] not in held)]}
            await counted(message)

        await self.app(scope, downstream, send_with_headers)
