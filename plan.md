# FIELD --- 24-Hour Hackathon MVP Plan

> **Tagline:** See the world. Understand the problem. Build the
> solution.
>
> **MVP promise:** A business owner uploads a short video of a
> real-world operation. FIELD analyses the available visual and audio
> evidence, asks clarifying questions, produces an approved software
> specification, generates a working application, runs it in an isolated
> E2B sandbox, and independently reviews it with Muse Glimmer.

------------------------------------------------------------------------

## 1. Mission and delivery constraints

FIELD is an AI software team that turns observations of a real-world
environment into useful software.

This is a **24-hour, solo-developer hackathon MVP**. The objective is to
demonstrate one complete, credible vertical slice---not to build a
production-ready, general-purpose autonomous software platform.

### Product workflow

**SEE → UNDERSTAND → INTERVIEW → PLAN → BUILD → TEST → AUDIT → PRESENT**

1.  **See:** The user records or uploads a short video of a business
    environment, ideally with spoken narration.
2.  **Understand:** FIELD extracts representative video frames and
    audio, transcribes narration, and combines the evidence using Meta
    Muse Spark.
3.  **Interview:** FIELD asks a small number of relevant follow-up
    questions.
4.  **Plan:** FIELD creates a structured specification and asks the user
    to approve it.
5.  **Build:** FIELD generates a constrained React application.
6.  **Test:** FIELD builds and runs the generated application in E2B and
    performs basic checks.
7.  **Audit:** Muse Glimmer independently reviews the specification and
    generated implementation. Findings are validated and reported.
8.  **Present:** FIELD shows the running application and explains what
    was built.

### Demonstration scenario

Use one small restaurant as the repeatable demo environment. The owner
records the counter and order workflow, explaining how staff receive,
prepare, and track orders and where problems occur.

The generated application should be a genuinely interactive restaurant
order-management dashboard with:

-   An order list.
-   Customer and order details.
-   Order statuses such as Received, Preparing, Ready, and Completed.
-   Working status updates.
-   A simple operational overview.
-   Sample data where real business data is not available.

The restaurant is a demonstration scenario, not a limitation on FIELD's
long-term product vision.

### Definition of success

A judge should be able to see a clear connection between:

-   What the user recorded and said.
-   What FIELD observed and inferred.
-   What FIELD asked the user.
-   What requirements the user approved.
-   What software FIELD generated.
-   Whether the generated application actually runs and works.
-   What the independent audit found.

Do not claim that a stage succeeded unless the integration was actually
executed and its result observed.

------------------------------------------------------------------------

## 2. Scope: must-have, should-have, and deferred

### P0 --- Required for submission

-   Public GitHub repository containing the hackathon implementation.
-   Mobile-responsive React/Vite/TypeScript frontend.
-   FastAPI backend.
-   Mission creation and mission status.
-   Video upload from a mobile or desktop browser.
-   Server-side extraction of representative frames and audio.
-   Narration transcription using Muse Voice Transcribe, subject to
    verified API support.
-   Visual and contextual analysis using supported Meta Model API
    capabilities, with Muse Spark as the primary reasoning model.
-   A short follow-up interview.
-   Structured requirements and explicit user approval.
-   Generation of a constrained React application.
-   Execution of the generated application in E2B.
-   Basic build and functional checks.
-   A genuine Muse Glimmer inference and independent audit, if
    deployment is feasible within the allocated time.
-   Clear status and error reporting.
-   README with setup instructions and an honest description of
    integrations.

### P1 --- Add only after P0 works

-   Azure AI Speech text-to-speech walkthrough.
-   Installable PWA polish, icons, and offline app shell.
-   More detailed visual evidence timeline.
-   One automated repair-and-retest cycle.
-   Improved application preview experience.
-   Additional mobile usability polish.

### Explicitly deferred

Do not build these during the 24-hour MVP unless all P0 requirements are
complete:

-   Real-time video streaming or live camera conversations.
-   Ten independently deployed agents or model instances.
-   Complex multi-agent frameworks.
-   Neo4j or knowledge graph infrastructure.
-   General-purpose database generation for arbitrary applications.
-   Multi-tenant accounts, billing, and organization management.
-   Push notifications.
-   Sophisticated offline synchronization.
-   Production deployment of every generated application.
-   Complex role-based access control.
-   Multiple autonomous repair loops.
-   Advanced SAM segmentation unless it is already working and
    materially improves the demo.
-   A general-purpose app builder supporting arbitrary stacks.

**Scope rule:** one reliable end-to-end mission is more valuable than
many incomplete features.

------------------------------------------------------------------------

