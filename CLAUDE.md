# NourishNet

## Git workflow — read this before any file changes

Most contributors on this project are new to git. Handle git operations for
them and explain what you did in plain language. Never assume they know what
a branch, a rebase, or a remote is.

### Before changing any file

Check the current branch. If it is `main`, do not edit anything yet. Instead:

1. Run `git checkout main && git pull` to get the latest code.
2. Create a branch: `git checkout -b <name>/<short-task-description>`
   (e.g. `sam/recipe-search`, `alex/fix-login-button`).
3. Tell the user which branch they are now on, then start work.

If they are already on a non-`main` branch, confirm it is the right one for
this task before continuing. Starting a second unrelated task on an existing
branch makes the pull request hard to review.

### Finishing a piece of work

When the user says they are done, or asks to save / upload / submit / push:

1. Run `git status` and show them what changed.
2. Stage and commit with a clear message describing what changed and why.
   Do not use "wip", "update", or "changes" as a message.
3. Push with `git push -u origin <branch>`.
4. Open a pull request with `gh pr create --fill`, then give them the URL.
5. Tell them it now needs review before it goes into `main`.

### Hard rules

- **Never** commit or push directly to `main`. The branch is protected and the
  push will be rejected. Create a branch instead.
- **Never** run `git push --force` or `git push -f` on any branch.
- **Never** run `git reset --hard`, `git clean -fd`, or delete a branch without
  explaining what will be lost and getting an explicit yes.
- **Never** commit `.env`, API keys, credentials, or anything in `.gitignore`.
  If you notice a secret in a file being staged, stop and flag it.
- Ask before adding a dependency or editing `package.json`.
- Keep pull requests small. If a change is growing past a few hundred lines,
  suggest splitting it.

### Before writing a new function

Search the codebase for existing code that does the same thing. Four people
work on this repo in parallel and duplicate implementations are a recurring
problem. If something similar exists, extend it rather than writing a second
version, and say so.

### When git fails

Translate the error instead of pasting it back. The common ones:

- **"Updates were rejected"** — someone else pushed first. Run
  `git pull --rebase`, resolve conflicts, push again.
- **"Protected branch"** — they are on `main`. Move the work to a new branch
  with `git checkout -b <name>/<task>` and push that.
- **"index.lock" / "File exists"** — OneDrive is syncing the repo. Tell them to
  pause OneDrive from the taskbar, retry, then resume.
- **Merge conflicts** — walk them through it one file at a time. Show both
  versions and ask which is correct. Do not pick silently.

Nothing here is unrecoverable except a force push. If they seem worried about
breaking something, say so.

---

## Project notes

NourishNet tracks grocery items toward their sell-by date and hands them to
verified food pantries before they are thrown out. Two halves, deployed
separately: a FastAPI backend (`backend/`, Render) and a React + Vite frontend
(`frontend/`, Vercel).

`README.md` is the setup document and stays the source of truth — don't copy
its instructions here, they will drift. What follows is the orientation and the
things that are not obvious from reading the code.

### Running locally

Full instructions live in the README under *Running it locally*. The shape of
it: two terminals, `python -m uvicorn app.main:app --reload` from `backend/`
and `npm run dev` from `frontend/`. Backend commands run from `backend/`,
frontend commands from `frontend/` — nothing runs from the repo root except
git. API docs at http://localhost:8000/docs.

Three things that account for most of the lost time:

- **`VITE_API_URL`** in `frontend/.env` has to point at wherever the API is.
  Unset, the app calls `http://localhost:8000`.
- **`python -m scripts.upgrade_schema`** after pulling a schema change.
  Startup creates missing *tables* but never adds a column to an existing one,
  so the API boots and then 500s on every item query. The script is idempotent.
- **Camera scanning needs a secure context** — `localhost` or HTTPS. A LAN
  address is refused by the browser. Use `npm run dev:mobile` for phone testing.

### Tests

There is no test suite yet. `pytest` and `httpx2` are pinned in
`backend/requirements-dev.txt` and ready to use, and `fastapi.testclient`
drives the app in-process, so a backend suite needs no running server:

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

The only automated check today is `npm run lint` (oxlint) in `frontend/`.
Run it before pushing frontend work. Everything else is manual verification —
`python -m scripts.demo_expiration` exercises the expiration rules against a
throwaway database, which is the closest thing to an integration test here.

If you add a feature, adding the first tests around it is worth more than the
feature. Start with `backend/tests/`.

### Folder layout

The README has the annotated tree. The short version:

```
backend/app/       routers/ = HTTP endpoints, crud.py = business logic,
                   models.py = SQLAlchemy, schemas.py = Pydantic,
                   expiration.py + scheduler.py = the automatic rules
backend/scripts/   seed_demo, upgrade_schema, sweep, demo_expiration
frontend/src/      pages/ = one per route, components/ = shared UI,
                   api.js = every call to the backend
docs/              functional-spec.md, intake-pipeline.md, deploy.md
```

The functional spec is numbered (FR-, NFR-, UC-, C-) and the code cites those
IDs in comments. When you change behaviour the spec describes, check whether
the spec needs updating too.

### Conventions

**Backend**

- Routers stay thin: validate, authorize, delegate. Business logic belongs in
  `crud.py` or `expiration.py`, not in an endpoint.
- Authorization is a dependency — `Depends(auth.require_staff)`,
  `Depends(auth.get_current_user)` — never an `if` inside the handler.
- Docstrings explain *why*, not what. The existing ones are the house style:
  they record the decision and the reason, so the next person doesn't undo it.
- Dependencies are pinned exactly, in both requirements files. Bump
  deliberately, run the app, commit the new pin.

**Frontend**

- Every backend call goes through the `api` object in `src/api.js`. No raw
  `fetch` in a component — 401 handling and auth headers live in that one file.
- Dates from the API are naive UTC. Parse them with `parseUtc()` from
  `src/api.js`, never `new Date()` directly, or every countdown is wrong by the
  viewer's UTC offset.
- Plain JSX and Tailwind utility classes. No TypeScript, despite the `@types`
  packages Vite pulls in.

**Both**

- Secrets go in `.env`, which is gitignored. `backend/.env.example` is the
  template and is committed — never put a real value in it.
- A failure at the loading dock must always have a way through. An unknown
  barcode, an unreadable label, Vision being down: each degrades to typing it
  by hand. Nothing in the intake path should be able to block receiving.
- The confirmation step is not optional. Scanners remove the typing; a person
  keeps the judgment. Don't add a path that writes an `Item` without one.