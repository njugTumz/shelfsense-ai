# FIELD — See the world. Understand the problem. Build the solution.

FIELD is an AI software team that turns observations of a real-world
environment into useful software. A business owner uploads a short video of an
operation; FIELD analyses the visual and audio evidence, asks clarifying
questions, produces an approved specification, generates a working application,
runs it in an isolated E2B sandbox, and independently reviews it with an
adversarial Muse Spark audit (Muse Glimmer preserved as the intended
auditor, not verified).

This repository is the 24-hour hackathon MVP. See [plan.md](plan.md) for the
full product plan. The demo scenario is a restaurant order-management
dashboard; the restaurant is a demonstration scenario, not a product limit.

## Stack

- Frontend: React, Vite, TypeScript, Tailwind CSS (mobile-first)
- Backend: Python, FastAPI, Pydantic
- Persistence: SQLite (MVP)
- AI: Muse Spark + Voice Transcribe via Meta Model API
- Audit: adversarial Muse Spark (verified); Muse Glimmer on Modal preserved, not verified
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

## Verification & Independent Audit

The VERIFY stage is the first complete vertical slice. For one mission it
combines two independent signals:

```text
                 Generated Application
                         │
                         ▼
                    E2B Sandbox
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
     Deterministic E2B       Adversarial Muse
         Verification          Spark Audit
              │                     │
              └──────────┬──────────┘
                         ▼
                 VERIFIED / WARNINGS /
                 FAILED / AUDIT_LIMITED
```

1. Generated applications run inside E2B. FIELD uploads the app files to
   an isolated sandbox, installs dependencies, runs the production build,
   serves the preview, and kills the sandbox afterwards.
2. FIELD performs deterministic runtime/build/functional verification:
   install, build, preview HTTP checks, referenced-asset checks, served
   workflow-content evidence, node execution of shipped logic, and real
   browser interaction tests (headless Firefox inside E2B: load, create
   order, advance status twice, reject invalid transitions, reload and
   confirm persistence). A check passes only if it actually ran.
3. FIELD performs an independent adversarial Muse Spark review. The
   auditor receives requirements, acceptance criteria, generated source,
   and the E2B test evidence — never the builder's reasoning — and
   returns structured findings (severity, category, evidence,
   recommendation) plus a PASS / PASS_WITH_WARNINGS / FAIL verdict.
4. Muse Glimmer is the intended independent auditor and its Modal
   integration code is preserved under `scripts/smoke/`.
5. Glimmer was **not verified** for the hackathon: the Modal GPU/account
   limit was reached before any genuine inference completed, so Glimmer
   is not part of the current runtime path.
6. FIELD therefore uses Spark + E2B as the current verified path. The
   auditor is a replaceable provider (`spark_adversarial` active,
   `glimmer` target), so Glimmer can be plugged back in after a genuine
   inference is observed — without rewriting verification.

Run the demo from the UI ("Run verification") or via the API:
`POST /api/missions/demo`, then `POST /api/missions/{id}/verify` with
`{"use_demo_app": true}`. The reference restaurant app is a controlled
starter for the demo; it is not claimed to be model-generated.

## AI Models & Intelligence Architecture

FIELD's intelligence comes from Meta models invoked with real provider
requests at each pipeline stage. The models below are distinct from the
infrastructure and tools that host, execute, and test the software they
produce (listed separately at the end of this section). Model IDs and
request shapes are verified against official docs in
[docs/integration-notes.md](docs/integration-notes.md).

### Muse Spark 1.3 (`muse-spark-1.3`)

Called via the Responses API with structured JSON output. Verified by
real calls across six roles:

- Multimodal visual analysis of extracted business-video frames.
- Interpretation and synthesis of visual evidence.
- Contextual owner interviews and follow-up questions (maximum 3).
- Structured requirements and specification generation.
- Constrained React application generation (`src/` only).
- Post-build independent adversarial audit of generated applications.

### Muse Voice Transcribe (`muse-voice-transcribe-1.0`)

