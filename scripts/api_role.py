"""Give the service's own role (`helixpeek_api`, migration 0017) a password, and
write the DATABASE_URL Render takes.

    .venv/bin/python scripts/api_role.py           # a new password, checked, then .env.render
    .venv/bin/python scripts/api_role.py --check   # check .env.render's URL; write nothing

Run from the machine whose `.env` holds the `postgres` URL, after 0017 is
applied. The password is 32 random characters, and the database is sent only
its SCRAM verifier, made here: the password itself never reaches the server,
its statement log or `pg_stat_statements`. The URL is the `postgres` one with
the role's name (`helixpeek_api.<ref>`, as Supabase's pooler names a role)
and that password, through the same pooler host, port and `sslmode`.

Before it writes anything, the URL is checked: it logs in through the pooler as
the role, reads the curated catalog, and is refused deleting a curated protein
and writing a paid call's ledger row. The URL goes to `.env.render` (mode 600,
git-ignored by `.env.*`), and nothing secret is printed. Put its line on
Render (Environment → DATABASE_URL), then delete the file.

Running it again sets a new password: the URL Render holds stops working
until it is given the new one.
"""

import argparse
import base64
import hashlib
import hmac
import os
import secrets
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg  # noqa: E402

from app.config import settings  # noqa: E402

ROLE = "helixpeek_api"
OUT = Path(__file__).resolve().parent.parent / ".env.render"


def scram_verifier(password: str, salt: bytes, iterations: int = 4096) -> str:
    """What Postgres keeps for a SCRAM-SHA-256 password (RFC 5802, 7677)."""
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode()

    return "SCRAM-SHA-256${}:{}${}:{}".format(
        iterations, b64(salt), b64(stored_key), b64(server_key))


def service_url(admin_url: str, password: str) -> str:
    """`admin_url` as the role: its name, with the project's ref where the
    pooler wants one (`postgres.<ref>` becomes `helixpeek_api.<ref>`)."""
    parts = urllib.parse.urlsplit(admin_url)
    user = parts.username or ""
    name = ROLE + user[len("postgres"):] if user.startswith("postgres.") else ROLE
    host = parts.hostname or ""
    netloc = "{}:{}@{}{}".format(
        urllib.parse.quote(name, safe="."), urllib.parse.quote(password, safe=""), host,
        ":{}".format(parts.port) if parts.port else "")
    return urllib.parse.urlunsplit(
        (parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def check(url: str) -> str:
    """What the role is let do through `url`, said in one line; raises where
    it cannot log in, or is let do what the service never does."""
    with psycopg.connect(url, connect_timeout=15, prepare_threshold=None) as conn:
        user, timeout = conn.execute(
            "select current_user, current_setting('statement_timeout')").fetchone()
        if user != ROLE:
            raise SystemExit("Logged in as {}, not {}.".format(user, ROLE))
        curated = conn.execute(
            "select count(*) from protein where catalog_order is not null").fetchone()[0]
        if not curated:
            raise SystemExit("The role reads no curated protein: its policies are missing.")
        conn.rollback()
        if conn.execute(
                "delete from protein where catalog_order is not null").rowcount != 0:
            conn.rollback()
            raise SystemExit("The role could delete a curated protein (rolled back).")
        conn.rollback()
        try:
            conn.execute("insert into spend_ledger (kind, usd) values ('score', 0)")
        except psycopg.errors.InsufficientPrivilege:
            conn.rollback()
        else:
            conn.rollback()
            raise SystemExit("The role could write a paid call's ledger row (rolled back).")
    host = urllib.parse.urlsplit(url).hostname
    return ("{} logs in through {} (statement_timeout {}), reads {} curated proteins, and "
            "may neither delete them nor write a paid call's row.").format(
                ROLE, host, timeout, curated)


def _read_out() -> str:
    for line in OUT.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line[len("DATABASE_URL="):]
    raise SystemExit("{} holds no DATABASE_URL line.".format(OUT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="check the URL in .env.render, and write nothing")
    asked = parser.parse_args()

    if asked.check:
        print(check(_read_out()))
        return 0

    admin = settings.database_url
    if not admin:
        raise SystemExit("DATABASE_URL is not set.")
    if not (urllib.parse.urlsplit(admin).username or "").startswith("postgres"):
        raise SystemExit("DATABASE_URL is not the postgres role's; run this with that one.")
    with psycopg.connect(admin, autocommit=True, prepare_threshold=None) as conn:
        if conn.execute("select 1 from pg_roles where rolname = %s", (ROLE,)).fetchone() is None:
            raise SystemExit("No role {}: apply migrations/0017_api_role.sql first.".format(ROLE))
        password = secrets.token_urlsafe(24)
        verifier = scram_verifier(password, secrets.token_bytes(16))
        assert "'" not in verifier
        # A utility statement takes no parameters; the verifier is base64 and
        # `$:` alone.
        conn.execute("alter role {} password '{}'".format(ROLE, verifier))

    url = service_url(admin, password)
    said = None
    for attempt in range(4):
        try:
            said = check(url)
            break
        except psycopg.OperationalError:
            # The pooler may take a moment to see a new password.
            if attempt == 3:
                raise
            time.sleep(5)

    descriptor = os.open(str(OUT), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write("DATABASE_URL={}\n".format(url))
    os.chmod(str(OUT), 0o600)
    print(said)
    print("Wrote {} (mode 600). Put its line on Render as DATABASE_URL, then delete the "
          "file.".format(OUT.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
