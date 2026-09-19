# NourishNet — Functional Specification and Use Cases

**Document version:** 1.0
**Date:** 2026-08-29
**Status:** Draft for team review
**Applies to:** NourishNet API v0.1.0 and the NourishNet web client

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Overall Description](#2-overall-description)
3. [Functional Requirements](#3-functional-requirements)
4. [Non-Functional Requirements](#4-non-functional-requirements)
5. [Use Cases](#5-use-cases)
6. [Data Model](#6-data-model)
7. [Item Lifecycle State Machine](#7-item-lifecycle-state-machine)
8. [API Specification](#8-api-specification)
9. [Traceability Matrix](#9-traceability-matrix)
10. [Out of Scope and Future Work](#10-out-of-scope-and-future-work)
- [Appendix A — Gap Analysis](#appendix-a--gap-analysis)
- [Appendix B — Requirement Status Summary](#appendix-b--requirement-status-summary)

---

## 1. Introduction

### 1.1 Purpose

This document specifies the functional and non-functional requirements of NourishNet, a smart-shelf food recovery system. It serves three audiences:

- **The development team**, as the reference defining what to build and in what order.
- **Project reviewers**, as the statement of scope against which the delivered system is evaluated.
- **Prospective grocery and nonprofit partners**, as a description of what the system does with their data and their food.

Requirements are grounded in the existing implementation wherever it exists. Each requirement is marked **Implemented**, **Partial**, or **Proposed**, and cites the source file that realizes it. This makes the document simultaneously a specification and an honest account of the prototype's current state.

### 1.2 Scope

NourishNet reduces grocery-retail food waste by automating the path from "this item is approaching its sell-by date" to "a verified nonprofit has collected it."

**In scope:**

- Authentication and role-based authorization for store staff and nonprofit organizations
- Shelf registration and environmental (temperature/humidity) monitoring
- Automated label reading via optical character recognition, with a confidence threshold that routes uncertain reads to human review
- Item lifecycle management from stocking through donation or disposal
- A donation marketplace where verified organizations discover, reserve, and schedule pickup of available items
- QR-code-based pickup verification at the shelf
- Automatic release of reservations whose hold window lapses

**Out of scope** — see [Section 10](#10-out-of-scope-and-future-work) for the full list. Briefly: multi-store tenancy, delivery logistics and driver routing, native mobile applications, and point-of-sale integration.

**System boundary.** NourishNet does not take physical custody of food and does not transport it. It records state, brokers reservations, and verifies handoffs. Physical food safety judgment remains with store staff at all times ([FR-6.7](#fr-6-item-lifecycle-and-status-transitions), [NFR-4.8](#48-legal-and-food-safety-compliance)).

### 1.3 Definitions, Acronyms, and Abbreviations

| Term | Definition |
|---|---|
| **Available** | An item's status once it has passed store-shelf eligibility and is published to the donation network for reservation. |
| **Confidence threshold** | The minimum OCR confidence score (currently 0.95) at which a label read is trusted without human review. |
| **CV** | Computer vision. |
| **Donation network** | The pool of `available` items visible to verified organizations. |
| **EIN** | Employer Identification Number — the IRS-issued identifier used to verify a nonprofit's legal status. |
| **Hold window** | The interval between reservation and automatic release, during which an item is held exclusively for one organization. Default 3 hours. |
| **Item** | A single physical unit or batch of a product on a shelf, tracked individually through its lifecycle. |
| **OCR** | Optical character recognition — extracting machine-readable text from a photograph of a product label. |
| **Organization / Pantry** | A nonprofit food pantry or similar entity that receives donations. Used interchangeably; `Pantry` is the entity name in code. |
| **Scheduled pickup time** | The specific time within the hold window at which the organization commits to arrive. |
| **Sell-by date** | The date by which a retailer should sell or remove a product. **Not** a safety deadline. |
| **SKU** | Stock Keeping Unit — the store's internal product identifier. |
| **Use-by date** | The manufacturer's estimate of peak quality, and for a small class of products (e.g. infant formula) a safety limit. |
| **Good Samaritan Act** | The Bill Emerson Good Samaritan Food Donation Act (42 U.S.C. § 1791), which limits civil and criminal liability for good-faith food donations to nonprofit organizations. |

### 1.4 References

| Ref | Document |
|---|---|
| [R1] | NourishNet project proposal — cited in source comments by section (§4.3 organization verification, §9.3 OCR confidence pipeline, §13.1 confidence target, §13.3 holding window). External document; not reproduced here. |
| [R2] | Bill Emerson Good Samaritan Food Donation Act, 42 U.S.C. § 1791. |
| [R3] | USDA / FDA guidance on food product dating — the distinction between sell-by, best-by, and use-by labels. |
| [R4] | Google Cloud Vision API — `document_text_detection` reference. |
| [R5] | IEEE Std 830-1998, Recommended Practice for Software Requirements Specifications (structural model for this document). |
| [R6] | WCAG 2.1 Level AA, W3C Recommendation. |

### 1.5 Document Conventions

**Requirement identifiers** take the form `FR-<group>.<number>` for functional requirements and `NFR-<section>.<number>` for non-functional. Identifiers are stable: once assigned, a number is never reused for a different requirement. Withdrawn requirements are struck through rather than deleted.

**Obligation levels** follow RFC 2119: **MUST** (mandatory), **SHOULD** (recommended; deviation requires justification), **MAY** (optional).

**Priority** is one of **Must-have** (the system is not functional without it), **Should-have** (significant value, deferrable), **Could-have** (desirable, first to cut).

**Status** describes the implementation as of this document's date:

| Marker | Meaning |
|---|---|
| ✅ **Implemented** | Working in the current codebase; the citation points at the code. |
| ◐ **Partial** | Some of the behavior exists; the requirement describes the complete target. |
| ○ **Proposed** | No implementation exists. |

**Code citations** name the file and line range. Paths are relative to `backend/` for Python and to the repository root otherwise — so `app/crud.py` is `backend/app/crud.py` on disk, and `frontend/src/api.js` is exactly that. Citations written before the backend/frontend split still read `app/…` and remain correct under that convention.

---

## 2. Overall Description

### 2.1 Product Perspective

NourishNet sits between a grocery store's physical shelves and a network of nonprofit food pantries. It is a new, self-contained system with three external dependencies: a camera and sensor bridge at the shelf, the Google Cloud Vision API for label reading, and the store's SKU catalog for cross-checking product identity.

```
     PHYSICAL SHELF                    NOURISHNET                     USERS
  ┌──────────────────┐          ┌──────────────────────┐      ┌──────────────────┐
  │  Camera          │──image──▶│  OCR Pipeline        │      │  Staff Dashboard │
  │                  │          │  (app/ocr.py)        │      │  /store          │
  │  Temp / Humidity │──────────┼──▶ Confidence branch │◀────▶│                  │
  │  Sensor          │ readings │    (app/crud.py)     │      │  · review queue  │
  └──────────────────┘          │          │           │      │  · near-expiry   │
                                │          ▼           │      │  · QR scan       │
                                │  ┌────────────────┐  │      └──────────────────┘
  ┌──────────────────┐          │  │  Shelf         │  │
  │ Store SKU        │◀─lookup──┼──│  Item          │  │      ┌──────────────────┐
  │ Catalog          │          │  │  Pantry        │  │      │  Org Portal      │
  └──────────────────┘          │  │  Reservation   │  │◀────▶│  /pantry         │
                                │  │  User          │  │      │                  │
  ┌──────────────────┐          │  └────────────────┘  │      │  · browse        │
  │ Google Cloud     │◀─────────┼──  FastAPI (app/)    │      │  · reserve       │
  │ Vision API       │          │                      │      │  · QR code       │
  └──────────────────┘          │  Expiry Scheduler    │      └──────────────────┘
                                └──────────────────────┘
```

### 2.2 Product Functions

**Authentication and authorization.** A single login serves both staff and organization users, routing each to the interface matching their role. Every state-changing operation is authorized against the acting user's role and, for organizations, against the organization they belong to.

**Shelf monitoring.** Shelves are registered with a location and an optional camera identifier. A sensor bridge posts temperature and humidity readings, which are stored as the shelf's current condition.

**Automated label reading.** When an item's label is photographed, the OCR pipeline extracts the text and a confidence score, attempts to match a known SKU, and posts the result. A confidence score at or above the threshold *with* a confirmed SKU match publishes the item automatically; anything less routes it to a staff review queue. This branch is the system's central design decision: it trades a small amount of automation for a guarantee that no uncertain date read reaches a food recipient unchecked.

**Item lifecycle.** Items move through a defined set of states from stocking to donation, disposal, or sale. Every transition has a defined trigger and actor.

**Donation discovery and reservation.** Verified organizations see the pool of available items, reserve what they can collect, and choose a pickup time within the hold window. Reservation is exclusive — one item, one organization.

**Pickup verification.** Each reservation carries a single-use token, rendered as a QR code in the organization's portal and scanned by staff at handoff. Scanning completes the donation and closes the record.

**Automatic release.** Reservations not collected within their hold window expire, returning the item to the available pool so it is not stranded.

### 2.3 Actor Catalog

| Actor | Type | Responsibilities | Authenticates? |
|---|---|---|---|
| **Store Staff** | Human | Registers shelves, stocks items, reviews low-confidence label reads, publishes items to the donation network, scans QR codes at pickup, discards unsafe items | Yes |
| **Store Manager** | Human | All Store Staff permissions, plus provisioning staff accounts and configuring store-level settings (hold window default, confidence threshold) | Yes |
| **Platform Admin** | Human | Verifies organization EIN and eligibility; activates and deactivates organization accounts | Yes |
| **Organization Coordinator** | Human | Browses available donations, reserves items, schedules pickup times, presents QR codes at collection, cancels reservations | Yes |
| **Sensor Bridge** | System | Posts temperature and humidity readings for a registered shelf at a fixed interval | Yes (service credential) |
| **OCR Pipeline** | System | Submits label-read results — raw text, confidence score, SKU match outcome, extracted date — for an item | Yes (service credential) |
| **Expiry Scheduler** | System | Runs periodically; expires reservations past their hold window and releases their items | Internal |

### 2.4 Operating Environment

| Component | Requirement |
|---|---|
| Backend runtime | Python 3.11 or later |
| Backend framework | FastAPI with Uvicorn (ASGI) |
| ORM | SQLAlchemy 2.x, Pydantic v2 for schema validation |
| Database — development | SQLite (`nourishnet.db`, file-based) |
| Database — production | PostgreSQL (`psycopg2-binary` is already a declared dependency) |
| Frontend | React 19, Vite 8, React Router 7, Tailwind CSS 4 |
| Browsers | Current and previous major versions of Chrome, Safari, Firefox, and Edge; mobile Safari and Chrome for the QR-presentation flow |
| External services | Google Cloud Vision API (requires `GOOGLE_APPLICATION_CREDENTIALS`) |
| Shelf hardware | Camera capable of legible label capture; temperature/humidity sensor with network connectivity |

### 2.5 Design and Implementation Constraints

- **C-1 — Single store.** The schema has no store or tenant identifier. All shelves, items, and reservations belong to one implicit store. Multi-store operation requires a schema change ([NFR-4.6](#46-scalability)).
- **C-2 — Network dependency at the shelf.** Sensor readings and label captures require connectivity. The system has no offline queue; a network outage at the shelf means readings are lost, not buffered.
- **C-3 — Vision API dependency.** Label reading is unavailable when the Google Cloud Vision API is unreachable. The system MUST degrade to manual entry rather than blocking ([FR-5.7](#fr-5-ocr--cv-label-pipeline-and-confidence-branch)).
- **C-4 — Prototype-grade SKU matching.** The current cross-check is literal substring matching against OCR text (`ocr.py:92-105`), which the source file's own docstring identifies as fragile. Replacing it with barcode detection is planned but not in this revision's scope.
- **C-5 — No schema migrations.** Tables are created with `Base.metadata.create_all` at `app/main.py:9`. Every schema change currently means dropping data. Alembic adoption is a prerequisite for any deployment holding real records.
- **C-6 — Server-side clock.** All expiry logic compares against server UTC time (`datetime.utcnow()`). Client clocks are never trusted for hold-window decisions.

### 2.6 Assumptions and Dependencies

- **A-1** — The partner store can provide a SKU catalog (SKU code → product name) accessible to the cross-check function.
- **A-2** — Organization coordinators have a smartphone or tablet capable of displaying a QR code at pickup.
- **A-3** — Store staff have a device capable of scanning a QR code at the shelf.
- **A-4** — Organizations are legitimate nonprofits whose EIN can be verified against public IRS records; verification is a manual administrative step, not an automated lookup, in this revision.
- **A-5** — Items published to the donation network have passed their sell-by date but remain safe for consumption. The distinction between sell-by and use-by dates ([R3]) is fundamental to the product's premise and is assumed understood by staff.

---

## 3. Functional Requirements

### FR-1 Authentication and Account Management

> **Status: ○ Proposed.** No authentication exists in the codebase. There is no user table, no password storage, no session or token handling. Every endpoint in `app/routers/` is reachable without credentials, and `app/main.py:19-24` permits any origin. This group is the largest single gap between the prototype and a deployable system.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-1.1** | The system MUST store user credentials as salted password hashes using a memory-hard algorithm (bcrypt or Argon2id). Plaintext or reversibly-encrypted passwords MUST NOT be stored. | Must | ○ |
| **FR-1.2** | The system MUST provide a single login endpoint accepting an email address and password, returning an access token on success and a generic failure message on any failure. | Must | ○ |
| **FR-1.3** | Authentication failures MUST NOT reveal whether the email address exists. The response for an unknown email and a wrong password MUST be indistinguishable in body, status code, and timing. | Must | ○ |
| **FR-1.4** | A single login form MUST serve both staff and organization users. On success the system MUST route the user to the landing page for their role — the inventory dashboard for staff roles, the donation portal for organization coordinators. | Must | ○ |
| **FR-1.5** | Access tokens MUST expire. Default lifetime is 8 hours for staff sessions and 24 hours for organization sessions, both configurable. | Must | ○ |
| **FR-1.6** | The system MUST provide a logout operation that invalidates the current session server-side. Discarding the token client-side alone is insufficient. | Must | ○ |
| **FR-1.7** | The system MUST provide a self-service password reset initiated by email, using a single-use token valid for no more than 60 minutes. | Should | ○ |
| **FR-1.8** | The system MUST lock an account for a cooling-off period after 5 consecutive failed login attempts within 15 minutes, and MUST log the event. | Should | ○ |
| **FR-1.9** | A Store Manager MUST be able to create, deactivate, and reactivate staff accounts. Deactivation MUST invalidate that user's active sessions. | Should | ○ |
| **FR-1.10** | The system MUST expose an endpoint returning the authenticated user's identity, role, and associated organization (where applicable), so the client can render the correct interface without inferring role from the URL. | Must | ○ |
| **FR-1.11** | System actors (Sensor Bridge, OCR Pipeline) MUST authenticate with service credentials distinct from human user accounts, and MUST be restricted to their specific endpoints. | Must | ○ |

*Verification:* Attempt each endpoint without a token and confirm 401. Log in as each role and confirm the landing page. Confirm the password column contains no recoverable plaintext. Time 100 login attempts against known and unknown emails and confirm the distributions overlap.

---

### FR-2 Authorization and Role Model

> **Status: ○ Proposed.** No role concept exists. The staff view and the organization view are distinguished only by which URL the browser is pointed at (`frontend/src/App.jsx:22-26`). An organization coordinator selects which organization they are acting as from an open dropdown (`frontend/src/pages/PantryPage.jsx:86-97`), which means any user can reserve food in any organization's name.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-2.1** | Every user MUST hold exactly one role from: `staff`, `manager`, `admin`, `org_coordinator`. | Must | ○ |
| **FR-2.2** | Every user with role `org_coordinator` MUST be associated with exactly one organization. Every user with a store role MUST NOT be associated with an organization. | Must | ○ |
| **FR-2.3** | The system MUST derive the acting organization from the authenticated user's account. A client-supplied organization identifier MUST NOT be accepted on any reservation operation. | Must | ○ |
| **FR-2.4** | An organization coordinator MUST be able to read and modify only reservations belonging to their own organization. Requests for another organization's reservations MUST return 404, not 403, to avoid confirming existence. | Must | ○ |
| **FR-2.5** | Authorization MUST be enforced server-side on every request. Client-side route guards are a usability affordance only and MUST NOT be the sole control. | Must | ○ |
| **FR-2.6** | Every authorization denial MUST be logged with the acting user, the attempted operation, and the target resource. | Should | ○ |

#### Role × Permission Matrix

| Operation | Staff | Manager | Admin | Org Coordinator |
|---|:---:|:---:|:---:|:---:|
| Register a shelf | ✔ | ✔ | — | — |
| View shelves and sensor readings | ✔ | ✔ | ✔ | — |
| Add an item to a shelf | ✔ | ✔ | — | — |
| View full item inventory (all statuses) | ✔ | ✔ | ✔ | — |
| View the near-expiry queue | ✔ | ✔ | — | — |
| Review a `needs_review` item | ✔ | ✔ | — | — |
| Publish an item to the donation network | ✔ | ✔ | — | — |
| Discard an item | ✔ | ✔ | — | — |
| Browse `available` items | — | — | — | ✔ |
| Reserve an item | — | — | — | ✔ |
| View own organization's reservations | — | — | — | ✔ |
| View all reservations | ✔ | ✔ | ✔ | — |
| Cancel own organization's reservation | — | — | — | ✔ |
| Cancel any reservation | — | ✔ | — | — |
| Scan a QR code to confirm pickup | ✔ | ✔ | — | — |
| Register an organization account | — | — | — | ✔ (self, pre-auth) |
| Verify an organization | — | — | ✔ | — |
| Create or deactivate a staff account | — | ✔ | — | — |
| Configure hold window / confidence threshold | — | ✔ | — | — |
| View donation history and reports | ✔ | ✔ | ✔ | ✔ (own only) |

*Verification:* For each row, authenticate as each role and confirm the operation succeeds only where marked. Attempt cross-organization access and confirm 404.

---

### FR-3 Staff Inventory Dashboard

> **Status: ◐ Partial.** `frontend/src/pages/StorePage.jsx` provides shelf creation, item creation, a flat item list, and a "Simulate OCR scan" button that posts a hardcoded 0.97 confidence (`StorePage.jsx:50-61`). There is no review queue, no near-expiry view, no filtering, and no QR scanning — although `GET /items/near-expiry` already exists on the backend and is unused by the client.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-3.1** | The dashboard MUST present a summary of inventory grouped by status, with counts for each. | Must | ○ |
| **FR-3.2** | The dashboard MUST provide a **review queue** listing all items in `needs_review`, sorted oldest first, showing for each the OCR raw text, the confidence score, the extracted date candidate, and the captured label image. | Must | ○ |
| **FR-3.3** | From the review queue, staff MUST be able to correct the sell-by date, correct or assign the SKU, and then either publish the item to the donation network or discard it — in a single action per item. | Must | ○ |
| **FR-3.4** | The dashboard MUST provide a **near-expiry view** listing items whose sell-by date falls within a configurable window, defaulting to 48 hours. | Must | ◐ *(backend exists: `app/routers/items.py:21-24`; no UI)* |
| **FR-3.5** | The item list MUST be filterable by status and by shelf, and sortable by sell-by date. | Should | ◐ *(backend supports `?status=`: `app/routers/items.py:16-18`)* |
| **FR-3.6** | The dashboard MUST display each shelf's current temperature, humidity, and the age of the last reading. A reading older than a configured staleness threshold MUST be visually flagged. | Should | ◐ *(data collected at `app/routers/shelves.py:21-28`; not displayed)* |
| **FR-3.7** | Staff MUST be able to register a shelf with a name, location, and optional camera identifier. | Must | ✅ *(`StorePage.jsx:70-89`, `app/routers/shelves.py:11-13`)* |
| **FR-3.8** | Staff MUST be able to add an item manually, specifying name, SKU, category, batch, shelf, and sell-by date. | Must | ◐ *(name, SKU, and shelf only: `StorePage.jsx:101-133`)* |
| **FR-3.9** | Item creation MUST reject a `shelf_id` that does not correspond to an existing shelf, returning 422. | Must | ○ *(currently unvalidated: `app/crud.py:51-56`)* |
| **FR-3.10** | The dashboard MUST provide a QR scanning interface for confirming pickup at the shelf, using the device camera where available and accepting manual token entry as a fallback. | Must | ✅ *(`StaffDashboard.jsx` `PickupScan`; camera via `BarcodeScanner` `mode="qr"`, manual entry behind a disclosure)* |
| **FR-3.11** | The "Simulate OCR scan" control MUST be removed from, or gated behind an explicit development flag in, any build served outside local development. | Must | ○ *(currently unconditional: `StorePage.jsx:144-151`)* |

*Verification:* Seed items in each status; confirm counts and queue contents. Correct a `needs_review` item end to end and confirm the resulting status and stored date. Confirm the near-expiry view matches `GET /items/near-expiry?within_hours=48`.

---

### FR-4 Shelf Registration and Environmental Monitoring

> **Status: ✅ Implemented** for ingest and storage. The collected temperature and humidity data is currently written and never read — no alerting, no history, no display.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-4.1** | The system MUST allow registration of a shelf with a name, an optional location description, and an optional camera identifier. | Must | ✅ *(`app/routers/shelves.py:11-13`)* |
| **FR-4.2** | The system MUST accept temperature and humidity readings for a registered shelf and record the time of receipt. | Must | ✅ *(`app/crud.py:31-42`)* |
| **FR-4.3** | A reading for an unregistered shelf MUST be rejected with 404. | Must | ✅ *(`app/routers/shelves.py:26-27`)* |
| **FR-4.4** | A reading submission containing only temperature or only humidity MUST update that field alone and leave the other unchanged. | Should | ✅ *(`app/crud.py:35-38`)* |
| **FR-4.5** | The system MUST retain a time series of readings, not only the latest value, so that cold-chain excursions can be reconstructed after the fact. | Should | ○ *(only current values are stored: `app/models.py:51-53`)* |
| **FR-4.6** | The system MUST raise an alert when a shelf's temperature leaves its configured safe range for longer than a configured duration. | Should | ○ |
| **FR-4.7** | The system MUST flag a shelf whose last reading is older than a configured staleness threshold, as this indicates sensor or network failure rather than safe conditions. | Should | ○ |
| **FR-4.8** | Items on a shelf with an unresolved temperature excursion MUST NOT be publishable to the donation network until staff explicitly confirm their safety. | Should | ○ |

*Verification:* Post readings for a known and an unknown shelf. Post a partial reading and confirm the untouched field. Simulate an excursion and confirm the alert and the publish block.

---

### FR-5 OCR / CV Label Pipeline and Confidence Branch

> **Status: ✅ Implemented** for the core branch, ◐ for the pipeline around it. `app/ocr.py` performs the Vision call and averages word-level confidence; `app/crud.py:93-116` applies the branch. The date extraction is regex-based and the SKU cross-check is substring matching — both flagged as prototype-grade in the source file's own docstring.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-5.1** | The system MUST extract text and a confidence score from a photographed product label. | Must | ✅ *(`app/ocr.py:50-89`)* |
| **FR-5.2** | When OCR confidence is **at or above 0.95** AND the SKU cross-check is confirmed, the system MUST set the item to `available` without human intervention. In every other case it MUST set the item to `needs_review`. | Must | ✅ *(`app/crud.py:109-112`)* |
| **FR-5.3** | The confidence threshold MUST be configurable without code changes. | Should | ◐ *(module constant, not runtime config: `app/crud.py:18`)* |
| **FR-5.4** | The system MUST persist the raw OCR text, the confidence score, and the SKU-match outcome against the item, so that a review decision can be audited afterward. | Must | ✅ *(`app/crud.py:103-105`; `app/models.py:77-79`)* |
| **FR-5.5** | The system MUST persist a reference to the captured label image, not the raw camera footage. | Must | ✅ *(`app/models.py:81`)* |
| **FR-5.6** | The system MUST extract candidate dates from label text and record the selected sell-by date. | Must | ◐ *(regex candidates at `app/ocr.py:36-40`; selection among multiple candidates is unspecified)* |
| **FR-5.7** | When the Vision API is unreachable or returns an error, the system MUST place the item in `needs_review` rather than failing silently or blocking the stocking workflow. | Must | ○ *(currently raises: `app/ocr.py:72-73`)* |
| **FR-5.8** | The mean-confidence aggregation MUST be supplemented by a minimum-confidence check on the tokens forming the extracted date. A high mean can conceal one badly misread but decisive token. | Should | ○ *(known limitation, documented at `app/ocr.py:55-59`)* |
| **FR-5.9** | The SKU cross-check SHOULD use barcode or UPC detection rather than literal substring matching against free text. | Should | ○ *(`app/ocr.py:92-105`)* |
| **FR-5.10** | The system MUST log every OCR result with its confidence and the branch taken, to support measurement of the false-accept and false-reject rates. | Must | ○ |

*Verification:* Unit-test `apply_ocr_result` at confidence 0.94/0.95/0.96 with SKU match true and false — six cases, expecting `available` only for (≥0.95, true). Submit a label photo to the live pipeline and confirm the stored raw text and confidence. Simulate a Vision API outage and confirm `needs_review`.

---

### FR-6 Item Lifecycle and Status Transitions

> **Status: ◐ Partial.** Eight states are defined at `app/models.py:26-34`. Two of them — `near_expiry` and `expired_hold` — are **never assigned by any code path**. `list_near_expiry` computes the near-expiry set by query instead (`app/crud.py:70-80`), and reservation expiry returns items to `available` rather than `expired_hold` (`app/crud.py:170-174`). This must be resolved: either the states get writers, or they are withdrawn.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-6.1** | Every item MUST hold exactly one status from the defined set at all times, defaulting to `in_stock` on creation. | Must | ✅ *(`app/models.py:74`)* |
| **FR-6.2** | The system MUST reject transitions not present in the state table in [Section 7](#7-item-lifecycle-state-machine), returning 409. | Must | ○ *(currently any status may be set from any other: `app/crud.py:83-90`)* |
| **FR-6.3** | The system MUST record the time of each status change. | Must | ◐ *(`updated_at` is maintained at `app/models.py:84`, but only the latest change survives)* |
| **FR-6.4** | The system MUST resolve the `near_expiry` state: either assign it to items whose sell-by date falls within the window, or withdraw it in favour of the computed query. **Recommendation: withdraw it** — a derived condition stored as state will drift from the date it derives from. | Must | ○ |
| **FR-6.5** | The system MUST resolve the `expired_hold` state. **Recommendation: use it** as a terminal state for items whose reservation lapsed *and* which staff subsequently judged no longer safe to re-offer, distinct from the automatic return to `available`. | Must | ○ |
| **FR-6.6** | An item MUST be reservable only from the `available` state. | Must | ✅ *(`app/crud.py:139-141`)* |
| **FR-6.7** | Staff MUST be able to move an item to `discarded` from any non-terminal state, without restriction and without requiring a reason, so that a food-safety judgment is never impeded by the software. | Must | ◐ *(possible via `PATCH /items/{id}/status`, but not surfaced as a distinct action)* |
| **FR-6.8** | Reaching a terminal state (`picked_up`, `discarded`, `expired_hold`) MUST be irreversible through the API. | Should | ○ |

*Verification:* Attempt each transition in and out of the state table and confirm acceptance or 409. Confirm an item cannot be reserved from any state but `available`.

---

### FR-7 Organization Registration and Verification

> **Status: ◐ Partial.** Registration works (`app/routers/pantries.py:11-18`). Verification does not: `verified` defaults to `False` (`app/models.py:98`) and **no code path ever sets it to `True`**. Critically, `create_reservation` does not check it (`app/crud.py:138-154`), so an unverified — or fraudulent — organization can reserve food today. The router's own docstring flags this as a pre-ship requirement.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-7.1** | An organization MUST be able to self-register with organization name, EIN, address, phone, and contact email. | Must | ◐ *(address and phone accepted by the API but absent from the form: `PantryPage.jsx:99-129`)* |
| **FR-7.2** | Contact email MUST be unique across organizations and MUST be validated as well-formed. | Must | ✅ *(`app/models.py:97`; `EmailStr` at `app/schemas.py:89`)* |
| **FR-7.3** | A newly registered organization MUST begin in an unverified state. | Must | ✅ *(`app/models.py:98`)* |
| **FR-7.4** | An unverified organization MUST NOT be able to reserve items. Reservation attempts MUST return 403 with an explanation of the pending verification. | Must | ○ **← security gap** |
| **FR-7.5** | A Platform Admin MUST be able to review a pending registration and mark it verified or rejected, with the decision, the deciding admin, and the timestamp recorded. | Must | ○ |
| **FR-7.6** | EIN MUST be validated for format (nine digits, `NN-NNNNNNN`) at registration, and MUST be unique across organizations. | Must | ○ *(currently a free-text string: `app/models.py:94`)* |
| **FR-7.7** | The system MUST notify the organization's contact email when verification is granted or refused. | Should | ○ |
| **FR-7.8** | An admin MUST be able to revoke verification, which MUST prevent new reservations while leaving existing ones intact. | Should | ○ |
| **FR-7.9** | The public organization listing MUST NOT expose EIN, phone, or contact email to non-admin users. | Must | ○ *(currently all fields are returned to anyone: `app/schemas.py:92-101`)* |

*Verification:* Register an organization and confirm `verified` is false. Attempt a reservation and confirm 403. Verify it as admin, retry, and confirm success. Confirm a duplicate email and a malformed EIN are both rejected.

---

### FR-8 Donation Discovery, Reservation, and Scheduling

> **Status: ✅ Built.** Scheduling exists. The relationship between the two times is the **inverse** of what earlier revisions of this section specified: rather than the organization picking a slot inside an independently-determined hold window, the organization picks the slot and the hold is derived from it — `hold_expires_at = scheduled_pickup_at + 30 minutes`. A slot may be booked up to 24 hours ahead, and never past the item's `discard_after`. `hold_minutes` is gone from the API; there is no longer a hold length to configure separately from the slot.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-8.1** | A verified organization MUST be able to list all items currently `available`. | Must | ✅ *(`GET /items?status=available`; `PantryPage.jsx:22`)* |
| **FR-8.2** | The available-items listing MUST show item name, category, sell-by date, and shelf location for each entry. | Must | ◐ *(name only in the UI: `PantryPage.jsx:145`)* |
| **FR-8.3** | The listing MUST be filterable by category and sortable by sell-by date. | Should | ○ |
| **FR-8.4** | An organization MUST be able to reserve an `available` item, which MUST make that item unavailable to every other organization immediately. | Must | ✅ *(`app/crud.py:139-151`)* |
| **FR-8.5** | ~~Reserving MUST start a hold window, defaulting to 180 minutes and configurable per request.~~ **Superseded by FR-8.6.** The hold is no longer independent of the pickup time, and no longer configurable per request: it is always `scheduled_pickup_at + 30 minutes`. A flat window from the moment of the click told staff nothing about when anyone would arrive, and left a no-show's item tied up for the balance of three hours. | Must | ⊘ superseded |
| **FR-8.6** | When reserving, the organization MUST select a **scheduled pickup time** at or after the current time and no more than **24 hours** ahead, and no later than the item's `discard_after`. The hold MUST be derived as `scheduled_pickup_at + 30 minutes`. A time outside the permitted range MUST be rejected with 422, and the message MUST name the boundary that was exceeded. | Must | ✅ *(`app/schemas.py` `ReservationCreate`, `PICKUP_GRACE`/`SCHEDULE_HORIZON`; the `discard_after` ceiling in `crud.create_reservation`)* |
| **FR-8.7** | An organization MUST be able to change its scheduled pickup time while the reservation is `pending`, subject to the same range constraint. ~~Changing the pickup time MUST NOT extend the hold window.~~ The struck clause is now unsatisfiable by construction: the hold **is** a function of the pickup time, so rescheduling necessarily moves it. Restate as *"rescheduling MUST NOT push `hold_expires_at` beyond `reserved_at + 24h`"* before building this. | Should | ○ *(not implemented; cancel and re-reserve is the workaround)* |
| **FR-8.8** | Reserving an item not in `available` MUST return 409 with a message distinguishing "already reserved" from "not yet eligible." | Must | ◐ *(409 returned with a combined message: `app/routers/reservations.py:19-22`)* |
| **FR-8.9** | Concurrent reservation attempts on the same item MUST result in exactly one success; the loser MUST receive 409. The check-and-claim MUST be atomic. | Must | ○ **← race condition**, see [NFR-4.7](#47-data-integrity) |
| **FR-8.10** | An organization MUST be able to view its own reservations, filtered by status, showing item, scheduled pickup time, hold expiry, and QR code. | Must | ✅ *(`GET /reservations/mine`; `OrganizerDashboard.jsx` `ReservationCard`)* |
| **FR-8.11** | An organization MUST be able to cancel its own `pending` reservation, which MUST return the item to `available` immediately. | Must | ○ *(`CANCELLED` is defined at `app/models.py:41` but is unreachable — no code sets it)* |
| **FR-8.12** | The system MUST warn an organization approaching its hold expiry with an uncollected reservation, at a configurable lead time. | Could | ○ |

*Verification:* Reserve as a verified organization and confirm the item leaves the available pool. Submit a `scheduled_pickup_at` in the past, one more than 24 hours ahead, and one past the item's `discard_after`; confirm all three are rejected with 422 and that the item is not left stranded in `reserved`. Submit a naive datetime and confirm it is rejected rather than read as UTC. Confirm `hold_expires_at` lands exactly 30 minutes after the accepted slot. Fire two simultaneous reservations at one item and confirm exactly one succeeds. Cancel and confirm the item returns. Covered by `backend/tests/test_reservation_scheduling.py`.

---

### FR-9 QR Code Generation and Pickup Verification

> **Status: ◐ Mostly built.** The token is minted with `secrets.token_urlsafe(16)` (128 bits), rendered as a QR on the organizer's phone, and redeemed by store staff scanning it with the device camera. The hold expiry is now checked at scan time rather than left to the sweep, and failures name their reason. One gap remains: the token still travels as a URL path parameter (FR-9.9).

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-9.1** | Every reservation MUST carry a unique, unguessable token generated with a cryptographically secure random source, of at least 128 bits of entropy. | Must | ✅ *(`app/crud.py:148`; uniqueness enforced at `app/models.py:116`)* |
| **FR-9.2** | The token MUST be single-use. A token already redeemed MUST be rejected. | Must | ✅ *(`app/crud.py:181`)* |
| **FR-9.3** | The organization portal MUST render the token as a scannable QR image, sized and contrasted to be readable on a phone screen in store lighting. | Must | ✅ *(`OrganizerDashboard.jsx` `<QRCodeSVG size={200} level="M">`)* |
| **FR-9.4** | Pickup confirmation MUST be performed by authenticated store staff. An organization MUST NOT be able to confirm its own pickup. | Must | ✅ *(`Depends(auth.require_staff)`; an organizer token gets 403)* |
| **FR-9.5** | Scanning a valid token MUST mark the reservation `picked_up`, record the pickup time, and set the item to `picked_up`. | Must | ✅ *(`app/crud.py:182-187`)* |
| **FR-9.6** | Scanning MUST fail for a token whose reservation is not `pending` — already collected, expired, or cancelled — and the failure message MUST state which. | Must | ✅ *(`crud.confirm_pickup` returns a named outcome; the router maps each to its own message)* |
| **FR-9.7** | Scanning a reservation past its `hold_expires_at` but not yet swept by the scheduler MUST be treated as expired, not accepted. The expiry time governs, not the sweep. | Must | ✅ *(`crud.confirm_pickup` compares against `hold_expires_at` and releases the item on the spot)* |
| **FR-9.8** | Repeated confirmation of the same token MUST be idempotent from the staff member's perspective: the second scan reports "already collected at HH:MM" rather than an unexplained error. | Should | ✅ |
| **FR-9.9** | The token MUST NOT appear in URL paths, query strings, or server access logs. | Should | ○ *(currently a path parameter: `POST /reservations/pickup/{qr_code}`)* |
| **FR-9.10** | Staff MUST be able to enter the token manually when a scan fails. | Must | ✅ *("Can't scan? Enter code" disclosure on the Confirm pickup card)* |

*Verification:* Confirm a pickup and confirm both the reservation and the item reach `picked_up`. Re-scan and confirm rejection names "already collected." Scan a token whose hold has expired but which the scheduler has not swept, and confirm both the rejection and that the item returns to `available`. Confirm an organizer token is refused. Covered by `backend/tests/test_pickup_expiry.py`. The token still appears in the request path (FR-9.9).

---

### FR-10 Reservation Expiry and Release

> **Status: ◐ Partial.** The logic is correct (`app/crud.py:157-176`) but nothing runs it. `POST /reservations/expire-stale` is an unauthenticated endpoint someone must remember to call (`app/routers/reservations.py:26-33`); its own docstring says "trigger manually for now."

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-10.1** | A `pending` reservation past its `hold_expires_at` MUST be set to `expired`, and its item MUST return to `available`. | Must | ✅ *(`app/crud.py:163-175`)* |
| **FR-10.2** | Expiry MUST run automatically on a schedule, at an interval no longer than 5 minutes. | Must | ○ *(manual trigger only)* |
| **FR-10.3** | The expiry sweep MUST be idempotent and safe to run concurrently with itself; a reservation MUST NOT be expired twice, and an item MUST NOT be released twice. | Must | ○ *(no locking: `app/crud.py:163-175`)* |
| **FR-10.4** | An item returned to `available` by expiry MUST be immediately reservable by another organization. | Must | ✅ |
| **FR-10.5** | The expiry trigger endpoint MUST require authentication and MUST be restricted to system or manager roles. | Must | ○ *(currently open to anyone)* |
| **FR-10.6** | The system MUST notify an organization when one of its reservations expires uncollected. | Should | ○ |
| **FR-10.7** | The system MUST record a count of expired-uncollected reservations per organization, so repeated no-shows can be identified. | Could | ○ |

*Verification:* Create a reservation with `hold_minutes=1`, wait, run the sweep, and confirm the reservation is `expired` and the item is `available`. Run the sweep twice in succession and confirm the second is a no-op. Run two sweeps concurrently and confirm no double release.

---

### FR-11 Reporting and Audit Trail

> **Status: ○ Proposed.** No reporting exists. This group is Could-have overall, with the exception of FR-11.1, which supports the Good Samaritan Act's good-faith documentation ([NFR-4.8](#48-legal-and-food-safety-compliance)) and is therefore Must-have.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **FR-11.1** | The system MUST retain an immutable record of every completed donation: item, organization, staff member confirming, timestamp, and the item's sell-by date at the time of donation. | Must | ○ |
| **FR-11.2** | Staff MUST be able to view donation history filtered by date range and organization. | Should | ○ |
| **FR-11.3** | An organization MUST be able to view and export its own collection history, for its own grant and impact reporting. | Should | ○ |
| **FR-11.4** | The system SHOULD report diverted food volume over a date range, as the system's primary impact metric. | Could | ○ |
| **FR-11.5** | The system SHOULD report OCR accuracy — auto-published count, review-queue count, and corrections made during review — to support tuning the confidence threshold. | Could | ○ |
| **FR-11.6** | Every status change, authorization denial, and verification decision MUST be written to an append-only audit log. | Should | ○ |

*Verification:* Complete a donation and confirm the record. Attempt to modify a historical record through the API and confirm rejection.

---

## 4. Non-Functional Requirements

### 4.1 Security

| ID | Requirement |
|---|---|
| **NFR-4.1.1** | Passwords MUST be hashed with bcrypt (cost ≥ 12) or Argon2id. |
| **NFR-4.1.2** | All traffic MUST use TLS 1.2 or later in any deployed environment. |
| **NFR-4.1.3** | `allow_origins=["*"]` MUST be replaced with an explicit allowlist of deployed frontend origins. The current setting (`app/main.py:19-24`) combined with credentialed requests would permit any website to act on a logged-in user's behalf. |
| **NFR-4.1.4** | Login endpoints MUST be rate-limited per source address and per account. |
| **NFR-4.1.5** | The pickup confirmation endpoint MUST be rate-limited to defeat token brute-forcing. At 128 bits of entropy the search space is infeasible, but rate limiting bounds the damage from any future reduction in token length. |
| **NFR-4.1.6** | Reservation tokens MUST NOT be logged, and MUST NOT be transmitted as URL path or query components ([FR-9.9](#fr-9-qr-code-generation-and-pickup-verification)). |
| **NFR-4.1.7** | All input MUST be validated at the API boundary via Pydantic schemas. Database access MUST use parameterized queries — satisfied today by the exclusive use of the SQLAlchemy ORM. |
| **NFR-4.1.8** | Dependencies MUST be pinned to specific versions. `requirements.txt` currently pins nothing, so any build may silently acquire a different — or compromised — version. |
| **NFR-4.1.9** | Secrets MUST be supplied by environment variables and MUST NOT be committed. Note that `.env` files are present in both the backend and frontend directories; confirm they are covered by `.gitignore` before any push. |

### 4.2 Privacy

| ID | Requirement |
|---|---|
| **NFR-4.2.1** | The system MUST store still frames of product labels only, never continuous camera footage. |
| **NFR-4.2.2** | Label capture MUST be framed to exclude shoppers and staff. Any frame containing an identifiable person MUST be discarded rather than stored. |
| **NFR-4.2.3** | Captured label images MUST be deleted once the item reaches a terminal state and the audit retention period has passed. Default retention: 90 days. |
| **NFR-4.2.4** | Organization contact details MUST be visible only to that organization and to admin and staff roles ([FR-7.9](#fr-7-organization-registration-and-verification)). |
| **NFR-4.2.5** | The system MUST NOT collect any data about individual shoppers. |

### 4.3 Performance

| ID | Requirement |
|---|---|
| **NFR-4.3.1** | The OCR round trip — image submitted to status updated — SHOULD complete within 5 seconds at the 95th percentile. |
| **NFR-4.3.2** | The staff dashboard SHOULD render its initial view within 2 seconds over a typical store network with 500 items in inventory. |
| **NFR-4.3.3** | The system MUST accept sensor readings from at least 50 shelves at a 30-second interval without degradation. |
| **NFR-4.3.4** | Reservation creation MUST complete within 500 ms at the 95th percentile; a slow claim widens the race window described in [NFR-4.7](#47-data-integrity). |
| **NFR-4.3.5** | The available-items listing MUST paginate beyond 100 entries. It currently returns the full set unbounded (`app/crud.py:63-67`). |

### 4.4 Reliability and Availability

| ID | Requirement |
|---|---|
| **NFR-4.4.1** | The expiry sweep MUST be safe to run repeatedly and concurrently ([FR-10.3](#fr-10-reservation-expiry-and-release)). |
| **NFR-4.4.2** | Pickup confirmation MUST be idempotent from the operator's point of view ([FR-9.8](#fr-9-qr-code-generation-and-pickup-verification)). |
| **NFR-4.4.3** | Loss of the Vision API MUST degrade the system to manual review, never block stocking ([FR-5.7](#fr-5-ocr--cv-label-pipeline-and-confidence-branch)). |
| **NFR-4.4.4** | Loss of sensor connectivity MUST surface as a stale-reading flag, never as an implied "conditions normal" ([FR-4.7](#fr-4-shelf-registration-and-environmental-monitoring)). |
| **NFR-4.4.5** | The database MUST be backed up daily, with a restore procedure exercised before production use. |
| **NFR-4.4.6** | Target availability during store hours is 99%. |

### 4.5 Usability and Accessibility

| ID | Requirement |
|---|---|
| **NFR-4.5.1** | Both interfaces MUST meet WCAG 2.1 Level AA [R6]. |
| **NFR-4.5.2** | Item status MUST be conveyed by text label, not by colour alone. |
| **NFR-4.5.3** | The organization portal MUST be usable on a phone held one-handed, as the QR code is presented in a store aisle. |
| **NFR-4.5.4** | The QR code MUST render at a minimum of 200 × 200 CSS pixels at maximum contrast, and the portal SHOULD raise screen brightness while it is displayed. |
| **NFR-4.5.5** | Every error message MUST state what happened and what the user can do about it. "Item is not available to reserve" satisfies this; a bare 409 does not. |
| **NFR-4.5.6** | The staff review queue MUST be operable by keyboard alone, since it is a repetitive high-volume task. |

### 4.6 Scalability

| ID | Requirement |
|---|---|
| **NFR-4.6.1** | The schema has **no store or tenant identifier**. Shelf, Item, and Reservation all assume a single store (`app/models.py`). Multi-store support requires adding `store_id` to Shelf and scoping every query — a change to make deliberately, before there is production data. |
| **NFR-4.6.2** | Production MUST use PostgreSQL, not SQLite. SQLite's write locking will not survive concurrent reservation traffic. |
| **NFR-4.6.3** | Indexes MUST exist on `Item.status`, `Item.sell_by_date`, `Reservation.status`, and `Reservation.hold_expires_at` — the columns the hot queries filter on. Only `Item.sku` is indexed today (`app/models.py:63`). |
| **NFR-4.6.4** | The system MUST adopt Alembic migrations before holding data that cannot be dropped ([C-5](#25-design-and-implementation-constraints)). |

### 4.7 Data Integrity

| ID | Requirement |
|---|---|
| **NFR-4.7.1** | ~~**Known race condition.**~~ **Fixed.** `create_reservation` claims the item with a conditional update (`UPDATE items SET status='reserved' WHERE id=? AND status='available'`) and treats a zero-row result as the 409, so the database picks the winner of a concurrent claim. |
| **NFR-4.7.2** | Item status transitions MUST be validated against the state table ([FR-6.2](#fr-6-item-lifecycle-and-status-transitions)). |
| **NFR-4.7.3** | `scheduled_pickup_at` MUST be constrained at the API layer, and SHOULD be at the database layer where the engine allows it. **Partially met.** The API enforces the full rule (not in the past, within 24 hours, not past `discard_after`, hold derived as slot + 30 min). There is no database CHECK: SQLite cannot add one to an existing table, so a constraint would hold in production and not in development — worse than none, because it would only ever fail where it is hardest to debug. Revisit when Alembic lands (NFR-4.6.4) and Postgres-only DDL becomes expressible. |
| **NFR-4.7.4** | Foreign keys MUST be enforced. SQLite requires `PRAGMA foreign_keys=ON` per connection — verify this is set, or the referential integrity the schema declares is not actually enforced in development. |
| **NFR-4.7.5** | All timestamps MUST be stored in UTC. The codebase consistently uses `datetime.utcnow()`; note that this function is deprecated in Python 3.12 and SHOULD be migrated to `datetime.now(timezone.utc)`. |

### 4.8 Legal and Food-Safety Compliance

| ID | Requirement |
|---|---|
| **NFR-4.8.1** | The system MUST retain donation records sufficient to evidence good-faith donation under the Bill Emerson Good Samaritan Food Donation Act [R2] — what was donated, to which verified nonprofit, when, and its date status at handoff ([FR-11.1](#fr-11-reporting-and-audit-trail)). |
| **NFR-4.8.2** | The system MUST distinguish sell-by from use-by dates. Items bearing a use-by date MUST NOT be auto-published past that date, regardless of OCR confidence. The current model stores only `sell_by_date` (`app/models.py:68`); this distinction needs a field. |
| **NFR-4.8.3** | Recipients MUST be verified nonprofits ([FR-7.4](#fr-7-organization-registration-and-verification)) — the Act's protection depends on it. |
| **NFR-4.8.4** | The system MUST NOT prevent or delay staff from discarding an item they judge unsafe ([FR-6.7](#fr-6-item-lifecycle-and-status-transitions)). |
| **NFR-4.8.5** | The system MUST record cold-chain conditions for temperature-sensitive donations ([FR-4.5](#fr-4-shelf-registration-and-environmental-monitoring)). |
| **NFR-4.8.6** | Product categories subject to donation restrictions — infant formula in particular — MUST be excluded from auto-publication and require explicit staff approval. |

---

## 5. Use Cases

### UC-01 — Staff Member Logs In

| | |
|---|---|
| **Primary actor** | Store Staff |
| **Stakeholders** | Store — needs assurance that only authorized employees alter inventory |
| **Preconditions** | The user has an active staff account |
| **Trigger** | The user opens the application |
| **Related** | FR-1.2, FR-1.3, FR-1.4, FR-1.5, FR-1.8, FR-2.1 |

**Main success scenario**

1. The system presents a login form requesting email and password.
2. The staff member submits their credentials.
3. The system verifies the email against an active account and the password against the stored hash.
4. The system issues an access token with an 8-hour lifetime.
5. The system routes the user to the inventory dashboard.

**Alternate flows**

- **3a. Credentials are wrong.** The system returns a generic "Email or password is incorrect," increments the failure counter, and returns to step 1.
- **3b. Five failures within 15 minutes.** The system locks the account for the cooling-off period, logs the event, and shows the lockout duration.
- **3c. The account is deactivated.** The system returns the same generic message as 3a — deactivation is not disclosed at the login boundary.

**Exception flows**

- **E1. Authentication service unavailable.** The system reports a service error and does not admit the user. It MUST NOT fail open.

**Postconditions** — A session exists for the staff member; `last_login_at` is updated.

---

### UC-02 — Organization Coordinator Logs In

| | |
|---|---|
| **Primary actor** | Organization Coordinator |
| **Preconditions** | The coordinator has an active account associated with a registered organization |
| **Trigger** | The coordinator opens the application |
| **Related** | FR-1.2, FR-1.4, FR-2.2, FR-2.3, FR-7.4 |

**Main success scenario**

1. The coordinator submits their credentials to the same login form as UC-01.
2. The system verifies them.
3. The system resolves the coordinator's associated organization.
4. The system issues an access token carrying the role and organization.
5. The system routes the coordinator to the donation portal.

**Alternate flows**

- **3a. The organization is not yet verified.** The coordinator is admitted and may browse available items, but the portal displays a pending-verification banner and reservation controls are disabled with an explanation.
- **3b. Verification has been revoked.** As 3a, with the reason and a contact route for appeal.

**Postconditions** — A session exists, bound to exactly one organization. The coordinator never selects which organization they act for — it is derived from the account (FR-2.3).

---

### UC-03 — Organization Registers for an Account

| | |
|---|---|
| **Primary actor** | Organization Coordinator |
| **Stakeholders** | Store (needs recipients to be legitimate nonprofits for liability protection); Platform Admin (performs the check) |
| **Preconditions** | None — this is a pre-authentication flow |
| **Trigger** | A nonprofit wishes to join the donation network |
| **Related** | FR-7.1, FR-7.2, FR-7.3, FR-7.6, FR-7.7 |

**Main success scenario**

1. The coordinator opens the registration form.
2. They submit organization name, EIN, address, phone, contact email, and a password.
3. The system validates the EIN format and the email format, and confirms both are unique.
4. The system creates the organization with `verified = false` and a coordinator account bound to it.
5. The system confirms registration and states that reservation is unavailable pending verification.
6. The system notifies Platform Admins that a registration awaits review.

**Alternate flows**

- **3a. Email already registered.** The system reports the conflict and offers password recovery.
- **3b. EIN already registered.** The system reports that the organization exists and directs the user to their administrator rather than creating a duplicate.
- **3c. EIN format invalid.** The system reports the expected format inline.

**Postconditions** — An unverified organization and its coordinator account exist. No reservation is possible until UC-04 completes.

---

### UC-04 — Admin Verifies an Organization

| | |
|---|---|
| **Primary actor** | Platform Admin |
| **Preconditions** | An unverified organization exists; the admin is authenticated |
| **Trigger** | A registration awaits review |
| **Related** | FR-7.4, FR-7.5, FR-7.7, FR-7.8, FR-2.6 |

**Main success scenario**

1. The admin opens the pending-verification queue.
2. The admin selects an organization and reviews its name, EIN, address, and contact details.
3. The admin checks the EIN against public IRS nonprofit records (a manual step in this revision — see A-4).
4. The admin marks the organization verified.
5. The system sets `verified = true` and records the deciding admin and the timestamp.
6. The system emails the coordinator that reservation is now available.

**Alternate flows**

- **4a. The admin rejects.** The system records the rejection with a reason, leaves `verified` false, and notifies the coordinator with the reason and an appeal route.
- **4b. The admin needs more information.** The system records a request-for-information state and emails the coordinator; the registration stays pending.

**Postconditions** — The organization is verified or rejected; the decision is auditable.

> **Implementation note.** This use case has no implementation whatsoever. `verified` is never set true by any code path, and `create_reservation` never reads it. Until FR-7.4 and this use case are built, the verification gate the design depends on does not exist.

---

### UC-05 — Reset a Forgotten Password

| | |
|---|---|
| **Primary actor** | Any authenticated human role |
| **Preconditions** | The user has an active account |
| **Trigger** | The user cannot log in |
| **Related** | FR-1.7, FR-1.3 |

**Main success scenario**

1. The user selects "Forgot password" and submits their email.
2. The system responds identically whether or not the account exists.
3. If an account exists, the system emails a single-use token valid for 60 minutes.
4. The user opens the link and sets a new password meeting the strength policy.
5. The system stores the new hash, invalidates the reset token, and invalidates all existing sessions for that user.
6. The user logs in with the new password.

**Alternate flows**

- **4a. The token has expired or was already used.** The system reports this and offers to send a new one.
- **4b. The new password fails the strength policy.** The system states the unmet requirement.

**Postconditions** — The password is changed; prior sessions are terminated.

---

### UC-06 — Shelf Posts an Environmental Reading

| | |
|---|---|
| **Primary actor** | Sensor Bridge |
| **Preconditions** | The shelf is registered; the bridge holds valid service credentials |
| **Trigger** | The bridge's fixed reporting interval elapses |
| **Related** | FR-4.2, FR-4.3, FR-4.4, FR-4.5, FR-4.6, FR-4.7 |

**Main success scenario**

1. The bridge reads temperature and humidity from the sensor.
2. The bridge posts them to `PATCH /shelves/{id}/reading`.
3. The system authenticates the service credential and confirms the shelf exists.
4. The system updates the shelf's current values and sets `last_reading_at` to now.
5. The system appends the reading to the shelf's time series (FR-4.5).
6. The system responds with the updated shelf.

**Alternate flows**

- **3a. Shelf not found.** 404. The bridge logs the misconfiguration and continues with its other shelves.
- **4a. Only one of the two values is present.** The system updates that value alone and leaves the other unchanged (`app/crud.py:35-38`).
- **5a. The reading is outside the safe range.** The system starts or continues an excursion timer; if the excursion exceeds its configured duration, the system raises an alert and blocks publication of items on that shelf (FR-4.6, FR-4.8).

**Exception flows**

- **E1. The bridge cannot reach the API.** The reading is lost — there is no offline buffer (C-2). The shelf's `last_reading_at` ages, and FR-4.7's staleness flag is what surfaces the outage. This is precisely why staleness must be flagged rather than treated as "no news is good news."

**Postconditions** — The shelf reflects current conditions, or is flagged stale.

---

### UC-07 — Staff Registers a Shelf and Stocks an Item

| | |
|---|---|
| **Primary actor** | Store Staff |
| **Preconditions** | The staff member is authenticated |
| **Trigger** | A new shelf enters service, or a delivery arrives |
| **Related** | FR-3.7, FR-3.8, FR-3.9, FR-4.1, FR-6.1 |

**Main success scenario**

1. The staff member opens the inventory dashboard.
2. They register a shelf with a name, a location description, and the identifier of the camera watching it.
3. The system creates the shelf and returns it.
4. The staff member adds an item, specifying name, SKU, category, batch, shelf, and — if known — sell-by date.
5. The system validates that the shelf exists, creates the item with status `in_stock`, and returns it.
6. The item appears in the dashboard inventory list.

**Alternate flows**

- **4a. Sell-by date is unknown.** The item is created without one; the OCR pipeline supplies it in UC-08.
- **4b. SKU is unknown.** The item is created without one; the cross-check resolves it in UC-08.
- **5a. The shelf does not exist.** 422 with the offending field named (FR-3.9).

**Postconditions** — An `in_stock` item exists on a registered shelf, ready for label capture.

---

### UC-08 — System Reads a Label and Auto-Publishes It

| | |
|---|---|
| **Primary actor** | OCR Pipeline |
| **Stakeholders** | Store (wants automation); recipients (need the date read correctly) |
| **Preconditions** | An item exists; its label has been photographed; the Vision API is reachable |
| **Trigger** | A label image is captured for the item |
| **Related** | FR-5.1, FR-5.2, FR-5.4, FR-5.5, FR-5.6, FR-5.10, FR-6.1 |

**Main success scenario**

1. The camera captures a still frame of the item's label.
2. The pipeline submits the image to Google Cloud Vision `document_text_detection`.
3. Vision returns the full text and per-word confidence scores.
4. The pipeline computes the mean word-level confidence (`app/ocr.py:77-85`).
5. The pipeline extracts date candidates by pattern match (`app/ocr.py:87`).
6. The pipeline cross-checks the text against the store's SKU catalog (`app/ocr.py:92-105`).
7. The pipeline posts raw text, confidence, SKU-match outcome, and the selected date to `POST /items/{id}/ocr-result`.
8. The system persists all four values against the item (`app/crud.py:103-107`).
9. Confidence is **≥ 0.95** and the SKU match is **confirmed**, so the system sets the item to `available` (`app/crud.py:109-110`).
10. The item appears in the donation network and is visible to verified organizations.

**Alternate flows**

- **9a. Confidence below threshold, or SKU unconfirmed, or both.** The system sets `needs_review` (`app/crud.py:111-112`) and the item enters the staff review queue — continue at UC-09. This is the branch that runs whenever the automated read is not trustworthy, and it is the design's central safeguard.
- **5a. No date candidate found.** The confidence branch still applies, but with no date the item cannot be published; it goes to review regardless.
- **5b. Multiple date candidates found.** Selection among them is currently unspecified (FR-5.6). Until specified, the item SHOULD go to review.

**Exception flows**

- **E1. Vision API returns an error or is unreachable.** Per FR-5.7 the item MUST go to `needs_review` and stocking MUST continue. The current implementation raises a `RuntimeError` instead (`app/ocr.py:72-73`) — an open defect.
- **E2. The item ID does not exist.** 404; the pipeline logs and moves on.
- **E3. The label is illegible.** Vision returns little or no text and low confidence; the branch routes to review, which is the correct outcome.

**Postconditions** — The item is `available` or `needs_review`; the OCR evidence is stored and auditable either way.

---

### UC-09 — Staff Reviews a Low-Confidence Label Read

| | |
|---|---|
| **Primary actor** | Store Staff |
| **Stakeholders** | Recipients — this review is the human check standing between an uncertain machine read and donated food |
| **Preconditions** | At least one item is in `needs_review`; the staff member is authenticated |
| **Trigger** | UC-08 alternate flow 9a placed an item in the queue |
| **Related** | FR-3.2, FR-3.3, FR-5.4, FR-6.2, FR-6.7, FR-11.5 |

**Main success scenario**

1. The staff member opens the review queue, sorted oldest first.
2. For each item the system displays the captured label image, the raw OCR text, the confidence score, the extracted date candidates, and the SKU-match outcome.
3. The staff member reads the physical label or its image and determines the correct sell-by date.
4. They confirm or correct the date and the SKU.
5. They judge the item safe to donate and publish it.
6. The system sets the item to `available` and records the reviewing staff member, the timestamp, and the values as corrected.
7. The item leaves the queue and enters the donation network.

**Alternate flows**

- **5a. The item is not safe to donate.** The staff member discards it; the system sets `discarded` and the item leaves the queue (FR-6.7).
- **5b. The item is still saleable.** The staff member returns it to `in_stock`; the system re-queues it for capture at a later date.
- **3a. The label is unreadable in the stored image.** The staff member requests a re-capture; the item stays queued.
- **4a. The corrected date is already past a use-by threshold.** The system warns and requires explicit confirmation before publication (NFR-4.8.2, NFR-4.8.6).

**Exception flows**

- **E1. Two staff members review the same item concurrently.** The first decision wins; the second receives a conflict notice stating the outcome, and the item is removed from their queue.

**Postconditions** — The item has left `needs_review`; the correction is recorded and feeds the accuracy metrics in FR-11.5.

> **Implementation note.** No review queue interface exists. Items enter `needs_review` today and have no path out except a raw `PATCH /items/{id}/status` call. This use case is the highest-value unbuilt piece of the staff dashboard.

---

### UC-10 — Staff Monitors the Near-Expiry Queue

| | |
|---|---|
| **Primary actor** | Store Staff |
| **Preconditions** | The staff member is authenticated; items with sell-by dates exist |
| **Trigger** | Routine shift check |
| **Related** | FR-3.4, FR-3.5, FR-6.4 |

**Main success scenario**

1. The staff member opens the near-expiry view.
2. The system queries items whose `sell_by_date` falls within the window (default 48 hours) and whose status is `in_stock` or `near_expiry` (`app/crud.py:70-80`).
3. The system displays them sorted by sell-by date, soonest first.
4. The staff member triggers label capture for items due to move to donation, entering UC-08.

**Alternate flows**

- **2a. The staff member widens or narrows the window.** The system re-queries with the supplied `within_hours`.
- **2b. Nothing is near expiry.** The system says so plainly.

**Postconditions** — Staff know what needs attention; items may have entered the OCR pipeline.

---

### UC-11 — Staff Discards an Unsafe Item

| | |
|---|---|
| **Primary actor** | Store Staff |
| **Preconditions** | The item exists and is not in a terminal state |
| **Trigger** | Staff judge the item unsafe or unusable |
| **Related** | FR-6.7, FR-6.8, NFR-4.8.4 |

**Main success scenario**

1. The staff member locates the item.
2. They select Discard.
3. The system sets the item to `discarded` immediately, without requiring justification.
4. The system records the acting staff member and the timestamp.

**Alternate flows**

- **1a. The item is currently reserved.** The system warns that an organization holds it, requires confirmation, then discards the item and cancels the reservation, notifying the organization with the reason.

**Postconditions** — The item is terminal and cannot re-enter the donation network.

> **Design note.** The software must never impede this action. A staff member holding a container they believe is spoiled should not be negotiating with a form (NFR-4.8.4).

---

### UC-12 — Organization Browses Available Donations

| | |
|---|---|
| **Primary actor** | Organization Coordinator |
| **Preconditions** | The coordinator is authenticated; their organization is verified |
| **Trigger** | The coordinator opens the portal |
| **Related** | FR-8.1, FR-8.2, FR-8.3, FR-7.4, NFR-4.3.5 |

**Main success scenario**

1. The coordinator opens the donation portal.
2. The system lists all items with status `available`.
3. Each entry shows name, category, sell-by date, shelf location, and time posted.
4. The coordinator filters by category and sorts by sell-by date.

**Alternate flows**

- **2a. The organization is unverified.** Items are listed but reservation controls are disabled, with the pending-verification reason shown (UC-02 alternate 3a).
- **2b. Nothing is available.** The system says so, rather than showing an empty table.
- **2c. More than 100 items.** The system paginates (NFR-4.3.5).

**Postconditions** — No state changes. This use case is read-only.

---

### UC-13 — Organization Reserves an Item and Schedules Pickup

| | |
|---|---|
| **Primary actor** | Organization Coordinator |
| **Stakeholders** | Store (needs a reliable arrival time); other organizations (are excluded for the hold duration) |
| **Preconditions** | The coordinator is authenticated; their organization is verified; the item is `available` |
| **Trigger** | The coordinator selects an item to collect |
| **Related** | FR-8.4, FR-8.5, FR-8.6, FR-8.8, FR-8.9, FR-9.1, FR-2.3, NFR-4.7.1 |

**Main success scenario**

1. The coordinator selects an available item and chooses Reserve.
2. The system determines the acting organization from the authenticated session — **not** from any client-supplied value (FR-2.3).
3. The system asks for a **scheduled pickup time**, offering any slot from now up to 24 hours ahead, and no later than the item's `discard_after`.
4. The coordinator selects a date and time.
5. The system validates the slot against that range, rejecting a naive datetime outright so a browser cannot submit local wall time as UTC (FR-8.6).
6. The system derives the hold: `hold_expires_at = scheduled_pickup_at + 30 minutes`.
7. The system atomically claims the item — transitioning it from `available` to `reserved` only if it is still `available` (NFR-4.7.1).
8. The system creates the reservation with status `pending` and mints a 128-bit token (`app/crud.py:148`).
9. The system returns the reservation with its QR code, scheduled pickup time, and hold expiry.
10. The portal renders the QR code (FR-9.3) and confirms the arrangement.

**Alternate flows**

- **5a. The chosen time is outside the permitted range.** 422, naming the boundary that was exceeded — the 24-hour horizon or the item's discard deadline. The claim is rolled back, so no reservation is created and the item does not linger in `reserved`.
- **7a. The item was claimed between listing and reserving.** 409 stating that another organization reserved it; the listing refreshes. This is the expected outcome of the atomic claim in step 7 and must not be an error the coordinator has to interpret.
- **1a. The organization is unverified.** 403 (FR-7.4).
- **5b. The coordinator wants a longer hold.** Not available: the hold is a function of the pickup time, and 24 hours is the ceiling. They may book a later slot instead, subject to the item's discard deadline.

**Exception flows**

- **E1. Concurrent reservation.** Two coordinators reserve the same item simultaneously. Exactly one MUST succeed. **The current implementation does not guarantee this** — `create_reservation` reads status then writes without a lock (`app/crud.py:139-153`), so both can succeed, producing two valid QR codes for one physical item and a dispute at the shelf. See NFR-4.7.1 for the fix.
- **E2. The item is discarded mid-reservation.** UC-11 alternate 1a cancels the reservation and notifies the coordinator.

**Postconditions** — The item is `reserved` and invisible to other organizations. A `pending` reservation exists with a scheduled pickup time, a hold expiry, and a single-use token.

---

### UC-14 — Organization Views Its Reservations and QR Code

| | |
|---|---|
| **Primary actor** | Organization Coordinator |
| **Preconditions** | The coordinator is authenticated and holds at least one reservation |
| **Trigger** | The coordinator prepares to collect, or checks their commitments |
| **Related** | FR-8.10, FR-9.3, FR-2.4, NFR-4.5.3, NFR-4.5.4 |

**Main success scenario**

1. The coordinator opens their reservations list.
2. The system returns reservations belonging to their organization only, derived from the session (FR-2.4).
3. Each entry shows the item, the scheduled pickup time, the hold expiry, the status, and time remaining.
4. The coordinator opens a `pending` reservation.
5. The system renders the token as a QR code at least 200 × 200 pixels at maximum contrast.
6. The coordinator presents the screen to staff at collection, entering UC-15.

**Alternate flows**

- **3a. A reservation is `expired`.** It is shown with its outcome and is no longer actionable.
- **5a. The screen is dim or the code will not scan.** The token is also shown as readable text for the manual fallback in FR-9.10.

**Exception flows**

- **E1. The coordinator requests another organization's reservation.** 404, not 403 — the response must not confirm that the record exists (FR-2.4).

**Postconditions** — No state change. Read-only.

> **Implementation note.** The portal currently keeps only the single most recent reservation in client state (`PantryPage.jsx:157-164`) and shows the token as raw text. There is no per-organization reservation listing endpoint; `GET /reservations?pantry_id=` is proposed in [Section 8](#8-api-specification).

---

### UC-15 — Staff Scans a QR Code to Release Food

| | |
|---|---|
| **Primary actor** | Store Staff |
| **Stakeholders** | Store (must document the donation); organization (must receive the food) |
| **Preconditions** | The staff member is authenticated; the coordinator is present with a `pending` reservation |
| **Trigger** | The coordinator arrives to collect |
| **Related** | FR-9.2, FR-9.4, FR-9.5, FR-9.6, FR-9.7, FR-9.8, FR-9.10, FR-11.1 |

**Main success scenario**

1. The coordinator presents the QR code from UC-14.
2. The staff member scans it with the dashboard's scanning interface.
3. The system looks up the reservation by token (`app/crud.py:180`).
4. The system confirms the reservation is `pending` and that the current time is at or before `hold_expires_at` (FR-9.7).
5. The system sets the reservation to `picked_up` and records `picked_up_at` (`app/crud.py:183-184`).
6. The system sets the item to `picked_up` (`app/crud.py:185-187`).
7. The system writes the immutable donation record: item, organization, confirming staff member, timestamp, and the item's sell-by date at handoff (FR-11.1).
8. The dashboard confirms the item and the organization; the staff member hands over the food.

**Alternate flows**

- **2a. The code will not scan.** The staff member enters the token manually (FR-9.10); the flow continues at step 3.
- **4a. The reservation is already `picked_up`.** The system reports "already collected at HH:MM" and does not alter the record (FR-9.8).
- **4b. The reservation is `expired`.** The system reports the expiry time and that the item was returned to the pool. If the item is still `available`, the staff member may re-reserve it for the present coordinator on the spot.
- **4c. The reservation is `cancelled`.** The system reports the cancellation and its time.
- **4d. The hold window has lapsed but the sweep has not run.** Treated as 4b — `hold_expires_at` governs, not the sweep (FR-9.7). `confirm_pickup` compares against `hold_expires_at` and, on a lapsed scan, expires the reservation and releases the item immediately rather than waiting for the next sweep tick.
- **3a. The token is unrecognized.** 404, "That code doesn't match any reservation."

**Exception flows**

- **E1. The network fails mid-confirmation.** The transaction either commits fully or not at all. The staff member re-scans; step 4a's idempotent response tells them whether the first attempt landed.
- **E2. Two staff scan the same token concurrently.** Exactly one commits; the other receives the 4a response.

**Postconditions** — The reservation and item are both `picked_up`; the donation is recorded immutably; the food has changed hands.

> **Implementation note.** This flow is currently on the **wrong side of the counter.** The confirmation form lives on the organization's own page (`PantryPage.jsx:166-180`) and the endpoint is unauthenticated — so the party receiving the food confirms its own collection, and anyone holding a token can mark it collected remotely. FR-9.4 moves this to authenticated staff. It is the single most important control change in this document after authentication itself.

---

### UC-16 — Organization Cancels a Reservation

| | |
|---|---|
| **Primary actor** | Organization Coordinator |
| **Preconditions** | The coordinator is authenticated; their organization holds a `pending` reservation |
| **Trigger** | The organization can no longer collect |
| **Related** | FR-8.11, FR-2.4, FR-6.6 |

**Main success scenario**

1. The coordinator opens the reservation and selects Cancel.
2. The system confirms the reservation is `pending` and belongs to their organization.
3. The system sets the reservation to `cancelled` and records `cancelled_at`.
4. The system returns the item to `available`.
5. The item reappears in the donation network for other organizations.

**Alternate flows**

- **2a. The reservation is already `picked_up`.** Cancellation is refused; the food has been collected.
- **2b. The reservation is already `expired`.** The system reports that it lapsed and the item was released automatically.
- **2c. The reservation belongs to another organization.** 404 (FR-2.4).

**Postconditions** — The item is available to others; the reservation is terminal.

> **Implementation note.** `ReservationStatus.CANCELLED` is defined at `app/models.py:41` and no code path ever assigns it. This use case is entirely unbuilt. Its absence means an organization that knows it cannot collect has no way to release the food early — it sits reserved until the hold lapses, which is the exact waste the system exists to prevent.

---

### UC-17 — Hold Window Lapses and the Item Is Released

| | |
|---|---|
| **Primary actor** | Expiry Scheduler |
| **Stakeholders** | Store (does not want food stranded); other organizations (want a fair second chance) |
| **Preconditions** | At least one `pending` reservation is past its `hold_expires_at` |
| **Trigger** | The scheduler's interval elapses (target: ≤ 5 minutes, FR-10.2) |
| **Related** | FR-10.1, FR-10.2, FR-10.3, FR-10.4, FR-10.6, FR-10.7 |

**Main success scenario**

1. The scheduler fires.
2. The system selects all reservations with status `pending` and `hold_expires_at < now` (`app/crud.py:164-169`).
3. For each, the system sets the reservation to `expired`.
4. For each, the system sets the associated item back to `available` (`app/crud.py:171-174`).
5. The system commits all changes in one transaction (`app/crud.py:175`).
6. The system notifies each affected organization that its reservation lapsed uncollected (FR-10.6).
7. The system increments each organization's no-show counter (FR-10.7).
8. The released items reappear in the donation network.

**Alternate flows**

- **2a. Nothing is stale.** The sweep is a no-op.
- **4a. The item was discarded while reserved.** The reservation still expires, but the item stays `discarded` — a terminal state is not reversed (FR-6.8).
- **4b. Staff judge a released item no longer safe to re-offer.** They move it to `expired_hold` as a terminal state rather than letting it return to circulation (FR-6.5).

**Exception flows**

- **E1. Two sweeps run concurrently.** Both may select the same reservation and both attempt to release the same item. The sweep MUST be idempotent and locked (FR-10.3); the current implementation has no guard (`app/crud.py:163-175`).
- **E2. A pickup is confirmed while the sweep is mid-run.** The reservation must not be both `picked_up` and `expired`. The sweep MUST re-check status inside the transaction, and FR-9.7 ensures the pickup would be refused anyway once `hold_expires_at` has passed.
- **E3. The scheduler stops running.** Reservations remain `pending` past expiry and items stay stranded — invisible to other organizations and unusable. Scheduler health MUST be monitored, because this failure is silent.

**Postconditions** — Lapsed reservations are `expired`; their items are `available` or terminal.

> **Implementation note.** The logic is written and correct. Nothing invokes it. `POST /reservations/expire-stale` is an open, unauthenticated endpoint someone must remember to call by hand (`app/routers/reservations.py:26-33`). Until FR-10.2 provides a real scheduler, an unnoticed lapse strands food indefinitely — and E3 is not a hypothetical failure mode but the current default state.

---

### UC-18 — Staff Reviews Donation History

| | |
|---|---|
| **Primary actor** | Store Staff |
| **Preconditions** | The staff member is authenticated; completed donations exist |
| **Trigger** | Reporting need, or a compliance question |
| **Related** | FR-11.1, FR-11.2, FR-11.4, NFR-4.8.1 |

**Main success scenario**

1. The staff member opens donation history.
2. They set a date range and optionally an organization.
3. The system returns matching donation records: item, organization, confirming staff member, timestamp, and sell-by date at handoff.
4. The system summarizes total items and, where weight is known, total volume diverted.
5. The staff member exports the results.

**Alternate flows**

- **2a. An Organization Coordinator runs this.** They see their own organization's collections only (FR-11.3, FR-2.4).

**Postconditions** — No state change. Read-only, and the records are immutable (FR-11.1).

---

## 6. Data Model

### 6.1 Entity Relationships

```
   ┌──────────┐  1      ∞ ┌──────────┐  1      ∞ ┌──────────────┐ ∞      1 ┌──────────┐
   │  Shelf   │───────────│   Item   │───────────│ Reservation  │──────────│  Pantry  │
   └──────────┘  holds    └──────────┘  has      └──────────────┘  made by └──────────┘
                                                                                 │ 1
                                                                                 │
                                                                                 │ ∞
                                                                          ┌──────────────┐
                                                                          │     User     │  ← proposed
                                                                          └──────────────┘
                                                                       role: staff | manager
                                                                           | admin | org_coordinator
                                                                       pantry_id set only for
                                                                       org_coordinator
```

An item belongs to at most one shelf and may have many reservations over its life, though at most one may be `pending` at a time. A reservation links exactly one item to exactly one organization.

### 6.2 Shelf

*Source: `app/models.py:44-56`*

| Field | Type | Constraints | Status |
|---|---|---|---|
| `id` | UUID (string) | PK, generated | ✅ |
| `name` | String | Not null — e.g. "Shelf A — Dairy" | ✅ |
| `location` | String | Nullable — store or aisle description | ✅ |
| `camera_id` | String | Nullable | ✅ |
| `current_temperature_c` | Float | Nullable | ✅ |
| `current_humidity_pct` | Float | Nullable | ✅ |
| `last_reading_at` | DateTime | Nullable, UTC | ✅ |
| `created_at` | DateTime | Default now, UTC | ✅ |
| `store_id` | UUID | FK → Store | ○ *(needed for multi-store, NFR-4.6.1)* |
| `safe_temp_min_c` / `safe_temp_max_c` | Float | Nullable — excursion bounds | ○ *(FR-4.6)* |

### 6.3 ShelfReading — proposed

*Required by FR-4.5; no time series exists today.*

| Field | Type | Constraints | Status |
|---|---|---|---|
| `id` | UUID | PK | ○ |
| `shelf_id` | UUID | FK → Shelf, not null, indexed | ○ |
| `temperature_c` | Float | Nullable | ○ |
| `humidity_pct` | Float | Nullable | ○ |
| `recorded_at` | DateTime | Not null, UTC, indexed | ○ |

### 6.4 Item

*Source: `app/models.py:59-86`*

| Field | Type | Constraints | Status |
|---|---|---|---|
| `id` | UUID (string) | PK, generated | ✅ |
| `sku` | String | Nullable, indexed — null until OCR resolves it | ✅ |
| `name` | String | Not null | ✅ |
| `batch_id` | String | Nullable | ✅ |
| `category` | String | Nullable | ✅ |
| `sell_by_date` | DateTime | Nullable, UTC | ✅ |
| `arrival_date` | DateTime | Nullable, UTC | ✅ |
| `shelf_id` | UUID | FK → Shelf, nullable | ✅ *(existence unvalidated — FR-3.9)* |
| `status` | Enum(ItemStatus) | Not null, default `in_stock` | ✅ *(should be indexed — NFR-4.6.3)* |
| `ocr_raw_text` | Text | Nullable | ✅ |
| `ocr_confidence` | Float | Nullable, 0.0–1.0 | ✅ |
| `sku_match_confirmed` | Boolean | Default false | ✅ |
| `image_url` | String | Nullable — pointer to the stored frame, never raw footage | ✅ |
| `created_at` / `updated_at` | DateTime | Defaults; `updated_at` on update | ✅ |
| `use_by_date` | DateTime | Nullable — safety deadline, distinct from sell-by | ○ *(NFR-4.8.2)* |
| `weight_kg` | Float | Nullable — for diversion reporting | ○ *(FR-11.4)* |
| `reviewed_by_user_id` | UUID | FK → User, nullable | ○ *(UC-09 step 6)* |

### 6.5 Pantry (Organization)

*Source: `app/models.py:89-101`*

| Field | Type | Constraints | Status |
|---|---|---|---|
| `id` | UUID (string) | PK, generated | ✅ |
| `org_name` | String | Not null | ✅ |
| `ein` | String | Not null | ✅ *(format unvalidated, not unique — FR-7.6)* |
| `address` | String | Nullable | ✅ |
| `phone` | String | Nullable | ✅ |
| `contact_email` | String | Not null, unique | ✅ |
| `verified` | Boolean | Default false | ◐ *(**never set true by any code path** — FR-7.5)* |
| `created_at` | DateTime | Default now | ✅ |
| `verified_at` | DateTime | Nullable | ○ |
| `verified_by_user_id` | UUID | FK → User, nullable | ○ |
| `verification_notes` | Text | Nullable — rejection reason | ○ |
| `no_show_count` | Integer | Default 0 | ○ *(FR-10.7)* |

### 6.6 Reservation

*Source: `app/models.py`, class `Reservation`*

| Field | Type | Constraints | Status |
|---|---|---|---|
| `id` | UUID (string) | PK, generated | ✅ |
| `item_id` | UUID | FK → Item, not null | ✅ |
| `pantry_id` | UUID | FK → Pantry, not null | ✅ *(must derive from session, not request — FR-2.3)* |
| `status` | Enum(ReservationStatus) | Not null, default `pending` | ✅ |
| `reserved_at` | DateTime | Default now, UTC | ✅ |
| `hold_expires_at` | DateTime | Not null, UTC; **derived** as `scheduled_pickup_at + 30 min` | ✅ *(indexed)* |
| `qr_code` | String | Nullable, unique — 128-bit token | ✅ |
| `picked_up_at` | DateTime | Nullable | ✅ |
| **`scheduled_pickup_at`** | DateTime | Nullable, UTC, indexed; MUST satisfy `now ≤ value ≤ reserved_at + 24h` and `value ≤ item.discard_after` | ✅ *(nullable at the DB layer only so the column could be added to existing SQLite tables and so pre-scheduling terminal rows keep NULL — the API requires it)* |
| `cancelled_at` | DateTime | Nullable | ○ *(FR-8.11)* |
| `confirmed_by_user_id` | UUID | FK → User, nullable — staff who scanned | ○ *(FR-9.4)* |

### 6.7 User — proposed

*Required by FR-1 and FR-2. No equivalent exists.*

| Field | Type | Constraints |
|---|---|---|
| `id` | UUID | PK, generated |
| `email` | String | Not null, unique, validated |
| `password_hash` | String | Not null — bcrypt or Argon2id |
| `role` | Enum | Not null: `staff` \| `manager` \| `admin` \| `org_coordinator` |
| `pantry_id` | UUID | FK → Pantry, nullable — set **only** when role is `org_coordinator` (FR-2.2) |
| `is_active` | Boolean | Default true |
| `failed_login_count` | Integer | Default 0 |
| `locked_until` | DateTime | Nullable |
| `last_login_at` | DateTime | Nullable |
| `created_at` | DateTime | Default now |

### 6.8 DonationRecord — proposed

*Required by FR-11.1 and NFR-4.8.1. Append-only; never updated or deleted.*

| Field | Type | Constraints |
|---|---|---|
| `id` | UUID | PK |
| `reservation_id` | UUID | FK → Reservation, not null, unique |
| `item_name` / `item_sku` / `item_category` | String | Snapshot at handoff — denormalized deliberately, so the record survives changes to the item |
| `sell_by_date_at_handoff` | DateTime | Nullable |
| `pantry_id` / `pantry_name_at_handoff` | UUID / String | Not null |
| `confirmed_by_user_id` | UUID | FK → User, not null |
| `confirmed_at` | DateTime | Not null, UTC |

### 6.9 Enumerations

**`ItemStatus`** — `app/models.py:26-34`

| Value | Meaning | Assigned by |
|---|---|---|
| `in_stock` | Normal shelf item, before sell-by | Item creation (default) |
| `near_expiry` | Approaching sell-by | ⚠️ **Nothing** — see FR-6.4 |
| `needs_review` | OCR confidence below threshold, awaiting staff | `apply_ocr_result` |
| `available` | Published to the donation network | `apply_ocr_result`, staff review, expiry release |
| `reserved` | Held by an organization | `create_reservation` |
| `picked_up` | Donation completed (terminal) | `confirm_pickup` |
| `expired_hold` | Hold lapsed and item judged unusable (terminal) | ⚠️ **Nothing** — see FR-6.5 |
| `discarded` | Removed, not usable (terminal) | Staff action |

**`ReservationStatus`** — `app/models.py:37-41`

| Value | Meaning | Assigned by |
|---|---|---|
| `pending` | Held, awaiting pickup | `create_reservation` (default) |
| `picked_up` | Collected (terminal) | `confirm_pickup` |
| `expired` | Hold lapsed (terminal) | `expire_stale_reservations` |
| `cancelled` | Released by the organization (terminal) | ⚠️ **Nothing** — see FR-8.11 |

---

## 7. Item Lifecycle State Machine

```
                                   ┌─────────────┐
                     item created  │             │
                    ──────────────▶│  in_stock   │
                                   │             │
                                   └──────┬──────┘
                                          │ label captured, OCR posted
                                          ▼
                            ┌─────────────────────────┐
                            │  confidence ≥ 0.95      │
                            │  AND sku_match_confirmed│
                            └────────┬───────┬────────┘
                                 yes │       │ no
                                     │       ▼
                                     │  ┌──────────────┐
                                     │  │ needs_review │◀── staff re-queues
                                     │  └──┬────────┬──┘
                                     │     │ staff  │ staff discards
                                     │     │ approves        │
                                     ▼     ▼                 │
                                 ┌───────────────┐           │
              expiry releases ──▶│   available   │           │
                             ┌───┤               │           │
                             │   └───────┬───────┘           │
                             │           │ org reserves      │
                             │           ▼    (atomic claim) │
                             │   ┌───────────────┐           │
                             │   │   reserved    │           │
                             │   └───┬───────┬───┘           │
                             │       │       │ staff scans QR│
                             │ hold  │       ▼               │
                             │ lapses│  ┌───────────┐        │
                             │  or   │  │ picked_up │        │
                             └───────┘  │ TERMINAL  │        │
                                     │  └───────────┘        │
                                     │                       │
                       staff judge   │                       ▼
                       unusable      │              ┌───────────────┐
                                     └─────────────▶│  discarded    │
                                                    │  TERMINAL     │
                              ┌──────────────┐      └───────────────┘
                              │ expired_hold │
                              │  TERMINAL    │◀── staff, after a lapsed hold,
                              └──────────────┘    judge the item unusable

     near_expiry — defined but unassigned; recommended for withdrawal (FR-6.4)
```

### Transition Table

| # | From | To | Trigger | Actor | Implementation |
|---|---|---|---|---|---|
| T1 | *(none)* | `in_stock` | Item created | Staff | ✅ `app/models.py:74` |
| T2 | `in_stock` | `available` | OCR confidence ≥ 0.95 **and** SKU confirmed | OCR Pipeline | ✅ `app/crud.py:109-110` |
| T3 | `in_stock` | `needs_review` | OCR confidence < 0.95 **or** SKU unconfirmed | OCR Pipeline | ✅ `app/crud.py:111-112` |
| T4 | `needs_review` | `available` | Staff confirm the date and approve | Staff | ◐ via generic status PATCH |
| T5 | `needs_review` | `in_stock` | Staff judge the item still saleable | Staff | ◐ via generic status PATCH |
| T6 | `needs_review` | `discarded` | Staff judge the item unsafe | Staff | ◐ via generic status PATCH |
| T7 | `available` | `reserved` | Organization reserves | Org Coordinator | ✅ `app/crud.py:151` |
| T8 | `reserved` | `picked_up` | Valid QR scanned within the hold window | Staff | ✅ `app/crud.py:187` |
| T9 | `reserved` | `available` | Hold window lapses | Expiry Scheduler | ✅ `app/crud.py:173-174` |
| T10 | `reserved` | `available` | Organization cancels | Org Coordinator | ○ FR-8.11 |
| T11 | `available` | `expired_hold` | Staff judge a released item unusable | Staff | ○ FR-6.5 |
| T12 | *any non-terminal* | `discarded` | Staff judge the item unsafe | Staff | ◐ FR-6.7 |
| T13 | `available` | `in_stock` | Staff pull the item back for sale | Staff | ○ |

**Terminal states:** `picked_up`, `discarded`, `expired_hold`. No transition leaves them (FR-6.8).

**Not permitted:** any transition into `reserved` from other than `available` (FR-6.6, ✅ enforced at `app/crud.py:139-141`); any transition out of a terminal state; any transition not listed above (FR-6.2, ○ — the current `update_item_status` at `app/crud.py:83-90` accepts every combination without checking).

---

## 8. API Specification

Base URL: `http://localhost:8000` in development. All request and response bodies are JSON. Timestamps are ISO-8601 UTC.

**Legend:** ✅ implemented · ◐ implemented but requires change · ○ proposed

### 8.1 System

| Method | Path | Auth | Roles | Response | Status |
|---|---|---|---|---|---|
| `GET` | `/health` | None | — | `{"status": "ok"}` | ✅ `app/main.py:32-34` |

### 8.2 Authentication — all proposed

| Method | Path | Auth | Request | Response | Errors |
|---|---|---|---|---|---|
| `POST` | `/auth/login` | None | `{email, password}` | `{access_token, token_type, role, pantry_id?}` | 401 generic, 429 rate-limited |
| `POST` | `/auth/logout` | Bearer | — | 204 | 401 |
| `POST` | `/auth/refresh` | Bearer | — | `{access_token}` | 401 |
| `GET` | `/auth/me` | Bearer | — | `{id, email, role, pantry_id?}` | 401 |
| `POST` | `/auth/password-reset` | None | `{email}` | 202 always | 429 |
| `POST` | `/auth/password-reset/confirm` | None | `{token, new_password}` | 204 | 400 invalid/expired |
| `POST` | `/auth/users` | Bearer | `{email, role, password}` | User | 401, 403 non-manager, 409 duplicate |
| `PATCH` | `/auth/users/{id}/active` | Bearer | `{is_active}` | User | 401, 403, 404 |

### 8.3 Shelves

| Method | Path | Auth | Roles | Request | Response | Status |
|---|---|---|---|---|---|---|
| `POST` | `/shelves` | Bearer ○ | staff, manager | `ShelfCreate` | `ShelfOut` | ◐ *(auth needed)* |
| `GET` | `/shelves` | Bearer ○ | staff, manager, admin | — | `[ShelfOut]` | ◐ |
| `PATCH` | `/shelves/{id}/reading` | Service ○ | sensor bridge | `ShelfReadingUpdate` | `ShelfOut` · 404 | ◐ `app/routers/shelves.py:21-28` |
| `GET` | `/shelves/{id}/readings` | Bearer | staff, manager | `?from=&to=` | `[ShelfReading]` | ○ *(FR-4.5)* |

### 8.4 Items

| Method | Path | Auth | Roles | Request | Response | Status |
|---|---|---|---|---|---|---|
| `POST` | `/items` | Bearer ○ | staff, manager | `ItemCreate` | `ItemOut` · 422 unknown shelf | ◐ *(FR-3.9)* |
| `GET` | `/items` | Bearer ○ | staff, manager, admin; org_coordinator limited to `status=available` | `?status=&shelf_id=&limit=&offset=` | `[ItemOut]` | ◐ *(pagination — NFR-4.3.5)* |
| `GET` | `/items/near-expiry` | Bearer ○ | staff, manager | `?within_hours=48` | `[ItemOut]` | ◐ `app/routers/items.py:21-24` |
| `GET` | `/items/{id}` | Bearer ○ | staff, manager, admin | — | `ItemOut` · 404 | ◐ |
| `PATCH` | `/items/{id}/status` | Bearer ○ | staff, manager | `ItemStatusUpdate` | `ItemOut` · 404 · 409 invalid transition | ◐ *(FR-6.2)* |
| `POST` | `/items/{id}/ocr-result` | Service ○ | OCR pipeline | `ItemOCRResult` | `ItemOut` · 404 | ◐ `app/routers/items.py:43-53` |
| `POST` | `/items/{id}/review` | Bearer | staff, manager | `{sell_by_date, sku, decision}` | `ItemOut` | ○ *(FR-3.3)* |

**Note on route ordering:** `/items/near-expiry` is declared before `/items/{item_id}` in `app/routers/items.py`. This ordering is required — FastAPI matches in declaration order, and reversing them would cause `near-expiry` to be captured as an item ID. Preserve it.

### 8.5 Organizations

| Method | Path | Auth | Roles | Request | Response | Status |
|---|---|---|---|---|---|---|
| `POST` | `/pantries` | None | — (self-registration) | `PantryCreate` | `PantryOut` · 409 duplicate | ◐ *(FR-7.6)* |
| `GET` | `/pantries` | Bearer ○ | staff, manager, admin | `?verified_only=` | `[PantryOut]` | ◐ *(FR-7.9 — currently leaks EIN, phone, email to anyone)* |
| `GET` | `/pantries/{id}` | Bearer | admin; own org | — | `PantryOut` · 404 | ○ |
| `PATCH` | `/pantries/{id}/verification` | Bearer | admin | `{verified, notes}` | `PantryOut` | ○ **← FR-7.5, the missing gate** |

### 8.6 Reservations

| Method | Path | Auth | Roles | Request | Response | Status |
|---|---|---|---|---|---|---|
| `POST` | `/reservations` | Bearer | org_coordinator | `{item_id, scheduled_pickup_at}` — offset-aware, no `hold_minutes` | `ReservationDetailOut` · 409 unavailable · 422 bad pickup time · 403 unverified | ✅ *(FR-2.3, FR-7.4, FR-8.6, NFR-4.7.1)* |
| `GET` | `/reservations` | Bearer | staff, manager, admin | `?status=` | `[ReservationDetailOut]` | ✅ *(store-wide; backs the Pickup schedule card)* |
| `GET` | `/reservations/mine` | Bearer | org_coordinator | `?status=` | `[ReservationDetailOut]` | ✅ *(FR-8.10; scoped to the caller's org)* |
| `GET` | `/reservations/{id}` | Bearer | owner org; staff, manager | — | `ReservationOut` · 404 | ○ |
| `PATCH` | `/reservations/{id}` | Bearer | owner org | `{scheduled_pickup_at}` | `ReservationOut` · 422 | ○ *(FR-8.7)* |
| `DELETE` | `/reservations/{id}` | Bearer | owner org; manager | — | `ReservationOut` (cancelled) · 409 | ✅ *(FR-8.11 — shipped as `POST /reservations/{id}/cancel`, 404 not 403 per FR-2.4)* |
| `POST` | `/reservations/pickup` | Bearer | **staff, manager** | `{token}` in body | `ReservationDetailOut` · 404 · 409 with reason | ◐ **← staff-only and reason-differentiated now; still `POST /reservations/pickup/{qr_code}` with the token in the URL path (FR-9.9)** |
| `POST` | `/reservations/expire-stale` | Bearer | manager, admin | — | `[ReservationOut]` | ✅ *(FR-10.5; the scheduler also runs it every 60s)* |

### 8.7 Reporting — all proposed

| Method | Path | Auth | Roles | Request | Response |
|---|---|---|---|---|---|
| `GET` | `/reports/donations` | Bearer | staff, manager, admin; org_coordinator own only | `?from=&to=&pantry_id=` | `[DonationRecord]` |
| `GET` | `/reports/diversion` | Bearer | staff, manager, admin | `?from=&to=` | `{item_count, weight_kg}` |
| `GET` | `/reports/ocr-accuracy` | Bearer | manager | `?from=&to=` | `{auto_published, sent_to_review, corrected}` |

### 8.8 Error Conventions

| Code | Meaning | Example |
|---|---|---|
| 400 | Malformed request | Expired reset token |
| 401 | Not authenticated | Missing or expired bearer token |
| 403 | Authenticated but not permitted | Unverified organization attempting to reserve |
| 404 | Not found, **or** not visible to this actor | Another organization's reservation (FR-2.4) |
| 409 | State conflict | Item already reserved; QR already redeemed |
| 422 | Validation failure | `scheduled_pickup_at` outside the hold window |
| 429 | Rate limited | Repeated login failures |
| 500 | Server error | Unhandled exception; MUST NOT leak internals |

Errors return `{"detail": "<message>"}`, matching FastAPI's convention and the frontend's existing handling at `frontend/src/api.js:9-11`.

---

## 9. Traceability Matrix

| FR | Use cases | Endpoints | Source |
|---|---|---|---|
| FR-1 Authentication | UC-01, UC-02, UC-05 | `/auth/*` | ○ none |
| FR-2 Authorization | UC-02, UC-04, UC-13, UC-14, UC-16 | all | ○ none |
| FR-3 Staff dashboard | UC-07, UC-09, UC-10, UC-11, UC-15 | `/items/*`, `/shelves` | `frontend/src/pages/StorePage.jsx` |
| FR-4 Shelf monitoring | UC-06, UC-07 | `/shelves`, `/shelves/{id}/reading` | `app/routers/shelves.py`, `app/crud.py:31-46` |
| FR-5 OCR pipeline | UC-08, UC-09 | `/items/{id}/ocr-result` | `app/ocr.py`, `app/crud.py:93-116` |
| FR-6 Item lifecycle | UC-07 – UC-17 | `/items/{id}/status` | `app/models.py:26-34`, `app/crud.py:83-90` |
| FR-7 Org verification | UC-03, UC-04, UC-12, UC-13 | `/pantries*` | `app/routers/pantries.py`, `app/crud.py:121-133` |
| FR-8 Reservation & scheduling | UC-12, UC-13, UC-14, UC-16 | `/reservations*` | `app/crud.py:138-154` |
| FR-9 QR & pickup | UC-14, UC-15 | `/reservations/pickup` | `app/crud.py:148, 179-190` |
| FR-10 Expiry | UC-17 | `/reservations/expire-stale` | `app/crud.py:157-176` |
| FR-11 Reporting | UC-15, UC-18 | `/reports/*` | ○ none |

**Coverage checks.** Every FR group maps to at least one use case, and every use case cites at least one FR. Every implemented endpoint maps to an FR group — no orphaned code. Four FR groups (1, 2, 11, and most of 3) have no implementation, which is the expected shape given that authentication was never built.

---

## 10. Out of Scope and Future Work

| Item | Rationale |
|---|---|
| **Multi-store tenancy** | The schema assumes one store (NFR-4.6.1). Supporting a chain means a Store entity and scoping every query. Best done before production data exists. |
| **Delivery and driver routing** | Organizations collect in person. Volunteer-driver dispatch is a separate product. |
| **Native mobile applications** | The responsive web client covers the QR-presentation and scanning flows. |
| **Point-of-sale integration** | Automatic SKU catalog synchronization and sale-triggered item removal require store IT engagement beyond this revision. |
| **Barcode / UPC detection** | Replaces the fragile substring SKU matching (C-4, FR-5.9). Vision supports it; the work is catalog integration. |
| **Cold-chain alerting** | Temperature is collected today and never read. FR-4.5 through FR-4.8 specify the use; implementation is deferred. |
| **Automated EIN verification** | IRS record lookup is manual in this revision (A-4). |
| **Recurring reservations** | Standing weekly pickups for regular partners. |
| **Alembic migrations** | Required before any deployment holding real data (C-5, NFR-4.6.4). |
| **Automated test suite** | No tests exist. `app/crud.py` was deliberately written to be directly unit-testable — the confidence branch, the hold window, and the expiry sweep should be the first tests written. |
| **Weight capture** | Needed for the diversion metric in FR-11.4. |

---

## Appendix A — Gap Analysis

Ordered by severity. This doubles as a build backlog.

| # | Gap | Impact | Closed by | Priority |
|---|---|---|---|---|
| **1** | **No authentication anywhere.** No users, no passwords, no sessions. Every endpoint is open, and `allow_origins=["*"]` (`app/main.py:19-24`) permits any origin. | Anyone reaching the server can alter inventory, reserve food as any organization, and confirm pickups. | FR-1, FR-2, NFR-4.1.3 | **Critical** |
| **2** | **Pickup confirmation is unauthenticated and lives on the recipient's page** (`PantryPage.jsx:166-180`). | The party receiving the food confirms its own collection; anyone with a token can mark an item collected without ever appearing at the store. | FR-9.4, FR-9.10 | **Critical** |
| **3** | **Organization verification is never applied.** `verified` is never set true, and `create_reservation` never checks it (`app/crud.py:138-154`). | Unverified or fraudulent organizations can reserve food. Good Samaritan Act protection depends on the recipient being a verified nonprofit. | FR-7.4, FR-7.5 | **Critical** |
| **4** | **Reservation race condition.** Check-then-act with no lock (`app/crud.py:139-153`). | Two organizations can hold valid QR codes for one physical item, producing a dispute at the shelf. | FR-8.9, NFR-4.7.1 | **High** |
| **5** | **Expiry never runs automatically.** `expire-stale` is a manual, open endpoint (`app/routers/reservations.py:26-33`). | An uncollected reservation strands food indefinitely — the exact waste the system exists to prevent. | FR-10.2, FR-10.5 | **High** |
| ~~**6**~~ | ~~**Pickup does not check the hold expiry.**~~ **Closed.** `confirm_pickup` now compares against `hold_expires_at` and releases the item on a lapsed scan. | — | FR-9.7 | ✅ |
| ~~**7**~~ | ~~**No cancellation path.**~~ **Closed.** `POST /reservations/{id}/cancel`. | — | FR-8.11 | ✅ |
| ~~**8**~~ | ~~**No scheduled pickup time.**~~ **Closed.** `Reservation.scheduled_pickup_at`, booked up to 24h ahead, drives the hold. Staff see a Pickup schedule card. | — | FR-8.6 | ✅ |
| **9** | **No review queue interface.** Items enter `needs_review` with no path out but a raw status PATCH. | The human safeguard the confidence branch depends on has no interface, so it does not function. | FR-3.2, FR-3.3 | **High** |
| ~~**10**~~ | ~~**QR code is rendered as raw text.**~~ **Closed.** Rendered with `QRCodeSVG` and read by the staff camera scanner (`BarcodeScanner` `mode="qr"`). | — | FR-9.3, FR-3.10 | ✅ |
| **11** | **Two item states have no writer.** `near_expiry` and `expired_hold` are defined and never assigned. | Ambiguous model; readers cannot tell whether the states are meaningful. | FR-6.4, FR-6.5 | **Medium** |
| **12** | **State transitions are unvalidated.** Any status may be set from any other (`app/crud.py:83-90`). | A `picked_up` item can be returned to `available`. | FR-6.2 | **Medium** |
| **13** | **Organization contact details are public.** `GET /pantries` returns EIN, phone, and email to any caller. | Discloses nonprofit contact data. | FR-7.9 | **Medium** |
| **14** | **Vision API failure raises rather than degrading** (`app/ocr.py:72-73`). | An outage blocks stocking instead of routing to review. | FR-5.7 | **Medium** |
| **15** | **No migrations; thin test coverage.** `scripts/upgrade_schema.py` is still a hand-rolled bridge, not Alembic. `backend/tests/` now covers reservation scheduling and pickup expiry; everything else is untested, and there is no frontend suite. | Schema changes risk data; regressions outside reservations go undetected. | C-5, NFR-4.6.4 | **Medium** |
| **16** | **"Simulate OCR scan" ships in the client** with a hardcoded 0.97 confidence (`StorePage.jsx:144-151`). | A production user can fabricate a passing OCR result and auto-publish an unverified item. | FR-3.11 | **Medium** |

### Suggested Build Order

**Phase 1 — Close the security gaps.** User model, login, role enforcement (FR-1, FR-2); move pickup confirmation to authenticated staff (FR-9.4); enforce the verification gate (FR-7.4, FR-7.5); fix the CORS allowlist. Nothing else should ship first — gaps 1, 2, and 3 make every other feature untrustworthy.

**Phase 2 — Make the core flow correct.** Atomic reservation claim (FR-8.9); scheduled pickup time (FR-8.6); hold-expiry check at pickup (FR-9.7); cancellation (FR-8.11); a real scheduler (FR-10.2).

**Phase 3 — Complete the interfaces.** Review queue (FR-3.2, FR-3.3); near-expiry view (FR-3.4); QR rendering and scanning (FR-9.3, FR-9.10); reservation listing (FR-8.10).

**Phase 4 — Harden and measure.** Transition validation (FR-6.2); resolve the two orphan states (FR-6.4, FR-6.5); donation records (FR-11.1); Alembic; tests for the confidence branch, the hold window, and the expiry sweep.

---

## Appendix B — Requirement Status Summary

| Group | Total | ✅ Implemented | ◐ Partial | ○ Proposed |
|---|---:|---:|---:|---:|
| FR-1 Authentication | 11 | 0 | 0 | 11 |
| FR-2 Authorization | 6 | 0 | 0 | 6 |
| FR-3 Staff dashboard | 11 | 1 | 4 | 6 |
| FR-4 Shelf monitoring | 8 | 4 | 0 | 4 |
| FR-5 OCR pipeline | 10 | 4 | 2 | 4 |
| FR-6 Item lifecycle | 8 | 2 | 2 | 4 |
| FR-7 Org verification | 9 | 2 | 1 | 6 |
| FR-8 Reservation | 12 | 3 | 3 | 6 |
| FR-9 QR & pickup | 10 | 3 | 2 | 5 |
| FR-10 Expiry | 7 | 2 | 0 | 5 |
| FR-11 Reporting | 6 | 0 | 0 | 6 |
| **Total** | **98** | **21** | **14** | **63** |

Roughly a fifth of the specified functionality is implemented, and it is concentrated in the parts hardest to get right — the OCR confidence branch, the hold-window arithmetic, and the expiry sweep are all correct. What is missing is the layer around them: who is allowed to do any of it.

---

*End of document.*