Called via file transcription in ENDPOINTING mode (24 kHz mono WAV).
Verified against a real recording by the `transcribe` smoke test:

- Transcribes spoken narration from uploaded business videos.
- Provides textual evidence combined with visual observations and
  owner answers.
- Supports the OBSERVED / STATED / INFERRED evidence model:
  narration is STATED evidence, never OBSERVED.

### Muse Glimmer — NOT VERIFIED

Intended role: independent application/code auditing through Modal.
Status: **NOT VERIFIED.** The Modal deployment reached model-loading
stages but never successfully served inference. Glimmer was not used
to produce any FIELD verification verdict; every verdict comes from
deterministic E2B checks plus the adversarial Spark audit. The
integration code is preserved under `scripts/smoke/` for a future
retry with unblocked GPU quota.

### Why Meta models are central

Meta models let FIELD move from multimodal real-world input (video
frames plus spoken narration) to structured reasoning, testable
requirements, and generated software. They are part of the actual
product workflow — evidence analysis, interviewing, specification,
code generation, and audit — not decorative chatbot integrations.

### Technology stack (infrastructure and tools, not AI models)

| Technology | Role |
|---|---|
| React, Vite, TypeScript | Frontend |
| Python, FastAPI | Backend orchestration |
| FFmpeg | Video and audio processing |
| E2B | Isolated execution of generated applications |
| Playwright with Firefox | Real browser workflow testing |
| SQLite | Initial mission persistence |
| Caddy | HTTPS and reverse proxy in the Azure deployment |

## Current capabilities

Implemented and tested with real provider calls:

- Mission lifecycle with SQLite persistence (survives backend restarts).
- Video upload (validated, size-capped, privately stored) with
  FFmpeg frame/audio extraction, Muse Voice Transcribe narration,
  and Muse Spark visual analysis — synthesized into timestamped
  OBSERVED/STATED/INFERRED evidence that feeds the interviewer.
- AI interview: grounded follow-up questions (maximum 3), persisted
  answers, interview progress.
- Structured requirements generation: problem, roles, functional
  requirements, journeys, entities, testable acceptance criteria,
  assumptions, unresolved questions.
- Requirements review, revisioned change requests, and explicit approval
  with timestamp. Nothing builds without approval.
- Constrained code generation from the approved spec (FIELD-owned
  scaffold; model generates `src/` only; paths, sizes, and content
  validated before E2B).
- Deterministic E2B verification (build/serve/functional checks).
- Real browser workflow tests with screenshots persisted as evidence.
- Requirements/acceptance coverage mapping: static source traces vs
  executed checks vs explicitly UNVERIFIED.
- Adversarial Muse Spark audit with structured findings and
  VERIFIED / WARNINGS / FAILED / AUDIT_LIMITED verdicts.
- Frontend flows for interview/approval, build progress, generation
  summary, and verification results.

Implemented but not yet connected end-to-end:

- The reference-app verification demo remains available alongside
  generated-app builds.
- Labeled demo observations remain as a development/testing path for
  missions without an uploaded video; real missions refuse the fixture.
- One-cycle repair service (`POST /api/missions/{id}/repair`) is
  implemented with passing keyless tests, and a live repair+retest
  test exists; the repair path was not exercised in the production
  validation run.

Still planned (per `plan.md`): additional workflow test types beyond
the restaurant flow, presentation/TTS.

## Hackathon judging criteria

### Impact — 30%

FIELD lets a business owner show and explain a real-world operational
problem instead of first translating it into a technical specification:

```text
Real-world business
      ↓
Video + voice
      ↓
Understanding
      ↓
Requirements
      ↓
Working software
```

Implemented today: the connected slice from uploaded business video
through evidence analysis, grounded interview, approved specification,
constrained code generation, E2B execution, browser testing, and
independent review. Missions without a video keep a clearly labeled
demo-evidence path for development and testing.

### Meta AI Tooling — 25%

Meta AI capabilities central to the implementation:

- Muse Spark structured reasoning (`muse-spark-1.3` via Responses API)
  for the interviewer, requirements author, constrained developer, and
  adversarial auditor — verified by real calls.
