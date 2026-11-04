import { useEffect, useState } from "react";
import InterviewFlow from "./components/InterviewFlow.tsx";
import VerificationResults from "./components/VerificationResults.tsx";
import {
  createDemoMission,
  getHealth,
  verifyMission,
  type HealthStatus,
  type Mission,
} from "./services/api.ts";

type BackendState =
  | { kind: "loading" }
  | { kind: "online"; health: HealthStatus }
  | { kind: "offline"; message: string };

type VerifyState =
  | { kind: "idle" }
  | { kind: "running"; stage: string }
  | { kind: "done"; mission: Mission }
  | { kind: "failed"; message: string };

const WORKFLOW_STAGES = [
  "See: upload a short video of the operation",
  "Understand: frames, audio, and transcript analysis",
  "Interview: answer a few clarifying questions",
  "Plan: review and approve the specification",
  "Build and test: run the generated app in E2B",
  "Verify: deterministic E2B checks plus an independent adversarial Muse Spark audit",
];

export default function App() {
  const [backend, setBackend] = useState<BackendState>({ kind: "loading" });
  const [verify, setVerify] = useState<VerifyState>({ kind: "idle" });

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((health) => setBackend({ kind: "online", health }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          error instanceof Error ? error.message : "Unknown connection error";
        setBackend({ kind: "offline", message });
      });
    return () => controller.abort();
  }, []);

  async function runDemoVerification() {
    setVerify({ kind: "running", stage: "Creating demo mission…" });
    try {
      const mission = await createDemoMission();
      setVerify({ kind: "running", stage: "Building and testing in E2B — this takes a few minutes…" });
      const verified = await verifyMission(mission.id, { use_demo_app: true });
      setVerify({ kind: "done", mission: verified });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "Unknown verification error";
      setVerify({ kind: "failed", message });
    }
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-stone-200">
        <div className="mx-auto max-w-3xl px-4 py-6">
          <h1 className="text-2xl font-bold tracking-tight text-green-900">
            FIELD
          </h1>
          <p className="mt-1 text-base text-stone-600">
            See the world. Understand the problem. Build the solution.
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-8">
        <section aria-label="Backend status" className="border border-stone-200 p-5">
          <h2 className="text-lg font-semibold">Backend status</h2>
          {backend.kind === "loading" && (
            <p className="mt-2 text-stone-600">Checking backend health…</p>
          )}
          {backend.kind === "online" && (
            <p className="mt-2">
              <span className="font-medium text-green-800">Online</span>
              <span className="text-stone-600">
                {" "}
                — {backend.health.service} (env: {backend.health.env})
              </span>
            </p>
          )}
          {backend.kind === "offline" && (
            <div className="mt-2">
              <p className="font-medium text-red-800">Offline</p>
              <p className="mt-1 text-sm text-stone-600">
                Could not reach the backend: {backend.message}. Start it with{" "}
                <code className="bg-stone-100 px-1">python -m app.main</code>{" "}
                from the backend directory.
              </p>
            </div>
          )}
        </section>

        <section aria-label="Interview and requirements" className="mt-8 border border-stone-200 p-5">
          <h2 className="text-lg font-semibold">Interview &amp; requirements</h2>
          <InterviewFlow backendOnline={backend.kind === "online"} />
        </section>

        <section aria-label="Verification demo" className="mt-8 border border-stone-200 p-5">
          <h2 className="text-lg font-semibold">Verification demo</h2>
          <p className="mt-2 text-sm text-stone-600">
            Runs the full VERIFY stage against the reference restaurant
            order-management app: real E2B build, serve, and functional checks,
            then an independent adversarial Muse Spark audit. The reference app
            is a controlled starter, not model output.
          </p>
          {verify.kind === "idle" && (
            <button
              type="button"
              onClick={() => void runDemoVerification()}
              disabled={backend.kind !== "online"}
              className="mt-4 bg-green-900 px-4 py-2 font-medium text-white disabled:opacity-50"
            >
              Run verification
            </button>
          )}
          {verify.kind === "running" && (
            <p className="mt-4 text-stone-700">{verify.stage}</p>
          )}
          {verify.kind === "failed" && (
            <div className="mt-4">
              <p className="font-medium text-red-800">Verification did not complete</p>
              <p className="mt-1 text-sm text-stone-600">{verify.message}</p>
              <button
                type="button"
                onClick={() => setVerify({ kind: "idle" })}
                className="mt-3 border border-stone-300 px-4 py-2 font-medium"
              >
                Try again
              </button>
            </div>
          )}
          {verify.kind === "done" && (
            <div className="mt-2">
              <p className="text-sm text-stone-500">
                Mission {verify.mission.id} · {verify.mission.status}
              </p>
              {verify.mission.verification !== null ? (
                <VerificationResults result={verify.mission.verification} />
              ) : (
                <p className="mt-2 text-stone-600">No verification result recorded.</p>
              )}
              {verify.mission.error !== "" && (
                <p className="mt-2 text-sm text-red-800">{verify.mission.error}</p>
              )}
              <button
                type="button"
                onClick={() => setVerify({ kind: "idle" })}
                className="mt-4 border border-stone-300 px-4 py-2 font-medium"
              >
                Run again
              </button>
            </div>
          )}
        </section>

        <section aria-label="Mission workflow" className="mt-8">
          <h2 className="text-lg font-semibold">Mission workflow</h2>
          <ol className="mt-3 list-decimal space-y-2 pl-6 text-stone-700">
            {WORKFLOW_STAGES.map((stage) => (
              <li key={stage}>{stage}</li>
            ))}
          </ol>
        </section>
      </main>

      <footer className="border-t border-stone-200">
        <div className="mx-auto max-w-3xl px-4 py-4 text-sm text-stone-500">
          FIELD hackathon MVP · frontend talks to the backend only; provider
          keys stay server-side.
        </div>
      </footer>
    </div>
  );
}
