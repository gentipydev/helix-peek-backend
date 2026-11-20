# Security

Helix Peek is a community and portfolio app with no accounts and no payments. This
repository is public, and so is the service it deploys
(`https://helix-peak-backend.onrender.com`). Its security is therefore about two things:
**no secret ever enters the repository**, and **no one can make the project cost money
or fall over**. This file states every guard and its number. It was written in the
review of 2026-11-13, before the app's release, and brought up to date in the review of
2026-11-20, with the app in Google Play's closed testing.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting (**Security → Report a vulnerability** on
this repository). Please don't open a public issue for it.

## Where the secrets are, and where they are not

| Secret | Lives in | Never in |
|---|---|---|
| `DATABASE_URL` (Supabase pooler, with the `postgres` password) | Render's environment; Modal secret `helix-peak-resolver`; the dev Mac's `.env` (mode 600) | git, logs (the worker scrubs it from its lines), `/health/db` (class name only) |
| `SUPABASE_SERVICE_KEY` (`sb_secret_…`, bypasses RLS) | Modal secret; the dev Mac's `.env` | Render (the service never needs it), git, the app |
| `ALPHAGENOME_API_KEY` | Modal secret (handed to the AVI bake alone); `.env`, `~/.env` | git, every other process |
| `MODAL_KEY` / `MODAL_SECRET` (proxy token `render-wake`) | Render's environment | git; it can call the wake endpoint, which starts a sweep, and nothing else |
| Modal token (`~/.modal.toml`, mode 600) | the dev Mac | git, Render |

`.env`, `.env.*` (except `.env.example`) and key, certificate and keystore files are
git-ignored. GitHub secret scanning and push protection are on. On 2026-11-13 every blob
and commit message in this repository's history, on every branch and pull request ref,
was checked for each of the secrets above by its exact value and for every common key
shape. No secret was found.

## What one request may cost: the service (`app/`)

No accounts, so every limit is on the request, the install's own random id (`asker`), or
the whole service. Each limit has a default in `app/config.py`, so it holds even where
Render's environment sets nothing. Each can be overridden by the environment variable
named in the table.

| Guard | Default | Variable | Past it |
|---|---|---|---|
| Request body | 16 KB | `MAX_BODY_BYTES` | 413, read no further |
| Requests a minute, one client | 240 | `REQUESTS_PER_MINUTE` | 429 + `Retry-After` |
| POSTs a minute, one client | 30 | `POSTS_PER_MINUTE` | 429 |
| Requests a minute, whole service | 3,000 | `SERVICE_REQUESTS_PER_MINUTE` | 429 |
| Bytes sent a UTC day, whole service (bodies and headers, refusals included) | 150 MB | `RESPONSE_BYTES_PER_DAY` | 503 until midnight UTC, health checks still answered |
| Builds asked a day (asks and resumes) | 20 | `RESOLVES_PER_DAY` | 429 |
| Builds asked a day, one install | 5 | `RESOLVES_PER_INSTALL_PER_DAY` | 429 |
| Proteins built on demand held (queued included) | 200 | `MAX_BUILT_PROTEINS` | 429 |
| Modal budget left for a whole build's worst case | see below | `app/budget.py` | 429 |
| `/health/db`: database round trips | one each 30 s; the answer stands between | | |
| Query strings / path names | 200 / 32–100 characters | | 422 |

`/health` and `/health/db` count against their client's minute. The service's minute and
the day's bytes never refuse them (their bytes are counted all the same), so a flood
elsewhere never fails the keep-alive that pings them.

The gene route, `/gene/{id}/{gene}`, was retired on 2026-11-20. It read any gene-sized
NCBI record for whoever asked (60 reads an hour, records to 8 MB, a 64 MB cache), and
answered with the gene's whole genomic sequence: megabytes for dystrophin, the largest
answer the service could be made to send, and Biopython's parse of an 8 MB record on a
512 MB instance. The app reads each record as a stored track (`TrackGeneDataSource`), so
nothing called it. With it went the service's only use of `Bio.Entrez`. There is no `/docs`,
`/redoc` or `/openapi.json` either: the routes are in this repository.

