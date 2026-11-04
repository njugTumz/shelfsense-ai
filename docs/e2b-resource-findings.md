# E2B Resource Findings — Browser Verification Memory Pressure

Status: measured findings + mitigations in place. No verification weakened.

## Measured environment

E2B sandbox for FIELD verification (single sandbox hosts the app server,
the browser, and the test driver together):

| Process | Approx. RSS | Notes |
|---|---|---|
| Sandbox total | ~473 MB | Hard ceiling; OOM kills observed |
| Firefox (Playwright) | ~230 MB | Single content process, no memory cache |
| `vite preview` (static build server) | ~78 MB | Serves `npm run build` output on :5173 |
| Test driver + node + overhead | ~60–90 MB | Playwright python, sandbox agent |

Combined steady-state usage is ~438 MB before transient spikes (page
load, screenshots), leaving almost no headroom.

## Observed failure mode

- One run: Firefox OOM-terminated ~90–135 s into the browser workflow.
- Healthy page loads take 13–20 s in-sandbox (Firefox cold start +
  constrained CPU), far slower than local.
- With the old 15 s page timeout, healthy apps were wrongly failed;
  raising the timeout exposed the real constraint (memory, not speed).

## Why the current mitigations are correct

1. **Lean Firefox prefs** (`dom.ipc.processCount=1`,
   `browser.cache.memory.enable=False`) — each extra content process
   costs tens of MB we do not have; the memory cache buys nothing for a
   single-pass workflow. See `browser_workflow.py`.
2. **60 s page timeout** — bisect proved 13–20 s loads are healthy in
   E2B; the timeout now distinguishes slow-infra from broken-app.
3. **Reload → fresh-navigation fallback** (`fresh_load()`) — a wedged
   reload (observed as 60 s stalls / `NS_BINDING_ABORTED`) is recovered
   by navigating to the URL directly instead of failing the mission.
4. **Console + responsive checks are non-gating** — they report honest
   FAIL when executed-and-failing but never flip a verdict on their own,
   so infra flakes in optional checks cannot fail a working app.

## What is NOT the problem

- **Screenshots/fixtures**: JPEG quality-60 screenshots are tens of KB;
  fixture storage is negligible next to the ~460 MB process footprint.
- **The production build**: `vite preview` serves the static `dist/`
  build, not a dev server — HMR/watch overhead is already eliminated in
  both verification (`e2b_verification.py`) and on-demand preview
  (`preview.py`).

## Small safe improvements (not yet implemented)

- Replace `vite preview` (~78 MB node) with a minimal static server
  (`python3 -m http.server`, ~10 MB) for the browser-test phase only.
  Both serve identical bytes; the verdict logic is unaffected. This is
  the deferred "static-build server" optimization: the remaining
  saving is the server process itself, not the build.
- Take the `reloaded` screenshot only on failure (saves a transient
  allocation spike, not steady-state RAM).
- Split browser setup (`BROWSER_SETUP_COMMANDS`) from the workflow run
  so a setup OOM is reported as infra-unavailable rather than
  app-failed — currently handled via `unavailable_browser_checks`, so
  this is already honest; the split would only sharpen the message.

## What we will not do

- Weaken or gate-off browser checks to dodge OOM.
- Shrink the workflow (fewer steps) to fit memory.
- Run the browser outside the sandbox against a local server
  (breaks the isolation guarantee: generated code never executes on
  the FIELD host).
