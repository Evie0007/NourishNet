# NourishNet

Smart-shelf food recovery: tracks grocery items toward their sell-by date and
hands them to verified food pantries before they're thrown out.

See [`docs/functional-spec.md`](docs/functional-spec.md) for the full
requirements and use cases.

## Layout

```
app/            FastAPI backend
  auth.py       password hashing, JWT, role guards
  crud.py       business logic (confidence branch, hold window, reservations)
  models.py     SQLAlchemy schema
  routers/      HTTP endpoints
frontend/       React 19 + Vite + Tailwind
  src/pages/    Login, StaffDashboard, OrganizerDashboard
scripts/        seed_demo.py
```

## The three screens

| Route | Who | What |
|---|---|---|
| `/` | Anyone | Welcome + sign in (one form, both roles). Also holds the pantry self-registration form. |
| `/staff` | `staff`, `manager`, `admin` | Status counts, OCR review queue, near-expiry list, shelf conditions, inventory, QR pickup confirmation |
| `/organizer` | `org_coordinator` | Available donations, reserve, QR code for pickup, hold-window countdown, cancel |

Sign-in routes each user to their own dashboard based on the role the server
returns — never on which URL was typed.

## Running it locally

**Backend**

```bash
python -m venv venv
venv\Scripts\activate          # PowerShell:  .\venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env           # then fill in DATABASE_URL and JWT_SECRET
python -m uvicorn app.main:app --reload
```

API docs at http://localhost:8000/docs.

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
pantry and sample shelves and items so both dashboards have content. Re-running
it updates the accounts rather than duplicating them.

## Deploying

The frontend deploys to Vercel with root directory `frontend/`. The API needs a
separate host that can run a long-lived process (Render, Railway, Fly). Two
environment variables matter on the API host:

- `JWT_SECRET` — a fresh value, not the one from local dev
- `CORS_ORIGINS` — your Vercel URL, comma-separated if there's more than one

Then set `VITE_API_URL` in Vercel to the API's URL.

## Known gaps

Tracked in the spec; the ones that matter most before real users:

- **No migrations.** `Base.metadata.create_all` at `app/main.py` means schema
  changes drop data. Adopt Alembic before there's anything worth keeping (C-5).
- **No admin verification UI.** `Pantry.verified` is enforced at reservation
  time but nothing flips it to true except the seed script (FR-7.5, UC-04).
- **Expiry sweep is manual.** `POST /reservations/expire-stale` needs a
  scheduler running at most 5 minutes apart (FR-10.2).
- **No server-side logout.** The client drops its token; the token stays valid
  until it expires (FR-1.6).
- **No email.** Reservation confirmations, expiry warnings, and password reset
  are all unbuilt (FR-1.7, FR-7.7, FR-8.12, FR-10.6).
- **No tests** beyond manual verification.