- Muse Spark image understanding for frame analysis — verified by the
  `spark_images` smoke test against real frames.
- Muse Voice Transcribe for narration — verified by the `transcribe`
  smoke test against a real recording.
- Muse Glimmer as the intended independent auditor — integration code
  preserved, but **not verified** (Modal limit); the UI and API say so.

### Technical Depth & Execution — 20%

The system pipeline:

```text
SEE → UNDERSTAND → INTERVIEW → PLAN → BUILD → RUN → VERIFY → PRESENT
```

Implemented in this MVP: video upload with FFmpeg extraction,
transcription, and visual analysis feeding a grounded interview;
mission lifecycle with SQLite persistence
(`CREATED → … → RUNNING → AUDITING → VERIFIED / …`); constrained
code generation; isolated E2B execution with deterministic
build/runtime/functional checks plus real browser workflow tests;
structured adversarial AI audit; honest failure states (`FAILED`,
`AUDIT_LIMITED` with reasons); and a mission UI that shows evidence
without log-reading.

### Demo — 25%

The connected slice runs end-to-end with real provider calls today:

```text
Uploaded business video
↓
Frames + transcribed narration + visual analysis
↓
Timestamped OBSERVED/STATED/INFERRED evidence
↓
Grounded interview (max 3 questions)
↓
Structured specification
↓
Review → change requests → explicit approval
↓
Constrained app generation (FIELD scaffold + model src/)
↓
E2B build + serve + functional checks
↓
Real browser workflow (create → advance → reload) + screenshots
↓
Coverage mapping (supported vs UNVERIFIED criteria)
↓
Independent adversarial audit
↓
VERIFIED result with evidence
```

A reference-app verification demo also remains available. Video
ingestion is proven by a live end-to-end mission test on the sample
restaurant video, alongside the original provider smoke tests.

## Production Deployment

FIELD is deployed on Azure and validated with a real end-to-end run:

- Live URL: https://field-demo-k7q2.southafricanorth.azurecontainer.io
- Azure Container Instances + Caddy (TLS terminator, static frontend,
  API reverse proxy) with the FastAPI backend in one group.
- Region: South Africa North.
- Verified end-to-end production workflow: 462 seconds.
- Final verdict: VERIFIED_WITH_WARNINGS.
- Browser tests: 4 passed, 1 skipped (invalid-transition path).
- Independent Muse Spark audit: PASS_WITH_WARNINGS.

This is the observed result of the tested mission, not a guarantee:
every mission is verified independently, and future missions can
return different verdicts.

## Tests

```bash
cd backend
source .venv/bin/activate
python -m pytest
```

The default suite is keyless and fast. The live tests (real E2B
sandbox + real Muse Spark calls, a few minutes and a few cents) are
opt-in:

```bash
cd backend
source .venv/bin/activate
FIELD_LIVE_VERIFY=1 python -m pytest tests/test_verify_live.py tests/test_interview_live.py tests/test_build_live.py tests/test_media_live.py -s
```

The full video-to-verdict mission (roughly 5–15 minutes depending on
provider latency, on the order of $0.50 of provider spend) is a
separate opt-in test:

```bash
cd backend
source .venv/bin/activate
FIELD_LIVE_VERIFY=1 python -m pytest tests/test_e2e_live.py -s
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
│   └── src/           # interview + verification UI, API client, entry point
├── backend/           # FastAPI app (owns all secrets and provider calls)
│   ├── app/
│   │   ├── api/       # HTTP routers (health, missions)
│   │   ├── core/      # settings/config, SQLite mission store
│   │   ├── demo/      # reference app + labeled demo evidence
│   │   ├── models/    # mission/interview/verification contracts
│   │   ├── services/  # Spark client, interview, requirements, generation, E2B, auditors, media
│   │   └── main.py    # entry point
│   └── tests/         # keyless suites + opt-in live tests
├── scripts/smoke/     # provider smoke tests (incl. preserved Glimmer spike)
├── docs/              # verified integration notes
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
