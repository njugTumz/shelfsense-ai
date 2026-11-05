import { useEffect, useState } from "react";
import {
  MACHINE_ACTIVE_STATUSES,
  TERMINAL_STATUSES,
  type BuildJob,
  type E2BVerification,
  type Mission,
} from "../services/api.ts";

export type StepState = "done" | "active" | "pending" | "failed" | "skipped";

export interface ProgressStep {
  key: string;
  label: string;
  state: StepState;
  detail?: string;
}

export interface ProgressPhase {
  name: string;
  steps: ProgressStep[];
}

const TERMINAL = ["VERIFIED", "VERIFIED_WITH_WARNINGS", "FAILED", "AUDIT_LIMITED", "INCONCLUSIVE"];
const PAST_INTERVIEW = ["REQUIREMENTS_READY", "APPROVED", "BUILDING", "RUNNING", "AUDITING", ...TERMINAL];
const E2B_ORDER = ["e2b_sandbox", "e2b_install", "e2b_build", "e2b_preview", "browser_tests"];
const E2B_CHECK: Record<string, string> = {
  e2b_sandbox: "sandbox_create",
  e2b_install: "npm_install",
  e2b_build: "production_build",
  e2b_preview: "preview_serves",
};

const EXPLAINERS: Record<string, string> = {
  upload: "Sending your video to FIELD for private analysis.",
  frames: "FFmpeg is pulling timestamped frames from the video.",
  audio: "FFmpeg is extracting the narration audio track.",
  transcribe: "Muse Voice Transcribe is turning narration into text.",
  visual: "Muse Spark is reading the frames for visible evidence.",
  synthesize: "Combining frames, transcript, and statements into labeled evidence.",
  questions: "Muse Spark is drafting a grounded follow-up question.",
  answers: "Waiting for your answer — it becomes STATED evidence.",
  requirements: "Muse Spark is drafting the specification from the evidence.",
  criteria: "Testable acceptance criteria ship with the specification.",
  approval: "Waiting for your review — nothing builds before you approve.",
  generate: "Muse Spark is writing the application files from your approved spec.",
  validate: "FIELD validates every path, size, and byte before E2B sees it.",
  e2b_sandbox: "Starting an isolated sandbox for the generated app.",
  e2b_install: "Installing the locked dependencies inside the sandbox.",
  e2b_build: "Compiling the production React build.",
  e2b_preview: "Serving the app and confirming it responds.",
  browser_tests: "A real Firefox is clicking through the order workflow.",
  auditing: "An independent Spark session is reviewing requirements, code, and test evidence.",
  verdict: "Combining execution evidence with the audit into a final verdict.",
};

function e2bStepState(
  view: { status: string; progressStep: string; e2b: E2BVerification | null },
  key: string,
): { state: StepState; detail?: string } {
  if (view.e2b !== null) {
    const check = view.e2b.checks.find((c) => c.name === E2B_CHECK[key]);
    if (check !== undefined && check.status === "fail") {
      return { state: "failed", detail: check.detail.slice(0, 200) };
    }
    return { state: "done" };
  }
  const order = E2B_ORDER.indexOf(view.progressStep);
  const mine = E2B_ORDER.indexOf(key);
  if (view.progressStep === key) return { state: "active" };
  if (order > mine) return { state: "done" };
  if (view.status === "RUNNING" && view.progressStep === "" && key === "e2b_sandbox") {
    return { state: "active" };
  }
  return { state: "pending" };
}

