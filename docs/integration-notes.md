# Integration notes (verified against official docs, 2026-11-03)

Sources: Meta Model API docs at `dev.meta.ai/docs` (Quickstart, Responses API,
Structured output, Image understanding, Video and audio understanding,
Speech to text, Models, Pricing and rate limits, API references for
`POST /responses`, `POST /asr/transcribe`, Responses schemas) and E2B docs at
`docs.e2b.dev` (quickstart, commands, public URL, billing, Python sync SDK
reference) plus `e2b.dev/pricing`. Third-party claims below were checked
against these and discarded where they disagree.

## 1. Meta Model API — base contract

- Base URL: `https://api.meta.ai/v1`
- Auth: `Authorization: Bearer <key>` header. Docs name the env var
  `MODEL_API_KEY`; this repo uses `META_MODEL_API_KEY` + `META_MODEL_BASE_URL`
  (same values, repo-local names — see mapping in §7).
- OpenAI-SDK compatible: `OpenAI(base_url=..., api_key=...)` works, but the
  SDK does not auto-read `MODEL_API_KEY` — pass the key explicitly.
- Three protocols: Responses (`POST /v1/responses`, recommended), Chat
  Completions, Anthropic Messages. FIELD smoke tests use Responses.
- Catalog: `GET /v1/models` lists API-hosted models.
- Errors: `401 invalid_api_key`, `404 model_not_found`, `400` for bad shapes/
  over-context, `413 payload_too_large`, `429` with `Retry-After` (exponential
  backoff + jitter). Token responses carry `x-ratelimit-*` headers;
  transcription responses do not.

## 2. Muse Spark — exact IDs and parameters

| Item | Verified value |
|---|---|
| Recommended model | `muse-spark-1.3` |
| Older versions | `muse-spark-1.2`, `muse-spark-1.1` (Standard only) |
| Contributor (discounted, trains on your content) | `muse-spark-1.3-contributor`, `muse-spark-1.2-contributor` |
| Context window | 1,048,576 tokens, no long-context premium |
| Max output | 131,072 tokens |
| Input modalities | text, image, video, audio, PDF; output text |
| Responses reasoning | `reasoning: {"effort": "none"\|"minimal"\|"low"\|"medium"\|"high"\|"xhigh"}`. `"none"` returns HTTP 400 on Spark. Omitted = reasons by default. Reasoning tokens bill as output tokens. |
| Output cap | `max_output_tokens`, minimum 16; shares the context budget (`input + max_output` must fit) |
| Reading output | `output[]` items; assistant text is `message` → `content[]` → `{"type": "output_text", "text": ...}`; SDK exposes `response.output_text`; `usage` has `input_tokens` / `output_tokens` (+ `reasoning_tokens`, `cached_tokens` details) |
| Rate limits | 3,000 RPM, 4,000,000 TPM per team (Standard); Contributor 150 RPM / 3M TPM |
| Pricing (Standard / 1M) | input $1.25, output $4.25, cached input $0.15 |
| Pricing (Contributor / 1M) | input $0.10, output $0.20, cached input $0.002 |

Audio caution (Models page + Video/Audio page): **audio understanding in
Muse Spark 1.3 is not fully supported; quality for requests including audio
may be degraded.** For audio use Spark 1.2 or transcribe first with
Muse Voice Transcribe. Spark 1.2 accepts `input_audio` (base64 wav/mp3).

## 3. Structured output on Responses — exact shape

- Parameter is `text.format`, NOT `response_format` (that is Chat Completions
  only; it is ignored on Responses even if accepted for compatibility).
- Shape (from Responses schemas reference, no `json_schema` wrapper):

```json
"text": {
  "format": {
    "type": "json_schema",
    "name": "MySchema",
    "schema": {"type": "object", "properties": {}, "required": []},
    "strict": false
  }
}
```

- `strict` defaults to `false` everywhere; output is always schema-constrained
  regardless — `strict: true` additionally validates the schema against the
  strict subset (plain-object root, `additionalProperties: false` everywhere,
  `required` lists every property; violations → HTTP 400 only when strict).
- Schema limits (HTTP 400 if exceeded): nesting depth 10; 5,000 total
  properties; 120,000 chars total string length; 1,000 enum values;
  200,000 expanded nodes; **recursive schemas rejected**.

## 4. Image input — exact shape and limits

- `input_image` blocks inside a `user` message in the `input` array:

```json
{"type": "message", "role": "user", "content": [
  {"type": "input_text", "text": "What is in this image?"},
  {"type": "input_image", "image_url": "https://... or data:image/jpeg;base64,..."}
]}
```

- `image_url` is a plain string (public URL or base64 `data:` URL), or use
  `file_id` from the Files API.
- Limits: **50 images/request** (enforced, else 400); 50 MB inline per image;
  MIME `jpeg/jpg/png/gif/webp/x-icon`; undecodable bytes → 400 naming the image.
