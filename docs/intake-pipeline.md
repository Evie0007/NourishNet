# The Intake Pipeline

**Applies to:** NourishNet API v0.3.0
**Status:** Implemented
**Supersedes:** the "add an item by typing it" flow, which remains available
at `POST /items` for corrections and backfills.

This document describes how a physical unit of food becomes a row in
`items`, and what happens to that row afterwards without anyone touching it.
It complements [`functional-spec.md`](functional-spec.md); where the two
disagree, the spec describes the target and this describes what is built.

---

## 1. The shape of it

```
   ┌──────────────┐
   │ UPC scanner  │  hardware wedge, phone camera, or typed
   └──────┬───────┘
          │  GET /catalog/upc/{code}
          ▼
   ┌──────────────┐   normalize → check digit → catalog lookup
   │  Product     │   miss is a normal outcome, not an error
   └──────┬───────┘
          │  POST /intake/scans
          ▼
   ┌──────────────────────────────────────────────┐
   │  IntakeScan  (status: pending)               │
   │                                              │
   │   POST /intake/scans/{id}/ocr-image  ────┐   │
   │   POST /intake/scans/{id}/date       ────┤   │  a proposed date,
   │                                          ▼   │  with a confidence
   │   detected_date, date_confidence, candidates │
   └──────┬───────────────────────────────────────┘
          │  POST /intake/scans/{id}/confirm   ← a person
          ▼
   ┌──────────────┐   dates resolved, deadlines computed
   │    Item      │   status: in_stock
   └──────┬───────┘
          │  app/scheduler.py, every 60s
          ▼
   near_expiry ──▶ available ──▶ reserved ──▶ picked_up
                        └──────────────────▶ discarded / expired_hold
```

## 2. Why a person is in the middle

The two scanners answer different questions and only one of them is
answered reliably.

A **barcode** identifies a product. It is a checksummed number that either
reads correctly or fails to read, and it says nothing whatsoever about how
old the unit in your hand is. Two cartons of milk three weeks apart carry
the same UPC.

An **OCR date read** is a guess with a number attached. Worse, the number
is easy to misread: a page-level mean confidence can sit at 98% while the
single token that decides everything — the year — is the one thing the
camera got wrong. That is why `app/ocr.py` scores each date candidate by
the *minimum* confidence across its own tokens rather than the page mean
(FR-5.8), and why a label carrying two dates is surfaced as a choice rather
than resolved by a heuristic.

So the pipeline is arranged so the machines do the part they are good at —
removing typing — and a person does the part that carries consequences.
`POST /intake/scans/{id}/confirm` is the only endpoint in the pipeline that
writes to `items`. Nothing a scanner produces reaches inventory on its own.

**The confirmation is not pre-checked.** `IntakeConfirm.confirmed_date` is
required and has no default. The client pre-fills the field from the OCR
reading; the person still has to submit it. Defaulting it server-side would
turn confirmation into a button someone presses without looking, which is
the exact failure this step exists to prevent.

## 3. Failure has a way through

A loading dock cannot stop. Every failure mode degrades rather than blocks:

| What fails | What happens |
|---|---|
| Barcode is crushed or missing | "No barcode" opens a scan anyway; the person types the name |
| Barcode reads but nothing knows it | 200 with `product: null`, not 404. The person names it, and it's in the catalog for next time |
| Barcode misreads | Check digit catches it, 422, "scan it again" — no phantom product is created |
| Label is unreadable | No candidate proposed; the date field is typed |
| Vision API is down or unconfigured | `OCRResult.error` is set, no exception. Same as an unreadable label (FR-5.7, NFR-4.4.3) |
| Label has two dates | Both shown as buttons; the person picks (UC-08 flow 5b) |
| Unit is not fit to donate | "Turn away" — no reason required, no confirmation gate (NFR-4.8.4) |
| Confirm is double-tapped | Second call gets 409. One scan cannot produce two pallets |

## 4. The automatic expiration rules

One row per category in `expiration_rules`, four numbers each:

| Column | Means |
|---|---|
| `near_expiry_hours` | How long before the sell-by date staff get warned |
| `publish_offset_hours` | When it joins the donation pool, relative to sell-by. Negative publishes early |
| `discard_after_hours` | The safety margin past sell-by — **used only when the package has no use-by date** |
| `auto_publish` | `false` means it still reaches `near_expiry` on its own, but a person presses publish |

At confirmation, `app/expiration.py:apply_rules_to_item` turns the confirmed
date into two stored deadlines:

- **`donate_after`** — when the item is published to the donation network
- **`discard_after`** — when it comes off the shelf, donated or not

They are stored rather than computed at query time for two reasons: the
sweep becomes an indexed comparison instead of a rule join per row, and
staff can see on the dashboard exactly when each transition will fire
instead of having to trust that it will. An unexplained automatic status
change is what makes people stop trusting automation and start keeping
their own list on paper.

### Invariants that hold however the rules are tuned

- **A use-by date is never crossed.** It caps `discard_after` outright; the
  category margin applies only when the package carries no use-by
  (NFR-4.8.2). Staff say which kind of date they confirmed, and a use-by
  goes in its own column so the margin is never added on top of it.
