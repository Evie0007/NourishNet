# NourishNet

Smart-shelf food recovery: tracks grocery items toward their sell-by date and
hands them to verified food pantries before they're thrown out.

See [`docs/functional-spec.md`](docs/functional-spec.md) for the full
requirements and use cases, and
[`docs/intake-pipeline.md`](docs/intake-pipeline.md) for how the UPC scanner,
OCR date reader, confirmation step, and expiration rules fit together.

## Layout

```
app/               FastAPI backend
  auth.py          password hashing, JWT, role guards
  crud.py          business logic (intake, confidence branch, hold window, reservations)
  database.py      engine + session (PostgreSQL; SQLite for local dev)
  expiration.py    the automatic expiration rules and the sweep that applies them
  models.py        SQLAlchemy schema
  ocr.py           OCR date scanner — Vision call, date parsing, per-date confidence
  scheduler.py     background loop that runs the sweep every minute
  upc.py           UPC/EAN normalization, check digits, catalog resolution
  routers/         HTTP endpoints
frontend/          React 19 + Vite + Tailwind
  src/pages/       Login, StaffDashboard, Intake, OrganizerDashboard
scripts/           seed_demo.py, upgrade_schema.py, sweep.py
```

## The three screens

| Route | Who | What |
|---|---|---|
| `/` | Anyone | Welcome + sign in (one form, both roles). Also holds the pantry self-registration form. |
| `/staff` | `staff`, `manager`, `admin` | Intake station, status counts, OCR review queue, near-expiry list, expiration rules, shelf conditions, inventory, QR pickup confirmation |
| `/organizer` | `org_coordinator` | Available donations, reserve, QR code for pickup, hold-window countdown, cancel |

Sign-in routes each user to their own dashboard based on the role the server
returns — never on which URL was typed.

## How an item gets into inventory

```
  UPC scanner ──▶ GET  /catalog/upc/{code}      identity, from the catalog
                    │
  OCR date    ──▶ POST /intake/scans/{id}/ocr-image   a proposed date + confidence
                    │
  a person    ──▶ POST /intake/scans/{id}/confirm     ← the only step that writes an Item
                    │
  the rules   ──▶ donate_after / discard_after computed from the category rule
                    │
  the sweep   ──▶ in_stock → near_expiry → available → discarded, on its own
```

**Why the confirmation step is not optional.** A barcode is reliable about
*what* something is and says nothing about *how old this unit is*. An OCR date
read is a guess with a number attached, and a page-level confidence score can
be high while the one token that matters — the year — is misread. Neither is a
basis for putting food in front of a family. The scanners remove the typing;
the person keeps the judgment.

Every failure in that chain has a way through, because a loading dock cannot
stop: an unknown barcode is named by hand, an unreadable label gets a typed
date, and Vision being down leaves a scan with no proposed date rather than an
error. None of them blocks receiving.

**The rules** live in the `expiration_rules` table, one row per category, and
are visible and editable on the dashboard's *Expiration rules* tab. Four
numbers per category: when to flag near-expiry, when to offer to pantries, how
long past the sell-by date is still safe, and whether publication happens
automatically at all. Two invariants hold whatever they are set to — a use-by
date is never crossed, and a category marked restricted (infant formula) is
never published without a person.

The sweep runs inside the API every 60 seconds (`SWEEP_INTERVAL_SECONDS`). Set
`SWEEP_ENABLED=false` and run `python -m scripts.sweep` from a real scheduler
instead if you'd rather it lived outside the web process.

### Seeing the rules work

They act on real dates, so freshly stocked items do nothing observable for a
day or more — which makes the one part of the system that runs unattended also
the part nobody ever watches run.

```bash
python -m scripts.demo_expiration
```

Stocks one item per interesting category and steps a simulated clock past their
deadlines, printing what moved. It is the real sweep against a real database
reading the real rules; only `now` is supplied rather than read off the wall
clock. Uses a throwaway SQLite file so it can't touch the team database — pass
`--use-configured-db` to run it against `DATABASE_URL` instead.

## Running it locally

**Backend**

```bash
python -m venv venv
venv\Scripts\activate          # PowerShell:  .\venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env           # then fill in DATABASE_URL and JWT_SECRET
python -m scripts.upgrade_schema   # only needed for a database that predates the intake tables
python -m uvicorn app.main:app --reload
```

API docs at http://localhost:8000/docs.

PostgreSQL is the target. Local Postgres in one command:

```bash
docker run --name nourishnet-db -e POSTGRES_PASSWORD=nourishnet \
  -e POSTGRES_USER=nourishnet -e POSTGRES_DB=nourishnet \
  -p 5432:5432 -d postgres:16
```

`DATABASE_URL=sqlite:///./nourishnet.db` still works for building screens, but
SQLite takes a lock over the whole file to write and will not reproduce the
behaviour under concurrency — test the reservation claim and the expiration
sweep against Postgres.

### Upgrading an existing database

`Base.metadata.create_all` at startup adds missing *tables* but never a column
to a table that already exists, so a database created before the intake
pipeline needs one run of:

```bash
python -m scripts.upgrade_schema
```

