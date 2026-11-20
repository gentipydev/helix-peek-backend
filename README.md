# helix-peek-backend

The FastAPI service behind the Helix Peek Flutter app: the protein catalog, each
protein's track index (every track a public Supabase storage URL the app fetches
directly), suggestions from every reviewed human protein, and builds on demand.
`pipeline/` holds the offline bake tools and the resolver that builds a protein.

## The endpoints

```
GET  /catalog                          the curated proteins, in catalog_order
GET  /catalog/search?q=                the curated proteins a query names
GET  /protein/{slug}                   one protein's row
GET  /protein/{slug}/tracks            its tracks: state, storage URL, sha256, provenance
GET  /proteins/suggest?q=              every reviewed human protein, as one types
GET  /proteins/built                   the proteins built on demand, newest first
POST /proteins/resolve                 ask for a protein to be built
GET  /proteins/resolve/{gene}          how far its build has got
POST /proteins/resolve/{gene}/stop     stop it (the install that asked)
POST /proteins/resolve/{gene}/keep     keep a stopped build as it is
POST /proteins/resolve/{gene}/discard  delete everything built for a stopped build
POST /proteins/stopped                 one install's stopped builds still to decide
GET  /peptide-lm/runs/{name}           the peptide model's recorded run
GET  /health                           the service is up
GET  /health/db                        the database answers (cached 30 s)
```

There is no `/docs`, `/redoc` or `/openapi.json`.

### The gene record, and the route it came from

Until 2026-11-20 the service also had `GET /gene/{id}/{gene}`, which read one
GenBank record from NCBI (through a cache) and returned one gene lifted out of
it. The app stopped calling it when each protein's record became a stored track
(`record`), and the route was retired: it read NCBI for anyone who asked, and its
answer, a gene's whole genomic sequence, was the largest thing the service could
be made to send (`SECURITY.md`).

The record's format is unchanged. `pipeline/mock/build_gene_record.py` (and the
resolver, for a protein built on demand) makes it with `app/genbank_parser.py`'s
`extract_gene`: the gene's span, sequence, exons, transcript, protein and
peptides, **1-based inclusive**, as NCBI shows the record, with `join(...)`
locations kept in `segments` so introns are not silently spliced out.

#### Why the gene filter is an exact match

`NG_007114` is a RefSeqGene *region*, not a single gene. It carries three: `TH`
(1..2266), `INS` (4986..6416) and the `INS-IGF2` readthrough (4986..>8416). `INS`
and `INS-IGF2` **start at the same base and share a signal peptide**, so no
coordinate window separates them -- `efetch` with `seq_start=4986&seq_stop=6416`
still returns five `INS-IGF2` features. Selection is therefore an exact comparison
against the `/gene` qualifier; `"INS-IGF2".startswith("INS")` is why a prefix or
substring test would be wrong.

## Setup

`NCBI_EMAIL` is required. NCBI rejects and eventually blocks traffic with no
contact address, and the scripts and the resolver that share these settings read
NCBI, so the settings refuse to load without it rather than failing later.

```bash
cp .env.example .env
# edit .env and set NCBI_EMAIL to a real address
```

### The database

`DATABASE_URL` is optional. Unset, the pool is never created and every route
that reads the database answers 503. It is opened in the app lifespan rather
than at import, so a wrong or unreachable database is reported on
`GET /health/db` instead of killing the worker before it can say why.

On Supabase, take the **connection pooler** URI (Project Settings → Database),
not the direct `db.<ref>.supabase.co` one: direct hosts resolve to IPv6 only,
which Render cannot reach. The URI needs `?sslmode=require`.

```
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-<n>-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Port 5432 is the session pooler, which is what a long-lived server with its own
client-side pool wants. Port 6543 is the transaction pooler, for serverless
callers; it disallows prepared statements, so it needs psycopg configured for
that before it will work here.

`GET /health/db` returns `unconfigured`, `ok` with the server version, or
`error` with just the exception class, and one answer stands for 30 seconds, so
a flood of it costs one round trip in that time. The endpoint is public and
psycopg names the host, port and user in its connection errors, so the full
message goes to the log instead of the response body.

The `genbank_record` table the retired gene route cached records in stays in the
database, unused: a migration here only ever adds.

### Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload
```