export function derivePhases(mission: Mission | null, inFlight: string | null): ProgressPhase[] {
  const status = mission?.status ?? "NONE";
  const media = mission?.media ?? null;
  const turns = mission?.interview.turns ?? [];
  const pending = turns.find((t) => t.answer.trim() === "") ?? null;
  const spec = mission?.spec ?? null;
  const generation = mission?.generation ?? null;
  const generated = generation?.status === "generated";
  const genFailed = generation?.status === "failed";
  const verification = mission?.verification ?? null;
  const audit = verification?.audit ?? null;
  const browser = verification?.e2b?.browser ?? null;
  const mediaFailed = media?.status === "failed";

  const seeFailedKey =
    mediaFailed && media !== null
      ? media.frames.length === 0
        ? "frames"
        : media.transcript === null || media.transcript.text === ""
          ? "transcribe"
          : media.observations.length === 0
            ? "visual"
            : "upload"
      : null;

  const seeState = (key: string, done: boolean, active: boolean): StepState => {
    if (media === null) {
      if (mission === null || status === "CREATED") {
        return key === "upload" && inFlight === "upload" ? "active" : "pending";
      }
      return "skipped";
    }
    if (done) return "done";
    if (seeFailedKey === key) return "failed";
    if (active) return "active";
    return "pending";
  };
  const seeDetail = (key: string): string | undefined =>
    seeFailedKey === key && media !== null ? media.error.slice(0, 200) : undefined;

  const audioDone =
    media !== null && ["transcribing", "analyzing", "ready"].includes(media.status);
  const pastInterview = PAST_INTERVIEW.includes(status);
  const answersDone =
    (turns.length > 0 && pending === null) || (pastInterview && turns.length > 0);
  const understandSkipped =
    mission !== null && turns.length === 0 && mission.evidence.length === 0 && pastInterview;
  const planSkipped =
    mission !== null &&
    spec === null &&
    ["BUILDING", "RUNNING", "AUDITING", ...TERMINAL].includes(status);
  const generateSkipped =
    mission !== null &&
    generation === null &&
    ["RUNNING", "AUDITING", ...TERMINAL].includes(status);

  const browserFailed =
    verification?.e2b.checks.some((c) => c.name.startsWith("browser_") && c.status === "fail") ??
    false;
  const e2bView = {
    status,
    progressStep: mission?.progress_step ?? "",
    e2b: verification?.e2b ?? null,
  };

  return [
    {
      name: "See",
      steps: [
        {
          key: "upload",
          label: "Upload video",
          state: seeState("upload", media !== null, inFlight === "upload"),
          detail: seeDetail("upload"),
        },
        {
          key: "frames",
          label: "Extract frames",
          state: seeState(
            "frames",
            media !== null && media.frames.length > 0,
            media?.status === "extracting" && (media?.frames.length ?? 0) === 0,
          ),
          detail: seeDetail("frames"),
        },
        {
          key: "audio",
          label: "Extract audio",
          state: seeState(
            "audio",
            audioDone,
            media?.status === "extracting" && (media?.frames.length ?? 0) > 0,
          ),
          detail: seeDetail("audio"),
        },
        {
          key: "transcribe",
          label: "Transcribe narration",
          state: seeState(
            "transcribe",
            (media?.transcript?.text ?? "") !== "",
            media?.status === "transcribing",
          ),
          detail: seeDetail("transcribe"),
        },
        {
          key: "visual",
          label: "Analyze visual evidence",
          state: seeState(
            "visual",
            (media?.observations.length ?? 0) > 0,
            media?.status === "analyzing",
          ),
          detail: seeDetail("visual"),
        },
      ],
    },
    {
      name: "Understand",
      steps: [
        {
          key: "synthesize",
          label: "Synthesize evidence",
          state: understandSkipped
            ? "skipped"
            : (mission?.evidence.length ?? 0) > 0
              ? "done"
              : inFlight === "analyze" || status === "ANALYZING"
                ? "active"
                : "pending",
        },
        {
          key: "questions",
          label: "Prepare interview questions",
          state: understandSkipped
            ? "skipped"
            : turns.length > 0
              ? "done"
              : inFlight === "interview" || inFlight === "answer"
                ? "active"
                : "pending",
        },
        {
          key: "answers",
          label: "Wait for your answers",
          state: understandSkipped
            ? "skipped"
            : answersDone
              ? "done"
              : pending !== null && inFlight === null
                ? "active"
                : "pending",
        },
      ],
    },
    {
      name: "Plan",
      steps: [
        {
          key: "requirements",
          label: "Generate requirements",
          state: planSkipped
            ? "skipped"
            : spec !== null
              ? "done"
              : inFlight === "requirements"
                ? "active"
                : "pending",
        },
        {
          key: "criteria",
          label: "Prepare acceptance criteria",
          state:
            planSkipped ? "skipped" : spec !== null && spec.acceptance_criteria.length > 0 ? "done" : "pending",
        },
        {
          key: "approval",
          label: "Wait for your approval",
          state: planSkipped
            ? "skipped"
            : mission?.is_approved === true
              ? "done"
              : status === "REQUIREMENTS_READY" && inFlight === null
                ? "active"
                : "pending",
        },
      ],
    },
    {
      name: "Build",
      steps: [
        {
          key: "generate",
          label: "Generate application files",
          state: generateSkipped
            ? "skipped"
            : generated
              ? "done"
              : genFailed
                ? "failed"
                : status === "BUILDING" || inFlight === "build"
                  ? "active"
                  : "pending",
          detail: genFailed ? (generation?.error.slice(0, 200) ?? "") : undefined,
        },
        {
          key: "validate",
          label: "Validate the manifest",
          state: generateSkipped ? "skipped" : generated ? "done" : genFailed ? "failed" : "pending",
        },
        {
          key: "e2b_sandbox",
          label: "Prepare the E2B sandbox",
          ...e2bStepState(e2bView, "e2b_sandbox"),
        },
        {
          key: "e2b_install",
          label: "Install dependencies",
          ...e2bStepState(e2bView, "e2b_install"),
        },
        {
          key: "e2b_build",
          label: "Build the React application",
          ...e2bStepState(e2bView, "e2b_build"),
        },
        {
          key: "e2b_preview",
          label: "Start the preview",
          ...e2bStepState(e2bView, "e2b_preview"),
        },
      ],
    },
    {
      name: "Test",
      steps: [
        {
          key: "browser_tests",
          label: "Run browser tests",
          state:
            browser !== null
              ? browserFailed
                ? "failed"
                : "done"
              : mission?.progress_step === "browser_tests"
                ? "active"
                : "pending",
          detail:
            browser !== null
              ? `${browser.steps.filter((s) => s.status === "pass").length}/${browser.steps.length} steps passed`
              : undefined,
        },
        {
          key: "auditing",
          label: "Independent Muse Spark audit",
          state:
            (audit?.completed ?? false)
              ? audit?.verdict === "FAIL"
                ? "failed"
                : "done"
              : status === "AUDITING" || mission?.progress_step === "auditing"
                ? "active"
                : "pending",
        },
        {
          key: "verdict",
          label: "Final verification verdict",
          state: TERMINAL.includes(status)
            ? status === "FAILED"
              ? "failed"
              : "done"
            : "pending",
          detail: TERMINAL.includes(status) ? status : undefined,
        },
      ],
    },
  ];
}

