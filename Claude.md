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

<!-- Fill these in: what the project does, how to run it, how to test it,
     folder layout, conventions. Anything you'd tell a new teammate on day one
     belongs here. Every contributor's Claude reads this file automatically. -->

### Running locally

### Tests

### Folder layout

### Conventions