Verified on macOS system Python 3.9.6; the code avoids 3.10+ syntax so it runs on
both that and the 3.12 in the Docker image.

### Run with Docker

```bash
docker compose up --build
```

`.env` is read via `env_file`, so create it first.

### Tests

```bash
.venv/bin/pytest
```

No test reaches the network. The database is a fake pool that answers by SQL
shape, except the opt-in SQL tests, which run against a local Postgres
(`RESOLVER_TEST_DATABASE_URL`). The parser tests read a saved NCBI response
(`tests/fixtures/ng_007114.gb`).

## Suggestions: every reviewed human protein

`GET /proteins/suggest?q=insul&limit=12` answers as someone types. The twenty
listed proteins are among the answers, tagged `listed`, but so is every other
reviewed human protein, each saying what the app can do with it: `ready`
(built earlier), `buildable`, or `unavailable` with a `reason`.

It reads `protein_index`, built from UniProt's reviewed human entries joined to
MANE Select, one row per (entry, gene) because some entries are made by two
genes (P69905 by HBA1 and HBA2). Search terms are normalised to lower-case
ASCII and stored under "C" collation, so every prefix is a plain btree range
that works under psycopg's prepared, generic plans too. A trigram index answers
typos, but only when no prefix matches at all. Server-side, a query takes 6-30 ms.

To build it, or to refresh it after a UniProt or MANE release:

```sh
.venv/bin/python scripts/apply_migration.py migrations/0002_protein_index.sql   # once
.venv/bin/python scripts/load_protein_index.py --dry-run                         # report only
.venv/bin/python scripts/load_protein_index.py                                   # replace the index
.venv/bin/python scripts/check_suggest.py https://helix-peak-backend.onrender.com
```

The loader refuses to write if a listed protein has no buildable row, or if a
build would take a listed protein's slug. `apply_migration.py` is `psql -f` for a
machine without psql.

## Resolving any protein (Phase 6)

```
POST /proteins/resolve        {"gene": "BRCA1"}  -> {"slug", "state", "reason"}
GET  /proteins/resolve/{gene}                     -> the same, without asking
```

A `buildable` suggestion can be asked for. The service writes one
`resolve_request` row (`migrations/0009_resolve_request.sql`) and returns: 202
`pending` while it is built, or 200 with what the protein already is --
`ready` (its `slug` opens the walk; for one of the twenty, the slug it shipped
with), `refused` or `unavailable` with a `reason`, or `failed` (asking again
queues a new request). A read also says `buildable` for a protein nobody has
asked for. A gene the index does not hold is a 404, and an unreadable database
a 503.

The service resolves nothing itself. The resolver
([`pipeline/resolver/`](pipeline/resolver/README.md)) runs on Modal, or on a Mac until it
is deployed there: it makes the protein row from
UniProt and MANE, stores the record track, and scores ESM-2 650M on a GPU; the
app polls `GET /proteins/resolve/{gene}`, then `/protein/{slug}/tracks` while
the constraint track goes from `pending` to `ready`. A resolved protein has a
row like any other but never joins `/catalog`, which stays the twenty.

Two readers asking for one gene make one request. A request costs GPU time and
the service has no auth, so asks are capped, each past its cap a 429 (`SECURITY.md`):
a day's (`RESOLVES_PER_DAY`, default 20) and one install's
(`RESOLVES_PER_INSTALL_PER_DAY`, default 5), counted in `spend_ledger`, which no discard
deletes, with a stopped protein's scoring asked again counted too; the proteins held
(`MAX_BUILT_PROTEINS`, default 200); and Modal's budget (`app/budget.py`), which must
still hold a whole build's worst case. After writing a request the service calls the resolver's
wake URL (`MODAL_WAKE_URL`, with a Modal proxy auth token in `MODAL_KEY` and
`MODAL_SECRET`); unset, the resolver's five-minute schedule takes it. A Mac's
worker is not woken: it asks the queue every minute.
`pipeline/resolver/README.md` is the runbook.

## Rate limiting