function StepGlyph({ state }: { state: StepState }) {
  if (state === "done") {
    return (
      <span aria-hidden="true" className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-moss-100 text-sm font-bold text-moss-800">
        ✓
      </span>
    );
  }
  if (state === "active") {
    return (
      <span
        aria-hidden="true"
        role="progressbar"
        aria-label="in progress"
        className="field-spinner inline-block h-4 w-4 rounded-full border-2 border-amber-300 border-t-amber-700"
      />
    );
  }
  if (state === "failed") {
    return (
      <span aria-hidden="true" className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-red-100 text-sm font-bold text-red-800">
        ✕
      </span>
    );
  }
  return (
    <span aria-hidden="true" className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-stone-300 text-xs text-stone-400">
      ○
    </span>
  );
}

//#region Tier 1 live progress
// Phase weights for the overall estimate, roughly proportional to measured
// machine time in stored runs (generation median ~3min, E2B verify ~2-7min
// typical, up to ~11min with the lifecycle walker). Media processing and
// audit have no stored durations, so their weights are small placeholders.
// Interview/approval waits are user time and excluded. Skipped steps do not
// count toward the denominator.
const PHASE_WEIGHTS: Record<string, number> = {
  See: 5,
  Understand: 10,
  Plan: 5,
  Build: 45,
  Test: 35,
};