- Cost: ~1,300–1,500 tokens per ~1280px image; exact count via
  `POST /v1/responses/input_tokens` (authoritative, no generation).
- Video: upload via Files API, reference with `input_file` + `file_id`.

## 5. Muse Voice Transcribe — exact contract

- Model ID: `muse-voice-transcribe-1.0`. Two routes, same model:

| Route | Use | Auth |
|---|---|---|
| `wss://api.meta.ai/v1/asr/realtime` | live audio | handshake frame (**`Authorization` header ignored**) |
| `POST {base}/v1/asr/transcribe[?sessionId=]` | a recording you have | `Authorization: Bearer` header |

- File request = `multipart/form-data` with parts `request` (JSON settings,
  content-type `application/json`) and `audio` (the WAV file):

```json
{"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "mode": "ENDPOINTING"}
```

- WAV requirements (strict): **RIFF/WAVE container, mono, 16-bit integer PCM,
  16 kHz or 24 kHz** (24 kHz is the model's native rate). Docs' conversion:

```bash
ffmpeg -i input.m4a -ac 1 -ar 24000 -c:a pcm_s16le -map_metadata -1 output.wav
```

- Modes: `PUSH_TO_TALK` (default, single turn), `ENDPOINTING` (model-found
  turn boundaries), `DIARIZATION` (speaker labels `A`, `B`, …; session-scoped,
  not cross-session identities).
- JSON response: `{sessionId, transcript, audioDurationMs,
  turns: [{turnId, startMs, endMs, transcript, speaker}]}`.
  `turns` is empty in `PUSH_TO_TALK`; `speaker` only in `DIARIZATION`.
- **Turn-level timestamps only.** No word-level timestamps, no confidence
  scores, no sound-event/emotion detection, no reformatting.
- Limits: body > 32 MB → 413; audio > 10 min → 400; bad WAV → 400;
  bad `Accept` → 406; stream-budget exceeded → 500 / terminal `error` event.
  `sessionId` is optional (server generates one) — retain it for support.
- Extras: `languageBias` (25 languages + code-switching), `keywords`
  vocabulary biasing; both set at session start only.
- Pricing: **$0.18/hour of audio**, metered in whole seconds (rounded down),
  streaming and file priced the same. Limits: 256 concurrent streams,
  16,000 streams/hour (realtime + file share one budget).

## 6. E2B — Python SDK contract and pricing

- Install: `pip install e2b` (Python 3.10+). The **core `e2b` package is
  enough for shell commands + files**; `e2b-code-interpreter` is only needed
  for `run_code()`.
- Key: `E2B_API_KEY` (starts with `e2b_`), env var or `Sandbox.create(api_key=...)`.
- Lifecycle: `Sandbox.create(template=None, timeout=<seconds>)` → default
  `base` template; `timeout` kills/pauses at expiry (Hobby max 3,600 s,
  Pro max 86,400 s); `sandbox.kill()`; default `on_timeout` action is `kill`.
- Files: `sandbox.files.write(path, data)` (`str`/`bytes`/file/iterable;
  creates dirs); batch `write_files([...])`.
- Commands: `sandbox.commands.run(cmd, background=False, cwd=None,
  envs=None, timeout=60)` — note the **60 s default command timeout**; pass a
  larger `timeout` (or `0` = unlimited) for `npm install` / builds.
  Background runs return a process with `.kill()`.
- Preview URL: `host = sandbox.get_host(port)` →
  `https://{port}-{sandbox_id}.e2b.app`. **Public by default: anyone with the
  URL can reach sandbox services** — treat it like a credential, restrict via
  `network/restrict-public-access` for anything long-lived, and kill the
  sandbox when done. Public-URL availability of a port requires a server
  actually listening on it.
- Published usage rates: **vCPU $0.000014/vCPU-s, RAM $0.0000045/GiB-s, disk
  free**. Default sandbox = 2 vCPU + 4 GiB → **$0.000046/s** (≈ $0.028 per
  10 min, ≈ $0.166/hour max). New accounts get $100 one-time credits.
  Hobby: 20 concurrent sandboxes, 1 h continuous runtime, 10 GiB disk.
- Base template is CPU-only (no GPUs) with outbound internet by default.

## 7. Repo `.env` mapping

| Repo variable | Docs equivalent | Notes |
|---|---|---|
| `META_MODEL_API_KEY` | `MODEL_API_KEY` | Bearer key, server-side only |
| `META_MODEL_BASE_URL` | `https://api.meta.ai/v1` | overrideable for compat endpoints |
| `MUSE_SPARK_MODEL_ID` | e.g. `muse-spark-1.3` | Standard vs `-contributor` selects training-use terms |
| `MUSE_TRANSCRIBE_MODEL_ID` | `muse-voice-transcribe-1.0` | |
| `E2B_API_KEY` | `E2B_API_KEY` | same name |

## 8. What contradicts or sharpens plan.md

1. **Timestamps are turn-level, not word-level.** Plan §7 wants a
   "timestamped transcript" — achievable via `turns[].startMs/endMs`, but any
   future need for word timing has no API support. Design evidence views
   around turns.
2. **Do not send raw audio to Spark 1.3.** Plan's transcribe-first pipeline
   (§7) is the correct order; direct `input_audio` to 1.3 is documented-
   degraded (1.2 fallback exists). No plan change needed, just do not
   shortcut transcription.
3. **Contributor mode is a product decision.** `muse-spark-1.3-contributor`
   is ~12x cheaper on input / ~21x on output but lets Meta train on sent
   content. The plan is silent on this. Recommendation: Standard mode for
   real business footage; Contributor only for synthetic prototyping.
4. **LiteLLM's "no file transcription" claim is wrong.** Official docs ship
   `POST /v1/asr/transcribe`; trust the official route (verified again in
   the API reference).
5. **E2B preview URLs are public-by-default secrets.** Plan §11's "display
   the preview in FIELD" needs the restrict-public-access control (or short
   sandbox lifetimes) before any real customer data flows through it.