## 3. Technology stack

  -----------------------------------------------------------------------
  Area                    Technology              MVP responsibility
  ----------------------- ----------------------- -----------------------
  Frontend                React, Vite, TypeScript FIELD user interface

  Styling                 Tailwind CSS            Responsive mobile-first
                                                  interface

  PWA                     Web app manifest and    Installable app shell
                          service worker          where supported

  Backend                 Python, FastAPI,        API and orchestration
                          Pydantic                

  Primary reasoning       Meta Muse Spark via     Analysis, interview,
                          Meta Model API          planning,
                                                  code-generation
                                                  coordination

  Speech recognition      Muse Voice Transcribe   Transcribe spoken
                          via Meta Model API      narration

  Visual perception       Supported Meta Model    Interpret selected
                          API image-input         frames
                          capability; SAM 3.1     
                          only if verified and    
                          useful                  

  Open-weight audit       Muse Glimmer hosted on  Independent
                          Modal                   code/specification
                                                  review

  Generated app execution E2B                     Isolated build and
                                                  runtime for generated
                                                  software

  Speech synthesis        Azure AI Speech TTS     Optional spoken
                                                  presentation

  Video/audio processing  FFmpeg and Python       Extract frames and
                          libraries               audio

  Persistence             SQLite initially        Missions, states,
                                                  results, and audit
                                                  records

  Source control          Public GitHub           Hackathon source and
                          repository              documentation

  Hosting                 Existing available      Host FIELD
                          Azure or other approved frontend/backend if
                          hosting                 time permits
  -----------------------------------------------------------------------

### Integration verification rule

Do not assume that a model, endpoint, SDK, runtime, or hardware
configuration supports a capability simply because it appears in a
product description.

Before implementing an integration:

1.  Read the current official documentation.
2.  Confirm the exact model identifier and endpoint.
3.  Confirm supported input types, payload sizes, and response format.
4.  Make a minimal real API request.
5.  Record the working request shape in the project documentation
    without recording secrets.

Do not invent API response formats or claim a model integration works
based on a mocked response.

------------------------------------------------------------------------

## 4. System architecture

``` text
┌──────────────────────────────────────────────────────────────┐
│                         FIELD PWA                            │
│  Capture / Upload → Mission → Interview → Workspace → Preview │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTPS
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                    FASTAPI BACKEND                            │
│                                                              │
│  Mission API ── Mission State ── Workflow Orchestrator        │
│       │                              │                       │
│       ▼                              ▼                       │
│  Media Pipeline                 Agent Instructions            │
│  ├─ Video metadata              ├─ Analyst                    │
│  ├─ Frame extraction            ├─ Interviewer                 │
│  ├─ Audio extraction            ├─ Planner                     │
│  └─ Transcription               ├─ Architect                   │
│                                 ├─ Developer                   │
│                                 ├─ Tester                      │
│                                 ├─ Glimmer Auditor             │
│                                 └─ Presenter                   │
└──────────────────────────────┬───────────────────────────────┘
                               │
              ┌────────────────┼─────────────────┐
              ▼                ▼                 ▼
       Meta Model API       E2B Sandbox       Modal Endpoint
       Muse Spark           Generated app     Muse Glimmer
       Voice Transcribe     Build and run     Independent audit
              │                │                 │
              └────────────────┼─────────────────┘
                               ▼
                     Mission results and evidence
                               │
                               ▼
                       FIELD application preview
```

### Architectural principles

-   The backend owns all secrets and external API calls.
-   The browser never receives provider API keys.
-   The orchestrator coordinates sequential stages; do not build a
    complex agent framework unnecessarily.
-   Agent roles are initially prompt/instruction files, not separate
    deployed services.
-   Generated code runs in E2B, not in the FIELD backend process.
-   Treat uploaded media and model-generated content as untrusted input.
-   Keep the original evidence, extracted artifacts, generated
    specification, test results, and audit report associated with the
    mission.
-   Make mission progress visible to the user.
-   Persist enough state to recover from a failed stage without
    repeating every previous stage.

------------------------------------------------------------------------

## 5. Public repository security --- mandatory

**This is a public repository. No API keys, tokens, passwords, private
certificates, connection strings containing credentials, or other
secrets may be committed.**

### Required files

-   `.env.example`: variable names and safe placeholder values only.
-   `.gitignore`: excludes `.env`, local secret files, credentials,
    private keys, generated media, local databases, caches, and build
    artifacts.
-   `README.md`: explains how developers create their own local `.env`
    file and obtain their own credentials.
-   Optional `SECURITY.md`: explains how to report a security issue
    without publishing a secret.

### Example `.gitignore`

``` gitignore
# Environment and secrets
.env
.env.*
!.env.example
*.pem
*.key
*.p12
*.pfx
credentials.json
secrets/
secrets.*
.azure/
.aws/

# Python
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.mypy_cache/

# Node
node_modules/
dist/
.vite/

# Local data and uploaded media
*.db
*.sqlite
*.sqlite3
uploads/
media/
tmp/
temp/

# Logs and operating-system files
*.log
.DS_Store
Thumbs.db
```

