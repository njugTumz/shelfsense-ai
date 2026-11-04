# FIELD — Hackathon Submission Text

## Project description

FIELD turns real-world business videos into working business software.
A business owner uploads a video of their operation; FIELD extracts
timestamped visual evidence, transcribes narration, asks grounded
follow-up questions, produces an approved requirements specification,
generates a professional React application, runs it in an isolated E2B
sandbox, drives it with a real browser, audits it with an independent
AI reviewer, and reports an honest verdict with evidence.

Pipeline: SEE → UNDERSTAND → INTERVIEW → PLAN → BUILD → TEST → AUDIT → PRESENT.

Live demo: https://field-demo-k7q2b.southafricanorth.azurecontainer.io
Repo: https://github.com/njugTumz/shelfsense-ai

## Meta products used (verified in the demo path)

- **Muse Spark 1.3** (`muse-spark-1.3`): multimodal frame analysis,
  evidence synthesis, interview questions, requirements generation,
  constrained application generation, and the independent adversarial
  audit. Every AI step in the user-facing workflow.
- **Muse Voice Transcribe 1.0**: business narration transcription
  (STATED evidence with turn timestamps).
- **Muse Code**: coding assistant used during development (not part of
  the user-facing workflow).
- **Muse Glimmer**: NOT used — integration preserved, but Modal
  GPU/account limits prevented genuine inference (see feedback below).
  The verified audit path is adversarial Spark + deterministic E2B.

## Why Meta models matter here

FIELD's core loop is multimodal understanding plus constrained
generation: Spark reads the same frames the owner sees, interviews
from that evidence (not generic questions), writes the spec, writes
the app within a strict scaffold contract, then a SEPARATE Spark
session audits the result against the evidence. Voice Transcribe
grounds owner statements with timestamps. Without strong
multimodal + structured-output models, the evidence chain collapses
into chat; with them, every claim stays traceable to video.

## Modal feedback (actual experience)

- Better quota/cost visibility: the account limit arrived without a
  clear remaining-budget signal during the spike.
- Clearer readiness feedback: vLLM startup vs checkpoint-load vs
  servable states were hard to distinguish from polling.
- Improved lifecycle controls: duplicate H100 containers suggested
  our polling raced container startup; single-flight guards would help.
- More reliable health-check behavior: no `/health 200` observed
  before the limit stopped the attempt.
- No Glimmer inference completed; GPU usage was halted at that point.

## Demo mission (judges can reproduce the flow)

Mission `b6669ee31e0a` ("Demo bakery mission", Fallow kitchen video):
video → 13 evidence items → 3-question interview → 12-FR spec →
approved → generated kitchen-display app → E2B build → real Firefox
workflow (load/create/advance/persist PASS, console clean, 390px
responsive PASS) → Spark audit PASS_WITH_WARNINGS → verdict
VERIFIED_WITH_WARNINGS. Generated preview available on demand
(~55-min E2B TTL, relaunchable from the mission page).