- **A restricted product or category never publishes automatically.**
  Infant formula ships as `auto_publish: false` (NFR-4.8.6).
- **`needs_review` never auto-publishes.** An unconfirmed read cannot reach
  a recipient.
- **Discarding is never blocked or delayed** by any of the above
  (NFR-4.8.4).

### The sweep

`app/scheduler.py` runs `expiration.sweep` plus `crud.expire_stale_reservations`
every 60 seconds, in that order — a lapsed hold returns its item to the pool
before the expiration rules judge it. Steps run most-urgent first, so an item
simultaneously due to publish and due to discard is discarded.

Idempotent and safe to run concurrently with itself: each step filters on the
status it expects, and the steps that load rows to make a per-row decision take
`FOR UPDATE SKIP LOCKED` on Postgres.

A reserved item that passes its safety deadline releases its reservation and
goes to `expired_hold` rather than `discarded` — something was promised to a
pantry and then withdrawn, which is a different fact about the item and one
worth being able to count.

## 5. PostgreSQL

The pipeline is what made Postgres non-optional. The sweep writes while
dashboards read; SQLite takes a lock over the whole file to do that, so one
sweep would stall every request in flight.

- `postgres://` and `postgresql://` URLs are rewritten to
  `postgresql+psycopg2://`, because that is the form hosting providers hand out
  and SQLAlchemy 2.x dropped the bare `postgres://` scheme.
- Pool sizing, recycling, and an optional statement timeout are configurable;
  see `.env.example`. The timeout is **off by default** — a transaction-mode
  pooler can reject the libpq startup parameter it uses and fail every
  connection. Set it on the database role instead.
- Indexes exist on `items.status`, `items.sell_by_date`, `items.category`,
  `items.upc`, both deadline columns, and `reservations.status` /
  `hold_expires_at` (NFR-4.6.3).
- SQLite dev databases now get `PRAGMA foreign_keys=ON` per connection, so
  the referential integrity the schema declares is actually enforced
  (NFR-4.7.4), plus WAL so reads survive a write.

## 6. Requirements this changes

Offered for the spec's next revision rather than applied to it — the status
table in `functional-spec.md` is the team's document.

| ID | Was | Now | Where |
|---|---|---|---|
| FR-5.6 | ◐ regex candidates, selection unspecified | ✅ candidates parsed to real dates, ranked by label keyword; ties surfaced to the person | `app/ocr.py` |
| FR-5.7 | ○ raises on Vision error | ✅ returns an empty result; intake continues | `app/ocr.py:extract_text_from_bytes` |
| FR-5.8 | ○ mean confidence only | ✅ minimum confidence across the date's own tokens | `app/ocr.py:_score_span` |
| FR-5.9 | ○ substring SKU matching | ✅ checksummed UPC/EAN with catalog resolution | `app/upc.py` |
| FR-5.10 | ○ no OCR logging | ✅ every scan retained with what was proposed and what was accepted | `intake_scans` |
| FR-6.4 | ○ `near_expiry` has no writer | ✅ assigned by the sweep at a rule-defined time | `app/expiration.py:sweep` |
| FR-6.5 | ○ `expired_hold` has no writer | ✅ terminal state for an item whose hold outlived the food | `app/expiration.py:sweep` |
| FR-10.2 | ○ manual trigger only | ✅ runs every 60s in-process | `app/scheduler.py` |
| FR-10.3 | ○ no locking | ✅ status-filtered updates + `FOR UPDATE SKIP LOCKED` | `app/expiration.py:sweep` |
| FR-10.5 | ○ open to anyone | ✅ `POST /items/run-expiration-sweep` is manager-only | `app/routers/items.py` |
| NFR-4.6.2 | ○ | ✅ Postgres-first engine config | `app/database.py` |
| NFR-4.6.3 | ○ only `sku` indexed | ✅ all hot-query columns indexed | `app/models.py` |
| NFR-4.7.4 | ○ unverified | ✅ `PRAGMA foreign_keys=ON` per connection | `app/database.py` |
| NFR-4.8.2 | ○ only `sell_by_date` exists | ✅ `use_by_date` distinct, and it caps the discard deadline | `app/models.py`, `app/expiration.py` |
| NFR-4.8.6 | ○ | ✅ `donation_restricted` on products, `auto_publish: false` on categories | `app/expiration.py` |

Still open, and deliberately so:

- **FR-1.11** — the intake endpoints authenticate as the staff member at the
  dock. An unattended pipeline needs its own service credential.
- **FR-5.5 / NFR-4.2.x** — label images are read and dropped, not stored.
  Storing them means solving retention and bystander framing first, and a
  reviewer currently checks the OCR text rather than the picture.
- **NFR-4.6.4** — `scripts/upgrade_schema.py` is a one-time bridge that knows
  about exactly one schema change. The next one should be Alembic.
- **FR-6.2** — transitions are still not validated against a state table.
  The sweep only makes legal moves, but `PATCH /items/{id}/status` still
  accepts any of them.
