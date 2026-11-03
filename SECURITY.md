# Security policy (hackathon MVP)

This repository is public. Treat it as hostile to secrets.

## Rules

1. Never commit `.env`, API keys, tokens, passwords, private certificates,
   or connection strings containing credentials.
2. Keep all provider credentials in the untracked root `.env` or an approved
   secrets manager. The backend loads them from process environment only.
3. Never put secrets in frontend code, `VITE_` variables, browser storage,
   logs, screenshots, fixtures, or demo recordings.
4. Never print full tokens or authorization headers in logs or errors.
5. Never commit uploaded videos, customer data, or local databases.

## Before every public push

```bash
git status --short
git check-ignore -v .env
git diff --cached
git ls-files .env   # must print nothing
```

Search staged changes for `API_KEY`, `BEGIN`, `token`, `secret`.

## If a secret leaks

Revoke/rotate it immediately, remove it from the tree and history, and review
provider activity. Deleting it in a later commit does not make it safe.

## Reporting

Do not file a public issue containing a secret. Contact the repository owner
directly with the file and commit reference (no secret values).
