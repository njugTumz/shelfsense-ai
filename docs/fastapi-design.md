# FastAPI Technical Design — Full-Stack Generation (Deferred, Post-Hackathon)

Status: design only. NOT implemented in this milestone. No dual-process
E2B execution exists. The current generator produces frontend-only apps;
`services/architecture.py` classifies specs as FRONTEND_ONLY /
LOCAL_PERSISTENCE / NEEDS_BACKEND / NEEDS_INTEGRATION so the UI can
honestly state prototype limitations.

## Goal

When a spec classifies NEEDS_BACKEND or NEEDS_INTEGRATION, FIELD should
generate a real full-stack app: the existing React/Vite/Tailwind
frontend plus a FastAPI backend, both verified in E2B and previewed
through FIELD.

## Backend architecture (proposed)

```text
server/
  app/
    main.py            # FastAPI app factory, router includes, CORS, errors
    core/
      config.py        # env-driven settings, no secrets in code
      security.py      # authN/Z helpers (API key or session, per spec)
    api/
      v1/
        orders.py      # routers: thin HTTP layer, Pydantic in/out
        ...
    schemas/
      orders.py        # Pydantic request/response models (the API contract)
    services/
      orders.py        # use cases: orchestrate domain + persistence
    domain/
      orders.py        # pure business rules: canTransition, invariants
    infra/
      db.py            # engine/session, SQLite default, Postgres optional
      repositories.py  # persistence interfaces + implementations
  tests/
    test_orders_api.py # API + domain tests, run in E2B
  requirements.txt     # pinned: fastapi, uvicorn, pydantic, sqlalchemy
  Dockerfile.dev       # NOT used in E2B; documents local parity
```

Rules carried over from the frontend contract:

- Domain rules are pure functions with zero infrastructure imports.
- Routers never touch the DB; services never build HTTP responses.
- Pydantic schemas are the single API contract shared by frontend
  types (generated `api.ts`) and backend validation.
- Auth is deny-by-default when the spec has roles; single-user specs
  get no fake login screen.

## API contract

- Versioned under `/api/v1/`.
- Errors: `{"detail": ..., "code": ...}` with correct HTTP status;
  validation errors return 422 with field paths.
- Frontend calls the backend through a dev/proxy prefix (`/api`) so
  the same bundle works in E2B preview and production.

## E2B execution implications (the hard part)

Today one sandbox runs one process (`vite preview`) plus the browser.
Full-stack needs two supervised processes plus the browser inside the
same ~473 MB sandbox (see `docs/e2b-resource-findings.md`):

| Concern | Proposed handling |
|---|---|
| Process startup | Backend first (`uvicorn --port 8000`), wait for `/healthz 200`, then frontend server |
| Supervision | Small supervisor loop in the driver: restart-once on crash, else FAIL with logs |
| Health checks | Backend `/healthz`; frontend HTTP 200; browser workflow unchanged |
| Port allocation | Fixed: 8000 backend, 5173 frontend, no dynamic ports |
| API proxying | `vite preview` cannot proxy; serve frontend via a tiny static server + reverse-proxy, or bake the backend base URL into the E2B build |
| Backend availability | If `/healthz` never passes, verdict FAILED (runtime), never AUDIT_LIMITED |
| Cleanup | Kill both processes before sandbox teardown; sandbox is still always killed |
| Persistence | SQLite file inside the sandbox (ephemeral, fine for verification); Postgres out of scope for E2B |
| Resources | ~473 MB must now fit uvicorn (~60 MB) + frontend server + Firefox (~230 MB): requires the minimal-static-server swap AND lean-prefs Firefox; may still OOM — needs measurement before committing |

## Preview lifecycle

- On-demand preview (`preview.py` pattern) starts backend + frontend,
  waits for both health checks, returns the frontend URL.
- Same TTL/expiry semantics as today; backend has no separate public URL.
- Preview failure (either process) surfaces as a preview error with
  per-process logs, never a half-working URL.

## Deployment implications

- Production FIELD stays as-is (it only orchestrates E2B).
- Generated full-stack apps are demo artifacts, not deployed by FIELD;
  deployment guidance (container per process, managed Postgres,
  secret management) ships as generated docs, not automation.

## Risks

1. **Memory**: two servers + Firefox may exceed the sandbox ceiling;
   the design is unverified until measured.
2. **Startup sequencing flakes**: readiness races need real health
   polling, not sleeps.
3. **Scope creep**: auth, migrations, and Postgres are each a
   milestone-sized risk; the first cut is SQLite + single-user or
   API-key auth only.
4. **Verification complexity**: browser tests must distinguish
   frontend bugs from API failures; API-level checks (status codes,
   schema conformance) come first.

## Recommended implementation stages

1. Backend-only generation + API checks in E2B (no browser).
2. Frontend API-client generation against the Pydantic contract.
3. Dual-process sandbox orchestration + health polling.
4. Browser workflow over the full stack.
5. Preview lifecycle for full-stack missions.
6. Auth + Postgres hardening (only if a demo needs it).
