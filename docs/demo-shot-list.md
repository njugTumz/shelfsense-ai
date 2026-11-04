# FIELD Demo Video — Shot List & Recording Checklist

Target: ~3 minutes. Production URL: https://field-demo-k7q2b.southafricanorth.azurecontainer.io
Demo mission: `b6669ee31e0a` (Fallow kitchen ticket video → kitchen display app, VERIFIED_WITH_WARNINGS).
Tagline: "See the world. Understand the problem. Build the solution."

## Before recording

- [ ] Production healthy: homepage 200, `/health` ok (do NOT redeploy — TLS budget).
- [ ] Mission `b6669ee31e0a` preview ACTIVE: relaunch via the UI if expired
      (previews last ~55 min; note the expiry shown in the UI).
- [ ] Preview URL opens the "FALLOW · KITCHEN PASS" board without login.
- [ ] Browser: fresh profile window, 1280x720, only FIELD tabs open.
- [ ] OBS: capture browser window, 1080p30; mic levels checked in Audacity.
- [ ] No secrets visible: no terminal with keys, no `.env`, no Azure portal tabs.
- [ ] Backup screenshots saved (in case a preview expires mid-session):
      created/advanced/reloaded/mobile verification screenshots.

## Shot list

### 0:00–0:20 — Problem + intro
- FIELD landing page. Voiceover: small businesses struggle to explain
  operations in requirements documents; FIELD lets them show the work.
- Show the mission video tile (kitchen footage) + Upload state.

### 0:20–0:50 — SEE
- Mission evidence panel: 6 timestamped frames (paper tickets, printers).
- Transcript turns (ticket system explanation).
- OBSERVED / STATED / INFERRED labels. Mention Muse Spark vision +
  Muse Voice Transcribe by name.

### 0:50–1:15 — UNDERSTAND
- One interview question (station taps vs lead-pass control) + owner answer.
- Requirements spec: problem, 12 FRs, acceptance criteria. Click APPROVE.

### 1:15–1:45 — BUILD
- Build progress (current stage label). Cut waiting time in edit.
- "Generated 8 files" + E2B build success line. No terminal footage needed.

### 1:45–2:30 — THE WORKING APP (most important)
- Live preview, full-window: FALLOW board, station tabs, ticket cards.
- Type a new ticket ("Table 9 – Corn Ribs"), submit, watch it appear.
- Mark an item Fired, then Ready (gate feedback visible).
- Reload the page: ticket + status persist.
- Switch Lead Pass → Hot Ladder → Waiter tabs briefly.

### 2:30–2:50 — VERIFY
- Browser checks list: load/create/advance/persist PASS, console clean,
  390px responsive PASS, invalid-transition FAIL shown as non-gating.
- Muse Spark audit verdict PASS_WITH_WARNINGS + 1–2 finding headlines.
- Final mission status VERIFIED_WITH_WARNINGS. Say plainly: warnings are
  KDS flows the generic checks don't cover; nothing is hidden.

### 2:50–3:00 — CLOSE
- FIELD branding + tagline. End card: repo URL + live demo URL.

## Edit notes (DaVinci Resolve)

- Cut all waiting (build/verify runs); use crossfades, never fake progress.
- Every screenshot/verdict shown must be the real mission output.
- Never claim Glimmer: audit is "independent Muse Spark audit".
- State preview expiry honestly if the URL is shown ("temporary preview").
- Loudness-check narration in Audacity before export.