6. **Core `e2b` package suffices** — no `e2b-code-interpreter` dependency
   needed for build/run/serve. Smaller install than the plan's E2B row implies.
7. **sample.mp4 fits transcription caps**: 89.5 s / 7.2 MB → under the 10-min
   and 32 MB limits; converted WAV @24 kHz mono ≈ 4.3 MB. No splitting needed.
8. **5–8 frames fit image caps**: 50 images/request max; ~1.4k tokens/frame
   at 720p — a full evidence set costs a few cents.
9. **Background responses have a 1 MiB body cap** — irrelevant today (smoke
   tests are foreground), but file-backed (`file_id`) inputs are mandatory if
   FIELD ever moves to `background: true` with inline media.
10. **Reasoning bills as output.** Keep `reasoning.effort` low for cheap
    stages (interview, presentation) and raise it only where multi-step
    reasoning pays (planning, code generation).

## 9. Smoke-test cost estimates (all < $0.50 — no approval gate)

| Test | Basis | Upper-bound estimate |
|---|---|---|
| `spark_structured.py` | ≤1k in / ≤2k out @ Standard | ≈ $0.01 |
| `spark_images.py` | 3 frames ≈ 4.5k in / ≤1k out @ Standard | ≈ $0.01 |
| `transcribe.py` | 89 whole seconds @ $0.18/h | ≈ $0.005 |
| `frames.py` | local ffmpeg only | $0.00 |
| `e2b_react.py` | default box @ $0.000046/s, 20-min kill cap | ≤ $0.056, expected ≈ $0.02 |

Estimates assume Standard-mode Spark pricing (upper bound unless the
configured model ID is a `-contributor` variant) and print before each call.

## 10. Provider timeouts, retries, and jobs (verified by keyless tests)

Transport uses split `httpx.Timeout`: connect 10s, pool 10s, write 60s
(120s for WAV uploads). Read budgets per call site:

| Site | Effort / output | Read budget | Constant |
|---|---|---|---|
| Generation + repair | high / 32k | 600s | `DEVELOPER_TIMEOUT_S` |
| Requirements | medium / 16k | 300s | `REQUIREMENTS_TIMEOUT_S` |
| Audit | medium / large evidence | 300s | `AUDIT_TIMEOUT_S` |
| Interview turn | low / short | 180s | `INTERVIEW_TIMEOUT_S` |
| Vision (6 frames) | low / multimodal | 300s | `VISION_TIMEOUT_S` |
| Transcribe | 10MB WAV | 300s read | `TRANSCRIBE_*_S` |

The 180s-previous default caused mission `7a4c82e0021e`'s generation to
fail with `ReadTimeout` at ~181s on a healthy slow run.

Retry policy (`spark_client`, shared by Responses + transcribe):
at most 3 HTTP attempts per logical call; only `SparkTransientError`
(timeouts, dropped peers, 408/429/5xx) retries, with exponential
backoff (2/4/8s), uniform [delay/2, delay] jitter, and `Retry-After`
(seconds or HTTP-date) honored as a floor. Auth (401/403),
bad-request (other 4xx), truncation, and malformed output never retry
at the transport layer.

Error taxonomy (persisted as `error_class` on generation/audit
records, mapped to friendly UI copy): `SparkTransientError`,
`SparkRateLimitError`, `SparkAuthError`, `SparkBadRequestError`.

Jobs: additive SQLite `jobs` table (partial unique index = at most one
live job per mission+operation; second index = one job per
idempotency key). `POST /build` accepts `Idempotency-Key` (replay
returns current state); `POST /build/cancel` stops background builds
at stage boundaries. `BUILD_ASYNC=true` enables the single-threaded
worker (max 1 concurrent build) with per-stage checkpoints and
startup crash recovery. Default off. Server logs carry `spark_call`,
`spark_retry`, `job_claim`, `job_stage`, `job_done`, `worker_recover`
lines (safe fields only — no prompts, outputs, or keys).