const STALE_HEARTBEAT_S = 600;

function useNow(tickMs: number, active: boolean): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(new Date()), tickMs);
    return () => window.clearInterval(timer);
  }, [tickMs, active]);
  return now;
}

function parseTime(iso: string): number | null {
  if (iso === "") return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

function formatClock(iso: string): string | null {
  const ms = parseTime(iso);
  if (ms === null) return null;
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Real completion timestamps only; steps without backend evidence show none. */
function finishedAt(mission: Mission, stepKey: string): string {
  const checks = mission.verification?.e2b.checks ?? [];
  switch (stepKey) {
    case "upload":
      return mission.media?.created_at ?? "";
    case "approval":
      return mission.approved_at;
    case "generate":
    case "validate":
      return mission.generation?.completed_at ?? "";
    case "verdict":
      return mission.verification?.completed_at ?? "";
    default: {
      const checkName = E2B_CHECK[stepKey];
      if (checkName === undefined) return "";
      return checks.find((c) => c.name === checkName)?.completed_at ?? "";
    }
  }
}

function estimatePercent(phases: ProgressPhase[]): number | null {
  let scored = 0;
  let total = 0;
  for (const phase of phases) {
    const weight = PHASE_WEIGHTS[phase.name] ?? 0;
    const counted = phase.steps.filter((s) => s.state !== "skipped");
    if (counted.length === 0) continue;
    const perStep = weight / phase.steps.length;
    for (const step of counted) {
      total += perStep;
      if (step.state === "done" || step.state === "failed") scored += perStep;
      else if (step.state === "active") scored += perStep / 2;
    }
  }
  if (total === 0) return null;
  return Math.round((100 * scored) / total);
}
//#endregion Tier 1 live progress

export default function WorkflowProgress({
  mission,
  inFlight,
  job,
  lastUpdated,
  reconnecting,
}: {
  mission: Mission | null;
  inFlight: string | null;
  job: BuildJob | null;
  lastUpdated: Date | null;
  reconnecting: boolean;
}) {
  const phases = derivePhases(mission, inFlight);
  const flatSteps = phases.flatMap((p) => p.steps);
  const activeStep = flatSteps.find((s) => s.state === "active");
  const status = mission?.status ?? "";
  const terminal = TERMINAL_STATUSES.includes(status);
  const machineActive = MACHINE_ACTIVE_STATUSES.includes(status);
  const now = useNow(1000, machineActive || reconnecting);

  const finished = flatSteps.filter(
    (s) => s.state === "done" || s.state === "failed" || s.state === "skipped",
  ).length;
  const stepPosition = activeStep !== undefined ? Math.min(finished + 1, flatSteps.length) : finished;
  const percent = mission === null ? null : estimatePercent(phases);

  const createdMs = mission === null ? null : parseTime(mission.created_at);
  const endMs = terminal && mission !== null ? (parseTime(mission.updated_at) ?? Date.now()) : now.getTime();
  const elapsedS = createdMs === null ? null : Math.max(0, (endMs - createdMs) / 1000);

  const heartbeatMs = job === null ? null : parseTime(job.heartbeat_at);
  const heartbeatAgeS =
    heartbeatMs === null ? null : Math.max(0, (now.getTime() - heartbeatMs) / 1000);
  const workerStale =
    job !== null && heartbeatAgeS !== null && heartbeatAgeS >= STALE_HEARTBEAT_S;

  return (
    <section aria-label="Workflow progress" className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm sm:p-5">
      <h3 className="text-base font-semibold text-stone-900">
        Where your mission stands{percent !== null ? ` — ≈${percent}% (estimate)` : ""}
      </h3>
      {activeStep !== undefined ? (
        <p className="mt-1 text-sm text-stone-700">
          <span className="font-medium text-amber-800">Working on:</span> {activeStep.label}…
        </p>
      ) : (
        <p className="mt-1 text-sm text-stone-500">
          {mission === null
            ? "Start with a video or a written brief — progress appears here."
            : "Waiting — nothing is running right now."}
        </p>
      )}
      {mission !== null && (
        <p className="mt-1 text-xs text-stone-500">
          Step {stepPosition} of {flatSteps.length}
          {elapsedS !== null && <> · elapsed {formatDuration(elapsedS)}</>}
          <> · automated stages typically 4–15 min (generation 1.5–6, verify 2–11)</>
        </p>
      )}
      {(machineActive || reconnecting) && (
        <p className="mt-1 text-xs text-stone-500">
          {reconnecting ? (
            <span className="font-medium text-amber-800">
              Reconnecting…{lastUpdated !== null ? ` last update ${formatDuration((now.getTime() - lastUpdated.getTime()) / 1000)} ago` : ""}
            </span>
          ) : lastUpdated !== null ? (
            <>Updated {formatDuration((now.getTime() - lastUpdated.getTime()) / 1000)} ago</>
          ) : (
            <>Starting live updates…</>
          )}
        </p>
      )}
      {job !== null && (
        <p className="mt-1 text-xs text-stone-500">
          {workerStale ? (
            <span className="font-medium text-red-800">
              Worker silent for {formatDuration(heartbeatAgeS ?? 0)} (stale past {STALE_HEARTBEAT_S}s) —
              the build may be stuck; it re-queues when the backend restarts.
            </span>
          ) : job.status === "queued" || heartbeatAgeS === null ? (
            <>Worker: queued — picks up shortly…</>
          ) : (
            <>Worker {job.status} · heartbeat {formatDuration(heartbeatAgeS)} ago</>
          )}
        </p>
      )}
      <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {phases.map((phase) => (
          <div key={phase.name}>
            <h4 className="text-sm font-semibold tracking-wide text-stone-500 uppercase">
              {phase.name}
            </h4>
            <ul className="mt-2 space-y-2">
              {phase.steps.map((step) => (
                <li key={step.key} className="flex items-start gap-2 text-sm">
                  <span className="mt-0.5 shrink-0">
                    <StepGlyph state={step.state} />
                  </span>
                  <span>
                    <span
                      className={
                        step.state === "done"
                          ? "text-stone-700"
                          : step.state === "active"
                            ? "font-medium text-stone-900"
                            : step.state === "failed"
                              ? "font-medium text-red-800"
                              : "text-stone-400"
                      }
                    >
                      {step.label}
                      {mission !== null &&
                        (step.state === "done" || step.state === "failed") &&
                        formatClock(finishedAt(mission, step.key)) !== null && (
                          <span className="text-xs text-stone-400">
                            {" · "}
                            {formatClock(finishedAt(mission, step.key))}
                          </span>
                        )}
                    </span>
                    {step.state === "skipped" && (
                      <span className="block text-xs text-stone-400">
                        skipped{phase.name === "See" ? " — no video" : " — not part of this run"}
                      </span>
                    )}
                    {step.state === "active" && EXPLAINERS[step.key] !== undefined && (
                      <span className="block text-xs text-stone-500">{EXPLAINERS[step.key]}</span>
                    )}
                    {step.detail !== undefined && step.detail !== "" && (
                      <span className="block text-xs text-stone-500">{step.detail}</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