Review this ignore list against the actual project before committing. Do
not ignore source files or safe documentation accidentally.

### Example `.env.example`

``` dotenv
# Backend
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=8000
DATABASE_URL=sqlite:///./field.db
FRONTEND_ORIGIN=http://localhost:5173

# Meta Model API — obtain credentials independently
META_MODEL_API_KEY=replace_with_your_own_key
MUSE_SPARK_MODEL_ID=replace_with_verified_model_id
MUSE_TRANSCRIBE_MODEL_ID=replace_with_verified_model_id

# Modal / Muse Glimmer — server-side only
MODAL_ENDPOINT_URL=replace_with_your_own_endpoint
MODAL_API_KEY=replace_with_your_own_key

# E2B — server-side only
E2B_API_KEY=replace_with_your_own_key

# Azure Speech — optional
AZURE_SPEECH_KEY=replace_with_your_own_key
AZURE_SPEECH_REGION=replace_with_your_region

# Optional storage configuration
MEDIA_STORAGE_PATH=./uploads
MAX_UPLOAD_SIZE_MB=100
```

The example above is illustrative. Adjust names to the actual SDKs and
verified endpoints used by the implementation. Placeholder values must
never be real credentials.

### Secret-handling rules

1.  Store real credentials only in an untracked local `.env` file or an
    approved secrets manager.
2.  Load secrets on the backend using environment variables.
3.  Never prefix a secret with `VITE_`. Vite variables prefixed with
    `VITE_` are exposed to browser code.
4.  Never place secrets in React source, browser local storage, session
    storage, HTML, client-side configuration, logs, screenshots, test
    fixtures, or demo recordings.
5.  Never print full tokens or authorization headers in logs or error
    messages.
6.  Do not return provider credentials to the frontend.
7.  Do not put secrets in GitHub Issues, pull requests, commit messages,
    README files, or public documentation.
8.  Use separate credentials for development and production where
    available.
9.  Restrict and rotate credentials if exposure is suspected.
10. Use GitHub secret scanning and push protection where available, but
    do not treat them as a replacement for careful review.

### Before every public push

Run:

``` bash
git status --short
git check-ignore -v .env
git diff --cached
```

Review staged files and search for likely secrets. If available, run a
secret scanner such as Gitleaks against the repository and staged
changes.

Confirm that `.env` is not tracked:

``` bash
git ls-files .env
```

This command should return no result.

If a secret is accidentally committed:

-   Revoke or rotate it immediately.
-   Remove it from the current source tree.
-   Remove it from Git history where appropriate.
-   Do not assume deleting it in a later commit makes the exposed
    credential safe.
-   Review logs and provider activity for possible misuse.

### Frontend/backend boundary

The frontend may contain public configuration such as the backend base
URL. It must not contain:

-   Meta API keys.
-   Modal credentials.
-   E2B API keys.
-   Azure Speech subscription keys.
-   Database credentials.
-   Private storage credentials.

All calls to these services must pass through the backend.

### Uploaded media privacy

-   Limit accepted file types and file sizes.
-   Generate server-side filenames rather than trusting user filenames.
-   Validate file content and media metadata.
-   Do not execute uploaded files.
-   Keep uploaded files outside public static directories.
-   Use temporary storage and delete unnecessary media after the demo or
    according to the configured retention policy.
-   Avoid placing private customer information in public demo videos or
    repository fixtures.

------------------------------------------------------------------------

## 6. Mission data model and workflow

Use SQLite for the MVP. Keep the schema small and explicit.

### Mission

Suggested fields:

-   `id`: UUID.
-   `status`: current workflow state.
-   `created_at`, `updated_at`.
-   `original_video_path` or private storage reference.
-   `video_metadata`: duration, dimensions, and file size.
-   `transcript`: extracted narration.
-   `visual_observations`: timestamped observations.
-   `environment_analysis`: structured Muse Spark result.
-   `interview_history`: questions and user answers.
-   `requirements`: approved specification.
-   `generated_app_reference`: E2B sandbox/application reference.
-   `build_result`: build logs and outcome.
-   `test_result`: test summary.
-   `glimmer_audit`: structured audit findings.
-   `presentation`: final summary and optional TTS artifact.
-   `error`: safe, user-readable stage error.

Avoid storing credentials or authorization headers in mission records.

### State machine

``` text
CREATED
  ↓
VIDEO_UPLOADED
  ↓
ANALYZING
  ↓
INTERVIEWING
  ↓
REQUIREMENTS_READY
  ↓
APPROVED
  ↓
BUILDING
  ↓
RUNNING
  ↓
AUDITING
  ↓
VERIFIED
  ↓
PRESENTED
```

A failed state should capture which stage failed and a safe explanation.
Provide a way to retry that stage where feasible.

