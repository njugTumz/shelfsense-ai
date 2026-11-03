import { useEffect, useState } from "react";
import { getHealth, type HealthStatus } from "./services/api.ts";

type BackendState =
  | { kind: "loading" }
  | { kind: "online"; health: HealthStatus }
  | { kind: "offline"; message: string };

const WORKFLOW_STAGES = [
  "See: upload a short video of the operation",
  "Understand: frames, audio, and transcript analysis",
  "Interview: answer a few clarifying questions",
  "Plan: review and approve the specification",
  "Build and test: run the generated app in E2B",
  "Audit: independent Muse Glimmer review",
];

export default function App() {
  const [backend, setBackend] = useState<BackendState>({ kind: "loading" });

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

        <section aria-label="Mission workflow" className="mt-8">
          <h2 className="text-lg font-semibold">Mission workflow</h2>
          <ol className="mt-3 list-decimal space-y-2 pl-6 text-stone-700">
            {WORKFLOW_STAGES.map((stage) => (
              <li key={stage}>{stage}</li>
            ))}
          </ol>
          <p className="mt-4 text-sm text-stone-600">
            Mission creation arrives with the first MVP feature. This shell
            verifies the frontend and backend run and communicate.
          </p>
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