It adds the new columns and indexes, seeds the expiration rules, and backfills
`donate_after` / `discard_after` onto existing items — without that backfill,
older stock has no deadlines and is invisible to the sweep. Idempotent, and a
no-op on a database that is already current. Render runs it automatically via
`preDeployCommand`.

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL` in `frontend/.env` to wherever the API is running.

## Demo accounts

The testing-phase accounts are created by the seed script. Passwords come from
the environment — this repo is public, so nothing is hardcoded.

```powershell
$env:DEMO_STAFF_PASSWORD="<pick something long>"
$env:DEMO_ORG_PASSWORD="<pick something long>"
python -m scripts.seed_demo
```

| Role | Email |
|---|---|
| Store manager | `nourishnet26+staff@gmail.com` |
| Organizer | `nourishnet26+organizer@gmail.com` |

Both plus-addresses deliver to the NourishNet26 inbox, so confirmation emails
land somewhere you can read them. The script also creates a pre-verified demo
pantry, sample shelves and items, the default expiration rules, a small UPC
catalog, and one intake scan left mid-flow so the Intake tab has something in
its queue. Re-running it updates the accounts rather than duplicating them.

To demonstrate the scanner without hardware, type one of the seeded barcodes
into the UPC field — `036000291452` (milk), `041196910759` (sourdough), or
`300871239609` (infant formula, the case where the rules decline to publish
automatically). A hardware wedge scanner types the digits itself, which is why
that field holds focus.

### Scanning a real barcode

**Scan with camera** on the Intake tab reads UPC-A, UPC-E, EAN-13 and EAN-8
off a live preview. It uses the browser's own `BarcodeDetector` where that
exists and lazy-loads ZXing where it doesn't, so the 400KB fallback only
downloads on browsers that need it.

The camera needs a secure context, so the page must be on `http://localhost`
or HTTPS — a LAN address like `http://192.168.1.20:5173` is refused by the
browser, and the error message says so rather than blaming permissions.

Laptop webcams are fixed-focus and struggle with the fine bars of a UPC. Fill
the on-screen box, hold still, and give it light. Whatever happens, the digits
printed under the barcode can always be typed — same value, same check-digit
validation.

### Scanning from a phone

A phone camera reads barcodes far more easily than a laptop webcam, so this is
worth the two minutes of setup.

```bash
npm run dev:mobile     # HTTPS + LAN + API proxy
```

Open the `https://` **Network** address it prints on a phone on the same
Wi-Fi, and accept the certificate warning (Advanced → Proceed). The cert is
self-signed, which is exactly what the warning is for; nothing here should
ever be reachable from outside your network.

Two problems get solved by that one command. `@vitejs/plugin-basic-ssl` gives
real HTTPS, so the camera is allowed to open. And `.env.mobile` sets
`VITE_API_URL=/api`, so the app calls its own origin and Vite proxies to the
backend — the phone needs one reachable address instead of two, and the
requests are same-origin, so `CORS_ORIGINS` never has to learn about each
tester's device.

`npm run dev` is unchanged: localhost only, API called directly on `:8000`.
Binding to the LAN is deliberately opt-in rather than something that happens
because you ran the usual command.

If the certificate warning is a dead end — iOS Safari is stricter than Chrome
— use a tunnel instead, which gets you a genuinely trusted certificate:

```bash
cloudflared tunnel --url http://localhost:5173
```

Then run `npm run dev:mobile` alongside it so the API proxy still applies.

## Deploying

The frontend deploys to Vercel with root directory `frontend/`. The API needs a
separate host that can run a long-lived process (Render, Railway, Fly). Two
environment variables matter on the API host:

- `JWT_SECRET` — a fresh value, not the one from local dev
- `CORS_ORIGINS` — your Vercel URL, comma-separated if there's more than one

Then set `VITE_API_URL` in Vercel to the API's URL.

## Known gaps

Tracked in the spec; the ones that matter most before real users:

- **No migrations.** `scripts/upgrade_schema.py` is a one-time bridge, not a
  migration system — it knows about exactly one schema change. The next one
  should arrive as Alembic (C-5, NFR-4.6.4).
- **No admin verification UI.** `Pantry.verified` is enforced at reservation
  time but nothing flips it to true except the seed script (FR-7.5, UC-04).
- **Label images aren't stored.** `POST /intake/scans/{id}/ocr-image` reads the
  frame and drops it, so the review queue has the OCR text but no picture to
  check it against. Storing them means solving retention first (NFR-4.2.3) and
  framing out bystanders (NFR-4.2.2).
- **The OCR pipeline borrows a staff token.** The intake endpoints authenticate
  as the person at the dock. An unattended pipeline needs its own service
  credential (FR-1.11).
- **The sweep dies with the web process.** It's an asyncio task, so a crashed
  worker stops sweeping until it restarts, and Render's free plan sleeps an
  idle service. Move to `scripts/sweep.py` on a cron service if that matters.
- **No server-side logout.** The client drops its token; the token stays valid
  until it expires (FR-1.6).
- **No email.** Reservation confirmations, expiry warnings, and password reset
  are all unbuilt (FR-1.7, FR-7.7, FR-8.12, FR-10.6).
- **No tests** beyond manual verification.