Do not mark a mission `VERIFIED` merely because code generation
completed. Verification requires observed build/test results and an
audit status that clearly distinguishes successful, failed, unavailable,
or skipped checks.

------------------------------------------------------------------------

## 7. Media ingestion and environment understanding

### Input modes

Support in this order:

1.  Prerecorded video upload, optionally containing narration.
2.  Video without narration.
3.  Video plus separately entered context.
4.  Image upload as a fallback if video processing fails.

The MVP does not require live video streaming.

### Video pipeline

``` text
Uploaded video
    │
    ├── Validate file type, size, duration
    │
    ├── Read metadata
    │
    ├── Extract representative frames
    │      └── Start with 5–8 frames across the clip
    │
    └── Extract audio
           │
           ▼
     Muse Voice Transcribe
           │
           ▼
      Timestamped transcript
           │
           ├──────────────┐
           ▼              ▼
     Selected frames   Narration
           │              │
           └──────┬───────┘
                  ▼
              Muse Spark
                  │
                  ▼
       Structured environment analysis
```

### Evidence discipline

Keep these categories separate:

-   **Visual observation:** directly visible in a selected frame.
-   **User statement:** information spoken or typed by the user.
-   **Model interpretation:** a possible explanation or inference.
-   **Unknown/unverified:** information that needs clarification.

Do not infer exact inventory quantities, prices, customer identities,
financial figures, or hidden business processes from a video without
evidence.

Preserve timestamps where possible so the user can understand which part
of the recording supports an observation.

### Suggested service modules

-   `media_ingestion_service.py`
-   `video_frame_service.py`
-   `audio_extraction_service.py`
-   `transcription_service.py`
-   `visual_analysis_service.py`
-   `environment_context_service.py`

Keep the implementation minimal. Do not build a separate microservice
for every module.

------------------------------------------------------------------------

## 8. AI team and agent instructions

Use one orchestrator and one primary reasoning model, Muse Spark, for
multiple sequential roles. Each role has a separate instruction file so
responsibilities and handoffs are clear.

Suggested files:

``` text
agents/
├── analyst.md
├── interviewer.md
├── planner.md
├── architect.md
├── developer.md
├── tester.md
├── glimmer-auditor.md
└── presenter.md
```

### Analyst

Input: visual observations, transcript, user-provided context.

Responsibilities:

-   Summarize the environment.
-   Identify business actors and workflows.
-   Identify observed pain points.
-   Separate facts from assumptions.
-   Identify missing information.

Output: structured environment analysis.

### Interviewer

Input: environment analysis and unresolved questions.

Responsibilities:

-   Ask one concise question at a time.
-   Ask no more than three high-value questions in the MVP.
-   Avoid asking for information already provided.
-   Clarify the workflow, pain point, and desired outcome.
-   Confirm the user's understanding before handing off.

Output: interview history and confirmed context.

### Planner

Input: analysis and interview.

Responsibilities:

-   Define the problem.
-   Propose a focused solution.
-   Identify user journeys.
-   Define MVP features and exclusions.
-   Create acceptance criteria.
-   Ask for user approval.

Output: structured, user-approved requirements.

### Architect

Input: approved requirements.

Responsibilities:

-   Define the generated app structure.
-   Define screens and interactions.
-   Define data models and state.
-   Define permitted dependencies.
-   Define build and test commands.
-   Keep the design within the supported application template.

Output: application build specification.

### Developer

Input: approved specification and controlled starter template.

Responsibilities:

-   Generate and modify application source.
-   Keep dependencies constrained.
-   Implement real interactions.
-   Follow the required project structure.
-   Return a file manifest and build instructions.

Output: generated source files and build instructions.

### Tester

Input: generated application and acceptance criteria.

Responsibilities:

-   Run build checks.
-   Run available automated tests.
-   Check important user interactions.
-   Record failures with reproducible details.
-   Never report a test as passing if it was not run.

Output: test report.

### Glimmer Auditor

Input: approved requirements, relevant generated source, and test
report.

Responsibilities:

-   Independently review whether implementation matches requirements.
-   Identify defects, insecure patterns, missing validation, and likely
    failure points.
-   Return findings with severity and file/line references where
    possible.
-   Avoid modifying source code directly.
-   Distinguish confirmed defects from suggestions.

Output: structured audit report.

### Presenter

Input: approved requirements, running application, tests, and audit.

Responsibilities:

-   Explain what the user asked for.
-   Explain what FIELD built.
-   Highlight the main working features.
-   Disclose limitations and skipped checks.
-   Optionally produce a short spoken walkthrough using Azure TTS.

Output: concise presentation script and optional audio.

------------------------------------------------------------------------

## 9. Muse Spark integration

Muse Spark is the primary reasoning model for the MVP.

Use it for:

