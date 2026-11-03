# FIELD — See the world. Understand the problem. Build the solution.

FIELD is an AI software team that turns observations of a real-world
environment into useful software. A business owner uploads a short video of an
operation; FIELD analyses the visual and audio evidence, asks clarifying
questions, produces an approved specification, generates a working application,
runs it in an isolated E2B sandbox, and independently reviews it with Muse
Glimmer.

This repository is the 24-hour hackathon MVP. See [plan.md](plan.md) for the
full product plan. The demo scenario is a restaurant order-management
dashboard; the restaurant is a demonstration scenario, not a product limit.

## Stack

- Frontend: React, Vite, TypeScript, Tailwind CSS (mobile-first)
- Backend: Python, FastAPI, Pydantic
- Persistence: SQLite (MVP)
- AI: Muse Spark + Voice Transcribe via Meta Model API
- Audit: Muse Glimmer on Modal (independent review)
- Execution: E2B sandboxes for generated apps
- Optional: Azure AI Speech TTS for the spoken walkthrough

## Prerequisites

- Python 3.11+
- Node.js 20+
- FFmpeg (needed from the media-ingestion milestone onward)

## Setup

```bash
# 1. Clone and enter the repo
git clone <your-public-repo-url>
cd shelfsense-ai

# 2. Backend environment (project-local virtualenv)
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt

# 3. Environment files (never commit real credentials)
cp .env.example .env
# Edit .env with your own keys; see "Environment variables" below.

# 4. Frontend dependencies
cd frontend
npm install
```

## Run (development)

Two terminals:

```bash
# Terminal 1 — backend (http://127.0.0.1:8000)
cd backend
source .venv/bin/activate
python -m app.main
# or: uvicorn app.main:app --reload
```

```bash
# Terminal 2 — frontend (http://localhost:5173)
cd frontend
npm run dev
```

Health check:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","service":"field-backend","env":"development"}
```

## Environment variables

Backend settings load from the repository-root `.env` (see `.env.example`).
Required at startup: none of the provider keys — the backend starts without
them, and each integration reports itself unavailable until configured. This
keeps every integration honest: nothing claims success before a real call.

| Variable | Purpose |
|---|---|
| `APP_ENV` / `APP_HOST` / `APP_PORT` | Backend runtime |
| `DATABASE_URL` | SQLite database location |
| `FRONTEND_ORIGIN` | Allowed CORS origin for the frontend |
| `META_MODEL_API_KEY` / `META_MODEL_BASE_URL` | Meta Model API access |
| `MUSE_SPARK_MODEL_ID` | Primary reasoning model (verify ID in docs) |
| `MUSE_TRANSCRIBE_MODEL_ID` | Narration transcription model (verify ID in docs) |
| `MODAL_ENDPOINT_URL` / `MODAL_API_KEY` | Muse Glimmer audit endpoint |
| `E2B_API_KEY` | Generated-app sandbox execution |
| `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` | Optional spoken walkthrough |
| `MEDIA_STORAGE_PATH` / `MAX_UPLOAD_SIZE_MB` | Upload storage and limits |

Frontend public config lives in `frontend/.env` (`VITE_API_URL` only).
Never put secrets in `VITE_` variables — they ship to the browser.

## Tests

```bash
cd backend
source .venv/bin/activate
python -m pytest
```

```bash
cd frontend
npm run typecheck
npm run build
```

## Project layout

```text
.
├── frontend/          # React/Vite/TS app (no secrets; calls backend only)
│   ├── public/        # manifest, icons
│   └── src/           # App shell, services, entry point
├── backend/           # FastAPI app (owns all secrets and provider calls)
│   ├── app/
│   │   ├── api/       # HTTP routers (health today, missions next)
│   │   ├── core/      # settings/config
│   │   └── main.py    # entry point
│   └── tests/
├── .env.example       # committed template (placeholders only)
├── .env               # local only, never committed
├── plan.md            # hackathon plan (source of truth for scope)
└── SECURITY.md        # secret-handling rules
```

## Status and honesty rules

- A stage succeeds only if the integration was actually executed and its
  result observed — no mocked provider success, no fake test results.
- Model IDs, endpoints, and SDK shapes must be verified against current
  official docs before integration (see plan.md §3).
- If an integration is unavailable, the app must say so instead of
  fabricating output.