`app/guard.py` holds every request, before a route sees it, to a body of 16 KB
(`MAX_BODY_BYTES`) and to so many requests a minute: 240 a client
(`REQUESTS_PER_MINUTE`), 30 of them POSTs (`POSTS_PER_MINUTE`), 3,000 for the
whole service (`SERVICE_REQUESTS_PER_MINUTE`); past one, a 429 with `Retry-After`.
`/health` and `/health/db` count against their client's minute and never the
service's. Every answer's bytes are counted for the UTC day, and past 150 MB
(`RESPONSE_BYTES_PER_DAY`) every route but the health checks is a 503 until
midnight UTC, which keeps a month inside Render's 5 GB of outbound bandwidth.
`SECURITY.md` has every limit and why.

## CORS

`allow_origins=["*"]` with no credentials: the service has no cookies or
accounts, so a page elsewhere can read only what anyone can, and
`flutter run -d chrome` works.

## Deliberately not built

Auth is not here: there are no accounts, and what an ask may cost is capped
instead (`SECURITY.md`).

## CI and deploys

- `.github/workflows/ci.yml`, on every push and pull request: `pytest` on Python 3.9 and
  3.12 (3.12 with the Modal client, for the Modal app's shape tests), against a Postgres 17
  service container so the SQL tests run, and a `docker build` of the service's image.
- Render deploys `main`. With its Auto-Deploy on "After CI Checks Pass", a commit deploys
  only once `ci` passed on it.
- Modal is deployed from the dev Mac with `scripts/deploy_modal.py` (`--check` first),
  which refuses unless both checkouts match GitHub and `ci` passed on them.
- `keepalive.yml` pings the service every ten minutes and the database once a day.

## Layout

```
app/
  main.py             FastAPI app: guard, CORS, router, pool lifespan, /health and
                      /health/db
  guard.py            body size, requests a minute and bytes a day, before every route
  budget.py           Modal's budget for builds on demand, read by both sides
  config.py           pydantic-settings; NCBI_EMAIL required, DATABASE_URL optional
  db.py               the psycopg connection pool; sync, to match the routes
  genbank_parser.py   extract_gene(record, gene) -- pure, no I/O; the record builder's
  protein_index.py    how index rows and search terms are made, and normalize()
  suggest.py          /proteins/suggest: ranked prefix tiers, near misses last
  resolves.py         /proteins/resolve: what an ask means, the request row, the wake
  schemas.py          pydantic response models
  router.py           the endpoints and the 404/429/503 mapping
migrations/           applied by hand, in order
pipeline/             the bake tools, moved from helix-peek/tool/ in Phase 3; see
                      pipeline/README.md. Nothing under app/ imports it yet
  targets.py          the twenty curated proteins' bake table
  curated/catalog.json  their hand-written prose, facts and tints
  fetch_tracks.py     storage's tracks back into pipeline/data/, sha256-checked
  check_assets.py     the tracks against each other, the tables and the service
  upload_tracks.py    validated tracks into storage, and their protein_track rows
  seed_catalog.py     the protein and protein_alias rows; --check diffs the live ones
  mock/ constraint/ impact/ clinvar/ structure/   one baker each
  resolver/           Phase 6: any buildable protein resolved on demand, on Modal or a Mac
scripts/
  apply_migration.py      psql -f, for a machine without psql
  resolver_worker.sh      the resolver's worker on this Mac, as a launchd agent
  load_protein_index.py   UniProt + MANE + LRG_RefSeqGene -> protein_index
  check_suggest.py        golden queries and warm timings against a running service
  check_built.py          a built protein against the service, storage, its rows and UniProt
  dry_resolve.py          what the resolver would build for a gene, writing nothing
tests/
  test_health.py          the liveness check; no API maps; the gene route retired
  test_guard.py           body size, requests a minute, bytes a day
  test_genbank_parser.py  the extractor, straight off the fixture
  test_config.py          startup fails without NCBI_EMAIL
  test_health_db.py       the readiness contract, without opening a socket
  test_protein_index.py   normalising, MANE and RefSeqGene parsing, rows and terms
  test_suggest.py         statuses, the short and near-miss rules, against a fake pool
  test_resolve_api.py     /proteins/resolve's states, cap and wake, against a fake pool
  test_pipeline.py        the pipeline imports, the curated rows, check_against's comparisons
  conftest.py             shared fixtures
  fixtures/ng_007114.gb
```