-   Environment understanding.
-   Combining visual observations and narration.
-   Follow-up interview questions.
-   Requirements generation.
-   Application planning and architecture.
-   Application code generation or code-generation coordination.
-   Repair suggestions based on actual build errors.
-   Final presentation.

### Structured outputs

Use Pydantic schemas to validate model outputs before storing or passing
them to the next stage.

Example environment analysis:

``` json
{
  "business_type": "restaurant",
  "workflow_summary": "Customers place orders at the counter and staff prepare them.",
  "actors": ["customer", "cashier", "kitchen staff"],
  "observations": [
    {
      "timestamp_seconds": 12,
      "type": "visual_observation",
      "description": "A counter is visible where orders are received."
    }
  ],
  "user_statements": [],
  "pain_points": [],
  "assumptions": [],
  "open_questions": []
}
```

Example approved requirements:

``` json
{
  "problem_statement": "The restaurant needs a clearer way to track incoming orders.",
  "users": ["cashier", "kitchen staff"],
  "features": [
    "View incoming orders",
    "Update order status",
    "View order details"
  ],
  "acceptance_criteria": [
    "A user can move an order from Received to Preparing.",
    "A user can move an order from Preparing to Ready."
  ],
  "excluded_features": [
    "Online payments",
    "Customer accounts",
    "Live delivery tracking"
  ]
}
```

These are examples of the application's internal data contract, not
claims about a provider's API response format.

### Reliability rules

-   Validate every structured response.
-   Retry malformed output once with a corrective instruction.
-   If still invalid, show a recoverable error.
-   Do not silently replace a real model response with a fabricated one.
-   Preserve enough model and stage metadata to explain the workflow.
-   Avoid sending unnecessary personal or sensitive information to model
    APIs.

------------------------------------------------------------------------

## 10. Muse Glimmer on Modal

Muse Glimmer is intended to be the independent reviewer and is important
to the open-weight-model demonstration.

### Deployment spike

Do this early, not at the end:

1.  Verify the official model identifier and current model access
    instructions.
2.  Verify license and permitted use for the hackathon.
3.  Confirm supported inference runtime and model format.
4.  Confirm available Modal hardware is sufficient.
5.  Deploy a minimal endpoint.
6.  Authenticate the endpoint.
7.  Make one real inference request.
8.  Record measured latency and any deployment constraints.

Do not assume a particular GPU, vLLM/SGLang/llama.cpp support, or memory
requirement without validating it against the exact model variant and
deployment documentation.

### Audit workflow

``` text
Approved requirements
        +
Generated source files
        +
Test report
        │
        ▼
Authenticated Modal endpoint
        │
        ▼
Muse Glimmer
        │
        ▼
Structured independent findings
        │
        ▼
FIELD validates and displays findings
```

Glimmer must be used for actual inference, not merely mentioned in the
README.

### Suggested audit result

``` json
{
  "overall_assessment": "needs_attention",
  "findings": [
    {
      "severity": "medium",
      "category": "validation",
      "file": "src/components/OrderList.tsx",
      "line": null,
      "finding": "The status update handler should validate the requested status.",
      "recommendation": "Restrict status changes to the supported status values.",
      "confidence": "medium"
    }
  ],
  "limitations": []
}
```

This is an illustrative output contract. Validate the actual model
output before relying on it.

### Independence and repair

-   Glimmer does not directly write to or modify generated source.
-   FIELD checks whether findings are relevant and reproducible.
-   The developer role may address one or more validated findings.
-   The tester reruns affected tests.
-   Maximum one repair-and-retest cycle in the MVP.
-   Clearly display if the audit was unavailable, failed, or skipped.

If Glimmer deployment is blocked after the two-hour early feasibility
window, continue the main MVP. Revisit deployment after the generated
application is running. Do not sacrifice the entire product to an
unverified deployment path.

------------------------------------------------------------------------

## 11. Generated application and E2B

### Constrained generation

Do not ask the model to generate an arbitrary application with unlimited
framework and dependency choices.

Use a known React/Vite starter template and a constrained build
contract:

-   React.
-   TypeScript.
-   Vite.
-   Existing Tailwind configuration.
-   A small permitted dependency set.
-   Known entry point.
-   Known build command.
-   Known start command.
-   Defined output manifest.

This improves the probability of producing a buildable application.

### E2B workflow

1.  Create a sandbox using the current supported E2B SDK.
2.  Upload or create the generated project files.
3.  Install dependencies.
4.  Run the production build.
5.  Start the application server.
6.  Capture build output and runtime errors.
7.  Obtain the preview URL using the supported E2B mechanism.
8.  Display the application preview in FIELD.
9.  Run basic checks against the actual running app.

Never execute model-generated code directly in the FIELD backend or on
the developer's host machine. Treat E2B as an isolated execution
boundary, while still applying resource limits and safe handling.