The build caps are counted in `spend_ledger` (migration 0015), which nothing that deletes
a build can reach. They used to count `resolve_request` rows, which a discard deletes, so
ask → stop → discard → ask went round without ever reaching the cap. A stopped protein's
scoring asked again was not counted at all. Both are now one row each, written under an
advisory lock, so two asks at once cannot both take the day's last build.

A client is the address it asks from: `CF-Connecting-IP`, else the first entry of
`X-Forwarded-For`, else the socket. A script can claim other addresses, so the
per-client limit is a courtesy. The service-wide limit and every cap in the database hold
whatever a request claims.

Every answer carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and
`Referrer-Policy: no-referrer`. CORS allows any origin, without credentials: there are no
cookies to send.

## What the service may send: Render's bandwidth

Render bills a Hobby workspace's outbound bandwidth past its 5 GB a month, per GB, with no
spend cap, where a card is on file; with none, it suspends every free service for the rest
of the month (render.com/docs/outbound-bandwidth, read 2026-11-20). No minute limit bounds
that: 3,000 answers a minute of `/catalog` (48 KB unzipped) is 5 GB in about 35 minutes,
from as many addresses as a script cares to use. So `guard.py` counts every byte the
service sends in the UTC day, each answer's body and headers and the 250 bytes or so the
server and the edge add to it (`EDGE_BYTES`, measured), and past
`RESPONSE_BYTES_PER_DAY` (150 MB, 4.65 GB in the longest month) answers every route but
the health checks with a 503 until midnight UTC, logging it once. The worst an attacker
can then do is take the service down until midnight, never run up a bill. The count is
the process's, so a deploy or a restart starts the day again; Render restarts the service
only on a deploy or a crash. The app's own answers are kilobytes, and its tracks never
pass through here (they come from Supabase's storage), so a day of real use is a small
part of the budget.

To see it: the service logs `past RESPONSE_BYTES_PER_DAY` the first time a day trips it,
and Render's dashboard (Billing → bandwidth) has the month's bytes.

## What builds may cost: Modal (`pipeline/resolver/modal_app.py`)

A build on demand is the one thing that costs money. The pieces are a fold and the
evidence on CPUs, and the ESM-2 scoring on a GPU (a T4, else an L4, else an A10). The
budget is in `app/budget.py`, read by the service and by the resolver:

| | |
|---|---|
| A rolling day | **$2.00** |
| A calendar month (UTC, as Modal bills) | **$10.00** |
| One whole build at its worst | $0.78 (scoring $0.65, evidence $0.09, fold $0.04); the service asks for $0.85 of room |
| A typical build | 1–3 cents (B2M, 2026-11-13: 102 s from ask to done) |

- Before `score`, `fold` or `evidence` claims anything, it reserves its worst case: its
  timeout at Modal's rates (`modal billing rates`, 2026-11-13), on the dearest GPU it may
  get. The reservation is a row in `spend_ledger`, written under a lock. Where the worst
  case would not fit the day or the month, the call claims nothing and its bake waits,
  queued, until there is room. When the call ends, even by an error, the row is settled
  at what it cost, at the GPU it was given. A call killed at its timeout keeps its worst
  case, which is what it cost.
- The sweep never starts a baker whose worst case would not fit, and neither does a
  baker starting the next one. No GPU starts only to find it may not work.
- The service turns an ask away (429) while the settled spending leaves no room for one
  whole build's worst case. A build it accepts is one the resolver can afford to finish.
- Every function runs in one container at most (`max_containers=1`), scales to zero 2 s
  after its last call, and stops at its timeout (scoring 30 min, fold 18, evidence 57,
  sweep 15). The wake endpoint needs a proxy token and runs in one container at most.
- Not in the ledger: the sweep every five minutes and the wake. Measured on 2026-11-13,
  both together came to about 3 cents a day.

Modal's own **usage limit** (workspace settings → Usage) is the last word, above all of
this. Keep it set (it was $20 on 2026-11-13), and keep it under the plan's monthly
credits.

`.venv/bin/python scripts/spend.py` prints what the budget has counted, and how near
each cap is. `modal billing summary` is Modal's own bill.

## The database (Supabase)

- Row level security is on for every table in `public`, with no policies, and Supabase's
  `anon` and `authenticated` roles hold no privilege there, for future tables either
  (migration 0016). Nothing reads the tables through Supabase's Data API: the service and
  the resolver use `DATABASE_URL`, and the app reads storage's public URLs alone.
- The storage buckets `tracks` and `models` are public to read, by design: they hold the
  tracks the app draws. Only the service key can write to them, and only the resolver and
  the upload scripts hold it.
- Every statement is parameterised. Search input is normalised to letters, digits and
  spaces before it reaches SQL.

## In an emergency

| To | Do |
|---|---|
| Stop every build at once | `modal app stop helix-peak-resolver` (deploy again to restart) |
| Stop new asks, keep reads | set `RESOLVES_PER_DAY=0` on Render |
| Send less | lower `RESPONSE_BYTES_PER_DAY` on Render (the service restarts, and its day with it) |
| Tighten the money | lower `DAY_USD` / `MONTH_USD` in `app/budget.py`, push, and once `ci` passes, `scripts/deploy_modal.py` (`--force` if it cannot wait) |
| See what was spent | `scripts/spend.py`; `modal billing report --for today --show-resources` |

Rotating a credential, if one ever leaks:

- **Database password**: Supabase → Settings → Database → reset the password. Then put
  the new `DATABASE_URL` on Render, in `.env`, and in the Modal secret.
- **Supabase secret key**: Supabase → Settings → API Keys → create a secret key and
  revoke the old one. Then update `.env` and the Modal secret.
- **AlphaGenome key**: issue a new key, delete the old one, and update `.env`, `~/.env`
  and the Modal secret.
- **Modal proxy token**: `modal workspace proxy-tokens create` → put it on Render →
  `modal workspace proxy-tokens delete <old id>`. `render-wake` (2026-11-13) is not scoped
  to an environment, which cannot be changed after a token is made, and `main` is the
  workspace's only environment.
- **The Modal secret** itself: `modal secret create helix-peak-resolver --force` with the
  five keys (`DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `NCBI_EMAIL`,
  `ALPHAGENOME_API_KEY`).

## Known and accepted

- **The pipeline's locked environments** (`pipeline/constraint/requirements-lock.txt`,
  `pipeline/impact/requirements-lock.txt`) carry advisories for transformers, pillow,
  filelock and urllib3 (as of 2026-11-13). They run on Modal and the dev Mac, never on the
  service, and read only trusted inputs: the ESM-2 weights at a pinned revision baked
  into the image, NCBI, UniProt, RCSB and the AlphaGenome Atlas, each over HTTPS. Moving
  them changes the bakes, which must then be re-proved byte for byte (`CLAUDE.md`). Do
  that as its own piece of work.
- **Biopython 1.85**'s advisory (CVE-2025-68463) is an XML external entity in
  `Bio.Entrez`'s XML parser. The service has not called Entrez at all since the gene
  route was retired (2026-11-20); the bakers fetch GenBank as text and never parse
  Entrez XML. 1.85 is kept for the same byte-for-byte reason.
- **Supabase storage's egress** is not limited by anything here: the app fetches the
  public track URLs directly, through Supabase's CDN. The Free plan has 5 GB of cached
  and 5 GB of uncached egress a month, and the largest object is 9.6 MB (dystrophin's
  ClinVar track), so a script could use the month's up in some hundreds of downloads.
  Past it, Supabase restricts the project until the quota refills; on the Free plan it
  never bills. The app caches every track by sha256, so its own readers download each
  once. If this ever matters, the buckets can move behind a host with no egress fees.
- **The per-client minute** is keyed on `CF-Connecting-IP`, which Render's Cloudflare
  edge sets. Whether it also overwrites one a client sends is checked by sending a run of
  requests that each claim another address (2026-11-20; see the review's notes). Either
  way the service's minute, the day's bytes and every cap in the database hold.
- **One person can take a day's 20 builds**, with a new install id for each 5. Testers
  then wait a day to build; nothing costs more.
- **`/health/db`** names the Postgres version. It is managed Postgres, and the keep-alive
  reads that endpoint.
- **Commit metadata** carries the author's addresses, as all git history does.