### Repair loop

``` text
Generate source
    ↓
Run build in E2B
    ↓
Build succeeds?
    ├── Yes → Start app and test
    └── No  → Capture actual error
                 ↓
             Developer repair
                 ↓
             Rebuild once
```

Do not let a repair loop run indefinitely. Record when the application
cannot be built.

### Generated app acceptance criteria

For the restaurant example:

-   Orders are displayed.
-   Order details can be viewed.
-   Status can be changed.
-   Status changes update the visible interface.
-   The application builds successfully.
-   The running preview can be opened.
-   The application works at a mobile viewport.

------------------------------------------------------------------------

## 12. Frontend experience and PWA

### Main screens

1.  **Home:** Start a new FIELD mission and see mission progress.
2.  **Capture:** Upload or select a short video, preview it, and submit
    it.
3.  **Analysis:** Display processing progress, selected frames,
    transcript, and observations.
4.  **Interview:** Ask and answer clarifying questions.
5.  **Workspace:** Review proposed requirements and approve them.
6.  **Build:** Show generation, E2B build, test, and audit status.
7.  **Application Preview:** Display the running generated application
    and final explanation.

### Mobile-first requirements

-   Responsive layout starting from narrow phone widths.
-   Large touch-friendly controls.
-   Clear video upload and preview.
-   Upload progress and actionable errors.
-   No essential workflow dependent on hover.
-   Readable mission status and progress.
-   Preview experience usable on mobile.

### PWA requirements

For the MVP:

-   Add a valid web app manifest.
-   Add application icons.
-   Add a basic service worker/offline app shell if stable.
-   Make the app installable where supported.
-   Ensure the core workflow is usable online.

Do not promise reliable background recording or background uploads on
every mobile browser. Browser support varies, particularly on iOS.
Prefer explicit recording/upload completion, visible progress, and retry
support.

------------------------------------------------------------------------

## 13. Backend API contract

Keep the API small.

  --------------------------------------------------------------------------------
  Method                  Endpoint                         Responsibility
  ----------------------- -------------------------------- -----------------------
  `GET`                   `/health`                        Backend health

  `POST`                  `/api/missions`                  Create a mission

  `GET`                   `/api/missions/{id}`             Retrieve mission and
                                                           progress

  `POST`                  `/api/missions/{id}/video`       Upload video

  `POST`                  `/api/missions/{id}/analyze`     Start media and
                                                           environment analysis

  `POST`                  `/api/missions/{id}/interview`   Submit interview answer
                                                           / receive next question

  `POST`                  `/api/missions/{id}/approve`     Approve requirements

  `POST`                  `/api/missions/{id}/build`       Generate and run
                                                           application

  `POST`                  `/api/missions/{id}/audit`       Run Glimmer audit

  `GET`                   `/api/missions/{id}/artifacts`   Retrieve safe artifact
                                                           metadata
  --------------------------------------------------------------------------------

Use the simplest progress mechanism that works reliably. Polling mission
status is acceptable for the MVP; use SSE only if it can be implemented
without delaying the core workflow.

------------------------------------------------------------------------

## 14. Suggested project structure

``` text
field/
├── frontend/
│   ├── public/
│   │   ├── manifest.json
│   │   └── icons/
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── services/
│       ├── hooks/
│       └── App.tsx
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── agents/
│   │   ├── services/
│   │   ├── schemas/
│   │   ├── models/
│   │   └── core/
│   └── requirements.txt
│
├── agents/
│   ├── analyst.md
│   ├── interviewer.md
│   ├── planner.md
│   ├── architect.md
│   ├── developer.md
│   ├── tester.md
│   ├── glimmer-auditor.md
│   └── presenter.md
│
├── docs/
│   ├── architecture.md
│   └── demo-script.md
│
├── .env.example
├── .gitignore
├── plan.md
├── README.md
└── SECURITY.md
```

Adjust structure if the starter project already has a sensible layout.
Do not spend time creating empty folders or files that the
implementation does not use.

------------------------------------------------------------------------

## 15. Environment configuration

Load all secrets from backend environment variables.

Suggested variable names:

``` dotenv
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=8000
DATABASE_URL=sqlite:///./field.db
FRONTEND_ORIGIN=http://localhost:5173

META_MODEL_API_KEY=
MUSE_SPARK_MODEL_ID=
MUSE_TRANSCRIBE_MODEL_ID=

MODAL_ENDPOINT_URL=
MODAL_API_KEY=

E2B_API_KEY=

AZURE_SPEECH_KEY=
AZURE_SPEECH_REGION=

MEDIA_STORAGE_PATH=./uploads
MAX_UPLOAD_SIZE_MB=100
```

The committed `.env.example` must use blank values or obvious
placeholders only. Developers create their own untracked `.env` from the
example.

Do not expose any of these values to the frontend. Verify the actual
model identifiers and SDK configuration before implementation.

------------------------------------------------------------------------

## 16. Twenty-four-hour execution schedule

The schedule assumes one developer and access to required hackathon
credentials.

### T+00:00--T+01:00 --- Repository and foundation

-   Create public repository.
-   Add `.gitignore` and `.env.example` before adding credentials or
    local configuration.
-   Scaffold React/Vite/TypeScript/Tailwind.
-   Scaffold FastAPI/Pydantic.
-   Add health endpoint.
-   Verify both applications start.

**Milestone:** frontend and backend run locally.

### T+01:00--T+02:00 --- Integration smoke tests

-   Make one real Muse Spark request.
-   Create and execute a minimal E2B sandbox command.
-   Verify media-processing dependencies.
-   Verify model API input capabilities and identifiers.
-   Check Glimmer model access and deployment documentation.

**Milestone:** critical provider access is understood and basic calls
work.

### T+02:00--T+04:00 --- Glimmer feasibility spike

-   Deploy minimal Glimmer inference on Modal.
-   Authenticate the endpoint.
-   Execute one real inference.
-   Record limitations and latency.

**Hard cutoff:** if blocked after two hours, defer and continue the main
product.

### T+04:00--T+06:00 --- Video ingestion

-   Implement video upload.
-   Validate file type and size.
-   Extract metadata.
-   Extract 5--8 representative frames.
-   Extract audio.

**Milestone:** video becomes inspectable frames and audio.

### T+06:00--T+08:00 --- Transcription and analysis

-   Integrate Muse Voice Transcribe.
-   Analyze selected frames with verified supported input.
-   Combine observations and transcript using Muse Spark.
-   Validate structured analysis.

**Milestone:** FIELD produces a grounded description of the restaurant
workflow.

### T+08:00--T+10:00 --- Interview and requirements

-   Implement interviewer role.
-   Ask up to three relevant questions.
-   Capture answers.
-   Generate requirements and acceptance criteria.
-   Present an approval step.

**Milestone:** user-approved requirements exist.

### T+10:00--T+12:00 --- Application specification

-   Generate application architecture.
-   Define data model and screens.
-   Define allowed dependencies.
-   Define build/test commands.
-   Validate the specification.

**Milestone:** build-ready specification exists.

### T+12:00--T+14:00 --- Code generation

-   Prepare known-good React starter.
-   Generate application source.
-   Validate file manifest.
-   Send files to E2B.

**Milestone:** generated project exists in E2B.

### T+14:00--T+17:00 --- Build and runtime

-   Install dependencies.
-   Run build.
-   Capture errors.
-   Repair actual failures.
-   Start application.
-   Retrieve preview URL.
-   Test core interactions.

**Milestone:** working restaurant application runs in E2B.

### T+17:00--T+18:00 --- Independent audit

-   Submit requirements, source, and test results to Glimmer.
-   Retrieve structured findings.
-   Validate and display findings.

**Milestone:** genuine independent audit is recorded, or audit
limitation is clearly documented.

### T+18:00--T+20:00 --- Testing and repair

-   Verify build.
-   Test order status interactions.
-   Test mobile viewport.
-   Address high-priority defects.
-   Re-run affected tests.

**Milestone:** verified demo application.

### T+20:00--T+21:00 --- PWA and presentation polish

-   Add manifest and icons.
-   Add stable offline app shell if feasible.
-   Improve progress indicators.
-   Add Azure TTS only if the core flow is complete.

### T+21:00--T+22:00 --- Full end-to-end rehearsal

-   Start with a fresh mission.
-   Upload video.
-   Complete analysis and interview.
-   Approve requirements.
-   Generate and run application.
-   Review audit.
-   Open final preview on mobile viewport.

**Milestone:** complete demo works without developer intervention.

### T+22:00--T+23:00 --- Public repository preparation

-   Review source and documentation.
-   Ensure no secrets are tracked.
-   Run secret scanning if available.
-   Push final implementation.
-   Write README, architecture notes, and setup instructions.
-   Confirm repository is public.

### T+23:00--T+24:00 --- Demo and submission

-   Record a concise three-minute demo.
-   Verify public repository and demo link.
-   Check submission requirements.
-   Submit before the deadline.

**Do not use the final hour for new features.**

------------------------------------------------------------------------

## 17. Fallback decisions

  -----------------------------------------------------------------------
  Risk                                Fallback
  ----------------------------------- -----------------------------------
  Browser camera recording fails      Upload an existing prerecorded
                                      video

  Narration extraction fails          Allow typed context or a text
                                      transcript

  Transcription API is unavailable    Use a clearly disclosed manual
                                      transcript fallback

  Visual model input fails            Use the verified supported
                                      image-input route

  Glimmer deployment is blocked       Continue the main build; retry
                                      after the running app milestone

  E2B integration fails               Debug with a minimal known-good
                                      project; do not execute generated
                                      code on the backend host

  Generated app is unreliable         Use a controlled starter and
                                      generate constrained
                                      screens/features

  Azure TTS fails                     Present the result in text

  PWA installability is unstable      Deliver a responsive web
                                      application first
  -----------------------------------------------------------------------

A fallback must be identified honestly in the README and demo. Never
present a manual or mocked result as a successful live model
integration.

------------------------------------------------------------------------

## 18. Three-minute hackathon demo

### 0:00--0:30 --- SEE

Show a business owner uploading a short restaurant video with narration.

Explain that FIELD starts with the real environment, not a blank prompt.

### 0:30--1:00 --- UNDERSTAND

Show representative frames, transcript, and the environment analysis.

Highlight how FIELD separates observed evidence from assumptions.

### 1:00--1:30 --- INTERVIEW AND PLAN

Show two or three clarifying questions and the approved software
requirements.

### 1:30--2:15 --- BUILD

Show Muse Spark's generated specification and code being built in E2B.

Open the resulting application.

### 2:15--2:45 --- VERIFY

Show real test results and Muse Glimmer's independent findings.

Explain how the audit is separate from the model that generated the
application.

### 2:45--3:00 --- PRESENT

Demonstrate an actual order status update in the running restaurant
application.

Close with:

**"FIELD turns what you see in the real world into software you can
use."**

The demo must show only functionality and code genuinely built during
the hackathon, consistent with the event rules.

------------------------------------------------------------------------

## 19. README requirements

The public README should include:

1.  Project name and tagline.
2.  Problem and solution.
3.  Short architecture diagram.
4.  Demo video and deployed demo links, when available.
5.  Technology stack.
6.  Explanation of how Muse Spark, Muse Voice Transcribe, and Muse
    Glimmer are used.
7.  E2B execution workflow.
8.  Local setup instructions.
9.  Environment variable setup using `.env.example`.
10. Testing instructions.
11. Current limitations and known issues.
12. Clear identification of what was built during the hackathon.

Never include real credentials or private endpoints containing tokens in
README examples.

------------------------------------------------------------------------

## 20. Instructions to Muse Code

You are the lead implementation engineer for FIELD.

Read this entire `plan.md` before changing the project.

We have 24 hours, one developer, and a hackathon deadline. Your
objective is a working, demonstrable vertical slice.

### Before coding

1.  Inspect the existing repository and preserve useful working code.
2.  Identify missing credentials without printing or exposing their
    values.
3.  Verify current official API documentation for every provider
    integration.
4.  Confirm model identifiers, supported inputs, and deployment
    constraints.
5.  Create a short prioritized task checklist.
6.  Identify the highest-risk integration.
7.  Establish the frontend/backend foundation and test it.

### During implementation

-   Work in small, testable increments.
-   Prioritize P0 requirements.
-   Keep secrets server-side and out of Git.
-   Do not fabricate model responses or integration success.
-   Do not over-engineer.
-   Do not add dependencies without a clear need.
-   Do not create empty abstractions for hypothetical future features.
-   Use the controlled application template for code generation.
-   Run generated code only in E2B.
-   Keep Glimmer independent from the developer role.
-   Record actual test and audit results.
-   Make recoverable failures visible.

### After every milestone

Report:

-   What was implemented.
-   What was actually tested.
-   What works.
-   What failed.
-   What credentials or external access are missing, without revealing
    secret values.
-   The next highest-priority task.
-   Whether the remaining schedule is at risk.

### First task

Implement the repository foundation:

-   React/Vite/TypeScript/Tailwind frontend.
-   FastAPI/Pydantic backend.
-   `.gitignore`.
-   `.env.example`.
-   Mission creation and retrieval.
-   Health endpoint.
-   Responsive FIELD shell.
-   README setup instructions.

Test that the frontend can create a mission and retrieve its status.

Do not start implementing every agent before this milestone passes.

------------------------------------------------------------------------

## 21. Final engineering principles

1.  **Complete the journey:** video to working application.
2.  **Show evidence:** connect the output to what the user recorded and
    said.
3.  **Keep humans in control:** the user approves the requirements
    before code generation.
4.  **Separate creation from verification:** Glimmer independently
    audits the developer's output.
5.  **Execute real software:** a screenshot or generated code block is
    not a running application.
6.  **Protect the public repository:** no credentials or private data in
    Git.
7.  **Be honest:** distinguish live integrations, fallbacks, skipped
    stages, and limitations.
8.  **Protect the deadline:** no new features during the final hour.

**The MVP is successful when a user can provide a real-world business
video, clarify the problem, approve a proposed solution, and receive a
genuinely running application that FIELD has tested and independently
reviewed.**
