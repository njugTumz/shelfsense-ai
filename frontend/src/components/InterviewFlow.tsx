import { useState, type ReactNode } from "react";
import VerificationResults from "./VerificationResults.tsx";
import WorkflowProgress from "./WorkflowProgress.tsx";
import { useMissionPolling } from "../hooks/useMissionPolling.ts";
import {
  TERMINAL_STATUSES,
  analyzeMission,
  answerQuestion,
  approveRequirements,
  buildApplication,
  cancelBuild,
  createMission,
  frameUrl,
  generateRequirements,
  getMission,
  launchPreview,
  repairApplication,
  requestChanges,
  startInterview,
  uploadVideo,
  type EvidenceItem,
  type GenerationRecord,
  type MediaRecord,
  type Mission,
  type MissionPreview,
  type RepairRecord,
  type StructuredRequirements,
} from "../services/api.ts";

function EvidencePanel({ items }: { items: EvidenceItem[] }) {
  if (items.length === 0) return null;
  const hasDemo = items.some((item) => item.source.includes("demo fixture"));
  return (
    <div className="mt-4 border border-stone-200 bg-white p-4 rounded-lg">
      <h4 className="font-semibold">What FIELD has understood</h4>
      {hasDemo && (
        <p className="mt-1 text-sm text-stone-500">
          Includes demo sample observations — the video pipeline is not connected yet.
        </p>
      )}
      <ul className="mt-2 space-y-1">
        {items.map((item, index) => (
          <li key={`${item.category}-${index}`} className="text-sm text-stone-700">
            <span className="font-semibold">{item.category}</span>: {item.text}{" "}
            <span className="text-stone-500">({item.source})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SpecSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-3">
      <h4 className="font-semibold">{title}</h4>
      <div className="mt-1 text-sm text-stone-700">{children}</div>
    </div>
  );
}

function SpecReview({ spec }: { spec: StructuredRequirements }) {
  return (
    <div className="mt-2">
      <p className="text-sm text-stone-500">Revision {spec.revision}</p>
      <SpecSection title="Problem">
        <p>{spec.problem_statement}</p>
      </SpecSection>
      {spec.business_context !== "" && (
        <SpecSection title="Business context">
          <p>{spec.business_context}</p>
        </SpecSection>
      )}
      {spec.objectives.length > 0 && (
        <SpecSection title="Objectives">
          <ul className="list-disc space-y-1 pl-5">
            {spec.objectives.map((objective) => (
              <li key={objective}>{objective}</li>
            ))}
          </ul>
        </SpecSection>
      )}
      <SpecSection title="User roles">
        <ul className="list-disc space-y-1 pl-5">
          {spec.user_roles.map((role) => (
            <li key={role.name}>
              <span className="font-medium">{role.name}</span>
              {role.description !== "" ? ` — ${role.description}` : ""}
            </li>
          ))}
        </ul>
      </SpecSection>
      <SpecSection title="Functional requirements">
        <ul className="list-disc space-y-1 pl-5">
          {spec.functional_requirements.map((req) => (
            <li key={req.id}>
              <span className="font-medium">{req.id}</span> — {req.requirement}
            </li>
          ))}
        </ul>
      </SpecSection>
      {spec.core_features.length > 0 && (
        <SpecSection title="Core features">
          <ul className="list-disc space-y-1 pl-5">
            {spec.core_features.map((feature) => (
              <li key={feature}>{feature}</li>
            ))}
          </ul>
        </SpecSection>
      )}
      {spec.user_journeys.length > 0 && (
        <SpecSection title="User journeys">
          {spec.user_journeys.map((journey) => (
            <div key={journey.name} className="mt-1">
              <p className="font-medium">{journey.name}</p>
              <ol className="list-decimal space-y-1 pl-5">
                {journey.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </div>
          ))}
        </SpecSection>
      )}
      {spec.data_entities.length > 0 && (
        <SpecSection title="Data entities">
          <ul className="list-disc space-y-1 pl-5">
            {spec.data_entities.map((entity) => (
              <li key={entity.name}>
                <span className="font-medium">{entity.name}</span>: {entity.fields.join(", ")}
              </li>
            ))}
          </ul>
        </SpecSection>
      )}
      <SpecSection title="Acceptance criteria">
        <ul className="list-disc space-y-1 pl-5">
          {spec.acceptance_criteria.map((criterion) => (
            <li key={criterion}>{criterion}</li>
          ))}
        </ul>
      </SpecSection>
      {spec.assumptions.length > 0 && (
        <SpecSection title="Assumptions (inferred — need confirmation)">
          <ul className="list-disc space-y-1 pl-5">
            {spec.assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>
        </SpecSection>
      )}
      {spec.unresolved_questions.length > 0 && (
        <SpecSection title="Unresolved questions">
          <ul className="list-disc space-y-1 pl-5">
            {spec.unresolved_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </SpecSection>
      )}
    </div>
  );
}

function friendlyProviderError(errorClass: string): string | null {
  if (errorClass === "SparkTransientError" || errorClass === "SparkRateLimitError") {
    return "The AI service was slow or busy. Nothing was half-built — retrying is safe.";
  }
  if (errorClass === "SparkAuthError" || errorClass === "SparkUnavailableError") {
    return "The AI service sign-in failed. Please contact support instead of retrying.";
  }
  if (errorClass === "SparkBadRequestError" || errorClass === "GenerationValidationError") {
    return "The AI service returned an unusable response. A retry usually fixes this.";
  }
  return null;
}

function GenerationPanel({ generation }: { generation: GenerationRecord }) {
  if (generation.status === "generating") {
    return <p className="mt-2 text-stone-600">Generation in progress…</p>;
  }
  if (generation.status === "failed") {
    const friendly = friendlyProviderError(generation.error_class ?? "");
    return (
      <div className="mt-2">
        {friendly !== null && <p className="text-sm text-stone-700">{friendly}</p>}
        <p className="mt-1 text-sm text-red-800">{generation.error}</p>
      </div>
    );
  }
  return (
    <div className="mt-2">
      <p className="text-stone-700">{generation.summary}</p>
      <p className="mt-2 text-sm text-stone-600">
        {generation.manifest.length} file(s): {generation.manifest.join(", ")}
        {generation.model_id !== "" ? ` · model ${generation.model_id}` : ""}
      </p>
      {generation.feature_map.length > 0 && (
        <ul className="mt-2 space-y-1">
          {generation.feature_map.map((item, index) => (
            <li key={`${item.feature.slice(0, 40)}-${index}`} className="text-sm text-stone-700">
              <span className="font-medium">{item.feature}</span>{" "}
              <span className="text-stone-500">→ {item.requirement_ids.join(", ") || "no FR mapped"}</span>
            </li>
          ))}
        </ul>
      )}
      {generation.unimplemented.length > 0 && (
        <div className="mt-2">
          <p className="text-sm font-semibold text-stone-600">Not implemented by the generator</p>
          <ul className="mt-1 space-y-1">
            {generation.unimplemented.map((item) => (
              <li key={item.requirement_id} className="text-sm text-stone-700">
                <span className="font-medium">{item.requirement_id}</span>
                {item.reason !== "" ? ` — ${item.reason}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const TERMINAL_BUILD_STATES = TERMINAL_STATUSES;

function isPreviewActive(preview: MissionPreview | null | undefined): boolean {
  if (preview == null || preview.preview_url === "") return false;
  const expires = Date.parse(preview.expires_at);
  return !Number.isNaN(expires) && expires > Date.now();
}

function PreviewPanel({
  mission,
  busy,
  onLaunch,
}: {
  mission: Mission;
  busy: string | null;
  onLaunch: () => void;
}) {
  const hasFiles = mission.files !== null && Object.keys(mission.files).length > 0;
  if (!hasFiles) return null;
  const preview = mission.preview ?? null;
  const active = isPreviewActive(preview);
  return (
    <div className="mt-3">
      <h3 className="text-lg font-semibold">Open the application</h3>
      {active && preview !== null ? (
        <>
          <a
            href={preview.preview_url}
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-block bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700"
          >
            Open generated application
          </a>
          <p className="mt-1 text-sm text-stone-500">
            Live until {new Date(preview.expires_at).toLocaleString()} — a temporary
            preview sandbox, not permanent hosting. The link stops responding at expiry.
          </p>
        </>
      ) : (
        <>
          <button
            type="button"
            onClick={onLaunch}
            disabled={busy !== null}
            className="mt-2 bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700 disabled:opacity-50 disabled:hover:bg-clay-600"
          >
            {preview !== null && (preview.error !== "" || preview.preview_url !== "")
              ? "Relaunch preview"
              : "Launch preview"}
          </button>
          {preview !== null && preview.error !== "" && (
            <p className="mt-1 text-sm text-red-800">
              Last preview failed: {preview.error.slice(0, 300)}
            </p>
          )}
          {preview !== null && preview.error === "" && preview.preview_url !== "" && (
            <p className="mt-1 text-sm text-stone-500">
              Previous preview expired — relaunching starts a fresh temporary sandbox.
            </p>
          )}
          <p className="mt-1 text-sm text-stone-500">
            Serves this mission&apos;s stored generated files in a temporary sandbox for
            about an hour. No rebuild, no AI calls.
          </p>
        </>
      )}
    </div>
  );
}

function RepairPanel({ repair }: { repair: RepairRecord }) {
  if (repair.status === "repairing") {
    return <p className="mt-2 text-stone-600">Repair in progress…</p>;
  }
  if (repair.status === "failed") {
    return (
      <div className="mt-2">
        <p className="text-sm text-red-800">
          Repair did not produce testable code and can be retried: {repair.error.slice(0, 300)}
        </p>
      </div>
    );
  }
  return (
    <div className="mt-2">
      <p className="text-stone-700">
        One repair-and-retest cycle ran for {repair.trigger !== "" ? repair.trigger : "the failure"}:{" "}
        {repair.previous_status} → {repair.new_status !== "" ? repair.new_status : "retested"}.
      </p>
      {repair.summary !== "" && <p className="mt-1 text-sm text-stone-600">{repair.summary}</p>}
      {repair.manifest.length > 0 && (
        <p className="mt-1 text-sm text-stone-600">
          Repaired files: {repair.manifest.join(", ")}
          {repair.model_id !== "" ? ` · model ${repair.model_id}` : ""}
        </p>
      )}
      {repair.new_status === "FAILED" && (
        <p className="mt-1 text-sm text-red-800">
          The retest still failed — no further automatic repair is attempted.
        </p>
      )}
    </div>
  );
}

const MEDIA_STAGE_LABELS: Record<string, string> = {
  uploaded: "Uploaded — waiting to analyze",
  extracting: "Extracting frames and audio",
  transcribing: "Transcribing narration",
  analyzing: "Analyzing visual evidence",
  ready: "Evidence ready",
  failed: "Analysis failed",
};

function EvidencePreview({ missionId, media }: { missionId: string; media: MediaRecord }) {
  return (
    <div className="mt-3 border border-stone-200 bg-white p-4 rounded-lg">
      <h4 className="font-semibold">Video evidence</h4>
      <p className="mt-1 text-sm text-stone-500">
        {media.original_filename !== "" ? media.original_filename : "uploaded video"} ·{" "}
        {media.duration_s.toFixed(0)}s
        {media.width > 0 ? ` · ${media.width}x${media.height}` : ""}
      </p>
      {media.frames.length > 0 && (
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {media.frames.map((frame) => (
            <figure key={frame.id}>
              <img src={frameUrl(missionId, frame.id)} alt={`Frame ${frame.id}`} />
              <figcaption className="mt-1 text-xs text-stone-500">
                {frame.id} @ {frame.timestamp_s.toFixed(1)}s
              </figcaption>
            </figure>
          ))}
        </div>
      )}
      {media.transcript !== null && media.transcript.text !== "" && (
        <div className="mt-3">
          <p className="text-sm font-semibold text-stone-600">
            Transcript (STATED{media.transcript.model_id !== "" ? ` · ${media.transcript.model_id}` : ""})
          </p>
          {media.transcript.turns.length > 0 ? (
            <ul className="mt-1 space-y-1">
              {media.transcript.turns.map((turn) => (
                <li key={turn.index} className="text-sm text-stone-700">
                  <span className="text-stone-500">
                    {(turn.start_ms / 1000).toFixed(0)}–{(turn.end_ms / 1000).toFixed(0)}s:
                  </span>{" "}
                  {turn.transcript}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm text-stone-700">{media.transcript.text}</p>
          )}
        </div>
      )}
      {media.observations.length > 0 && (
        <div className="mt-3">
          <p className="text-sm font-semibold text-stone-600">
            Visual observations (OBSERVED
            {media.vision_model_id !== "" ? ` · ${media.vision_model_id}` : ""})
          </p>
          <ul className="mt-1 space-y-1">
            {media.observations.map((obs, index) => (
              <li key={`${obs.observation.slice(0, 30)}-${index}`} className="text-sm text-stone-700">
                {obs.observation}{" "}
                <span className="text-stone-500">
                  ({obs.frame_ids.join(", ")}
                  {obs.confidence !== "" ? ` · ${obs.confidence}` : ""})
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {media.hypotheses.length > 0 && (
        <div className="mt-3">
          <p className="text-sm font-semibold text-stone-600">Hypotheses (INFERRED — need confirmation)</p>
          <ul className="mt-1 space-y-1">
            {media.hypotheses.map((hyp, index) => (
              <li key={`${hyp.hypothesis.slice(0, 30)}-${index}`} className="text-sm text-stone-700">
                {hyp.hypothesis}{" "}
                <span className="text-stone-500">({hyp.basis_frame_ids.join(", ")})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {media.notes.length > 0 && (
        <ul className="mt-2 space-y-1">
          {media.notes.map((note) => (
            <li key={note} className="text-sm text-stone-500">
              Note: {note}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function InterviewFlow({ backendOnline }: { backendOnline: boolean }) {
  const [mission, setMission] = useState<Mission | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [context, setContext] = useState("");
  const [useDemo, setUseDemo] = useState(true);
  const [answer, setAnswer] = useState("");
  const [feedback, setFeedback] = useState("");
  const [resumeId, setResumeId] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [mediaStage, setMediaStage] = useState<string | null>(null);
  const [inFlight, setInFlight] = useState<string | null>(null);
  // Persistent refresh while the backend works (async builds included);
  // also mirrors media status for the upload panel.
  const live = useMissionPolling(mission, setMission, (current) => {
    if (current.media !== null) setMediaStage(current.media.status);
  });

  async function run(
    stage: string,
    action: () => Promise<Mission>,
    clear?: () => void,
    step?: string | null,
  ) {
    setBusy(stage);
    setInFlight(step ?? null);
    setFailure(null);
    try {
      const updated = await action();
      setMission(updated);
      clear?.();
    } catch (error: unknown) {
      setFailure(error instanceof Error ? error.message : "Unknown error");
    } finally {
      setBusy(null);
      setInFlight(null);
    }
  }

  // Action-scoped polling, kept ONLY for repair: the mission stays in a
  // terminal (FAILED) status throughout, so the status-keyed live poller
  // above does not run. Every other long action is covered by live polling.
  function withMissionPolling(missionId: string, action: () => Promise<Mission>) {
    return async (): Promise<Mission> => {
      const handle = window.setInterval(() => {
        void getMission(missionId)
          .then((current) => {
            setMission(current);
            if (current.media !== null) setMediaStage(current.media.status);
          })
          .catch(() => undefined);
      }, 3000);
      try {
        return await action();
      } finally {
        window.clearInterval(handle);
      }
    };
  }

  function start() {
    void run(
      "Starting interview…",
      async () => {
        const created = await createMission("Interview");
        return startInterview(created.id, { owner_context: context, use_demo_evidence: useDemo });
      },
      undefined,
      "interview",
    );
  }

  function resume() {
    const id = resumeId.trim();
    if (id === "") return;
    void run("Loading mission…", () => getMission(id), undefined, "reload");
  }

  function uploadAndAnalyze() {
    if (selectedFile === null) return;
    const file = selectedFile;
    setBusy("Uploading video…");
    setInFlight("upload");
    setFailure(null);
    setMediaStage("uploaded");
    (async () => {
      try {
        const created = await createMission("Video mission");
        const uploaded = await uploadVideo(created.id, file);
        setMission(uploaded);
        setBusy("Analyzing video — this takes a couple of minutes…");
        setInFlight("analyze");
        const done = await analyzeMission(created.id);
        setMediaStage(done.media?.status ?? null);
        setMission(done);
        setSelectedFile(null);
      } catch (error: unknown) {
        setFailure(error instanceof Error ? error.message : "Unknown error");
      } finally {
        setBusy(null);
        setInFlight(null);
      }
    })();
  }

  function retryAnalyze(missionId: string) {
    setBusy("Analyzing video — this takes a couple of minutes…");
    setInFlight("analyze");
    setFailure(null);
    void analyzeMission(missionId)
      .then((done) => {
        setMediaStage(done.media?.status ?? null);
        setMission(done);
      })
      .catch((error: unknown) => {
        setFailure(error instanceof Error ? error.message : "Unknown error");
      })
      .finally(() => {
        setBusy(null);
        setInFlight(null);
      });
  }

  const pending =
    mission !== null ? (mission.interview.turns.find((t) => t.answer.trim() === "") ?? null) : null;
  const answered = mission !== null ? mission.interview.turns.filter((t) => t.answer.trim() !== "") : [];

  return (
    <div>
      {mission === null && (
        <div className="mt-2">
          <h3 className="font-semibold">Start from a business video</h3>
          <p className="mt-1 text-sm text-stone-600">
            Upload a short video of the operation. FIELD extracts frames, transcribes
            narration, and analyzes the visual evidence before interviewing you.
          </p>
          <label className="mt-2 block text-sm font-medium">
            Video file (mp4, mov, webm, m4v, avi, mkv — max 100 MB)
            <input
              type="file"
              accept="video/*"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
              className="mt-1 block w-full text-sm"
            />
          </label>
          {selectedFile !== null && (
            <p className="mt-1 text-sm text-stone-600">
              Selected: {selectedFile.name} ({(selectedFile.size / 1_000_000).toFixed(1)} MB)
            </p>
          )}
          <button
            type="button"
            onClick={uploadAndAnalyze}
            disabled={!backendOnline || busy !== null || selectedFile === null}
            className="mt-2 bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700 disabled:opacity-50 disabled:hover:bg-clay-600"
          >
            Upload &amp; analyze video
          </button>

          <div className="mt-6 border-t border-stone-200 pt-4">
            <h3 className="font-semibold">Or start without a video (development/testing)</h3>
            <p className="mt-1 text-sm text-stone-600">
              Describe the business problem in your own words. FIELD will ask up to three
              clarifying questions then draft an application specification for your approval.
            </p>
            <label className="mt-3 block text-sm font-medium">
              Business context
              <textarea
                value={context}
                onChange={(event) => setContext(event.target.value)}
                rows={4}
                placeholder="Orders are written on paper slips and get lost during rush hours…"
                className="mt-1 block w-full rounded-md border border-stone-300 bg-white p-2 text-sm"
              />
            </label>
            <label className="mt-2 flex items-center gap-2 text-sm text-stone-700">
              <input
                type="checkbox"
                checked={useDemo}
                onChange={(event) => setUseDemo(event.target.checked)}
              />
              Include labeled demo sample observations (test fixture, not a real video)
            </label>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={start}
                disabled={!backendOnline || busy !== null}
                className="bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700 disabled:opacity-50 disabled:hover:bg-clay-600"
              >
                Start interview
              </button>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-stone-200 pt-3">
            <input
              value={resumeId}
              onChange={(event) => setResumeId(event.target.value)}
              placeholder="mission id"
              className="rounded-md border border-stone-300 bg-white p-2 font-mono text-sm"
            />
            <button
              type="button"
              onClick={resume}
              disabled={!backendOnline || busy !== null || resumeId.trim() === ""}
              className="rounded-md border border-stone-300 bg-white px-4 py-2 font-medium hover:bg-parchment disabled:opacity-50 disabled:hover:bg-white"
            >
              Resume mission
            </button>
          </div>
        </div>
      )}

      {mission !== null && (
        <p className="mt-2 text-sm text-stone-500">
          Mission {mission.id} · {mission.status}
        </p>
      )}

      <div className="mt-3">
        <WorkflowProgress
          mission={mission}
          inFlight={inFlight}
          job={live.job}
          lastUpdated={live.lastUpdated}
          reconnecting={live.reconnecting}
        />
      </div>

      {mission !== null &&
        (mission.status === "VIDEO_UPLOADED" || mission.status === "ANALYZING") &&
        mission.media !== null &&
        mission.media.status !== "ready" &&
        mission.media.status !== "failed" && (
          <div className="mt-2">
            <p className="text-stone-700">
              {MEDIA_STAGE_LABELS[mediaStage ?? mission.media.status] ?? mission.media.status}…
            </p>
            <p className="mt-1 text-sm text-stone-500">
              {mission.media.original_filename !== "" ? mission.media.original_filename : "video"} ·{" "}
              {mission.media.duration_s.toFixed(0)}s · this takes a couple of minutes
            </p>
          </div>
        )}

      {mission !== null && mission.media !== null && mission.media.status === "failed" && (
        <div className="mt-2">
          <p className="font-medium text-red-800">Video analysis failed</p>
          <p className="mt-1 text-sm text-stone-600">{mission.media.error}</p>
          <button
            type="button"
            onClick={() => retryAnalyze(mission.id)}
            disabled={busy !== null}
            className="mt-2 rounded-md border border-stone-300 bg-white px-4 py-2 font-medium hover:bg-parchment disabled:opacity-50 disabled:hover:bg-white"
          >
            Retry analysis
          </button>
        </div>
      )}

      {mission !== null &&
        (mission.status === "VIDEO_UPLOADED" || mission.status === "ANALYZING") &&
        mission.media !== null &&
        mission.media.status === "ready" && (
        <div className="mt-2">
          <EvidencePreview missionId={mission.id} media={mission.media} />
          <label className="mt-3 block text-sm font-medium">
            Anything to add? (optional — stored as your own statement)
            <textarea
              value={context}
              onChange={(event) => setContext(event.target.value)}
              rows={2}
              placeholder="Extra context the video does not show…"
              className="mt-1 block w-full rounded-md border border-stone-300 bg-white p-2 text-sm"
            />
          </label>
          <button
            type="button"
            onClick={() =>
              void run(
                "Starting interview…",
                () => startInterview(mission.id, { owner_context: context, use_demo_evidence: false }),
                undefined,
                "interview",
              )
            }
            disabled={busy !== null}
            className="mt-2 bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700 disabled:opacity-50 disabled:hover:bg-clay-600"
          >
            Start interview from this evidence
          </button>
        </div>
      )}

      {mission !== null && mission.status === "INTERVIEWING" && pending !== null && (
        <div className="mt-2">
          <p className="text-sm text-stone-600">
            Question {pending.index} of max {mission.interview.max_questions}
          </p>
          <p className="mt-1 font-medium">{pending.question}</p>
          {pending.rationale !== "" && (
            <p className="mt-1 text-sm text-stone-600">Why we ask: {pending.rationale}</p>
          )}
          <textarea
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            rows={3}
            placeholder="Your answer…"
            className="mt-2 block w-full rounded-md border border-stone-300 bg-white p-2 text-sm"
          />
          <button
            type="button"
            onClick={() =>
              void run(
                "FIELD is thinking…",
                () => answerQuestion(mission.id, answer),
                () => setAnswer(""),
                "answer",
              )
            }
            disabled={busy !== null || answer.trim() === ""}
            className="mt-2 bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700 disabled:opacity-50 disabled:hover:bg-clay-600"
          >
            Submit answer
          </button>
        </div>
      )}

      {mission !== null && mission.status === "INTERVIEWING" && pending === null && (
        <div className="mt-2">
          <p className="font-medium text-moss-800">
            Interview complete — {answered.length} question(s) answered.
          </p>
          <button
            type="button"
            onClick={() =>
              void run(
                "Drafting specification…",
                () => generateRequirements(mission.id),
                undefined,
                "requirements",
              )
            }
            disabled={busy !== null}
            className="mt-2 bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700 disabled:opacity-50 disabled:hover:bg-clay-600"
          >
            Generate requirements
          </button>
        </div>
      )}

      {answered.length > 0 && mission?.status === "INTERVIEWING" && (
        <div className="mt-4 border-t border-stone-200 pt-3">
          <h4 className="text-sm font-semibold text-stone-600">Answered so far</h4>
          <ul className="mt-1 space-y-2">
            {answered.map((turn) => (
              <li key={turn.index} className="text-sm text-stone-700">
                <p className="font-medium">Q{turn.index}: {turn.question}</p>
                <p>A{turn.index}: {turn.answer}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {mission !== null && mission.status === "REQUIREMENTS_READY" && mission.spec !== null && (
        <div className="mt-2">
          <h3 className="text-lg font-semibold">Review the specification</h3>
          <SpecReview spec={mission.spec} />
          <label className="mt-4 block text-sm font-medium">
            Request changes
            <textarea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              rows={2}
              placeholder="e.g. orders must also track dine-in vs takeaway…"
              className="mt-1 block w-full rounded-md border border-stone-300 bg-white p-2 text-sm"
            />
          </label>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() =>
                void run(
                  "Revising specification…",
                  () => requestChanges(mission.id, feedback),
                  () => setFeedback(""),
                  "requirements",
                )
              }
              disabled={busy !== null || feedback.trim() === ""}
              className="rounded-md border border-stone-300 bg-white px-4 py-2 font-medium hover:bg-parchment disabled:opacity-50 disabled:hover:bg-white"
            >
              Revise
            </button>
            <button
              type="button"
              onClick={() =>
                void run(
                  "Recording approval…",
                  async () => {
                    const approved = await approveRequirements(mission.id);
                    setBusy("Generating application in E2B — this takes several minutes…");
                    return buildApplication(approved.id, false, crypto.randomUUID());
                  },
                  undefined,
                  "build",
                )
              }
              disabled={busy !== null}
              className="bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700 disabled:opacity-50 disabled:hover:bg-clay-600"
            >
              Approve requirements &amp; build
            </button>
          </div>
          <p className="mt-2 text-sm text-stone-500">
            Approval unlocks application generation. Nothing is built before you approve.
          </p>
        </div>
      )}

      {mission !== null && mission.status === "APPROVED" && (
        <div className="mt-2">
          <p className="font-medium text-moss-800">
            Approved{mission.approved_at !== "" ? ` at ${mission.approved_at}` : ""} — ready to build.
          </p>
          {mission.generation !== null && mission.generation.status === "failed" && (
            <div className="mt-1">
              {friendlyProviderError(mission.generation.error_class ?? "") !== null && (
                <p className="text-sm text-stone-700">
                  {friendlyProviderError(mission.generation.error_class ?? "")}
                </p>
              )}
              <p className="mt-1 text-sm text-red-800">
                Last generation failed and can be retried: {mission.generation.error.slice(0, 300)}
              </p>
            </div>
          )}
          <button
            type="button"
            onClick={() =>
              void run(
                "Generating application in E2B — this takes several minutes…",
                () => buildApplication(mission.id, false, crypto.randomUUID()),
                undefined,
                "build",
              )
            }
            disabled={busy !== null}
            className="mt-2 bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700 disabled:opacity-50 disabled:hover:bg-clay-600"
          >
            Build application
          </button>
          {mission.spec !== null && <SpecReview spec={mission.spec} />}
        </div>
      )}

      {mission !== null && ["BUILDING", "RUNNING", "AUDITING"].includes(mission.status) && (
        <div className="mt-2">
          <p className="text-stone-700">
            Build {mission.status.toLowerCase()}
            {mission.progress_label !== "" ? ` — ${mission.progress_label}` : ""} This page
            refreshes automatically.
          </p>
          <button
            type="button"
            onClick={() => void run("Cancelling…", () => cancelBuild(mission.id))}
            disabled={busy !== null}
            className="mt-2 rounded-md border border-stone-300 bg-white px-4 py-2 font-medium hover:bg-parchment disabled:opacity-50 disabled:hover:bg-white"
          >
            Cancel build
          </button>
        </div>
      )}

      {mission !== null && TERMINAL_BUILD_STATES.includes(mission.status) && (
        <div className="mt-2">
          <p className="text-sm text-stone-500">
            Mission {mission.id} · {mission.status}
          </p>
          {mission.generation !== null && (
            <>
              <h3 className="mt-3 text-lg font-semibold">Generated application</h3>
              <GenerationPanel generation={mission.generation} />
            </>
          )}
          <PreviewPanel
            mission={mission}
            busy={busy}
            onLaunch={() =>
              void run(
                "Starting preview sandbox — install and build take about a minute…",
                () => launchPreview(mission.id),
                undefined,
                "preview",
              )
            }
          />
          {mission.repair !== null && (
            <>
              <h3 className="mt-3 text-lg font-semibold">Repair &amp; retest</h3>
              <RepairPanel repair={mission.repair} />
            </>
          )}
          {mission.status === "FAILED" &&
            (mission.repair === null || mission.repair.attempts === 0) && (
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() =>
                    void run(
                      "Repairing and retesting in E2B — this takes several minutes…",
                      withMissionPolling(mission.id, () => repairApplication(mission.id)),
                      undefined,
                      "repair",
                    )
                  }
                  disabled={busy !== null}
                  className="mt-2 bg-clay-600 px-4 py-2 font-medium text-white rounded-md hover:bg-clay-700 disabled:opacity-50 disabled:hover:bg-clay-600"
                >
                  Repair &amp; retest (one attempt)
                </button>
                <p className="mt-1 text-sm text-stone-500">
                  FIELD gets one bounded repair: the failure evidence goes back to the
                  developer, then the fixed app is rebuilt and retested.
                </p>
              </div>
            )}
          {mission.verification !== null ? (
            <VerificationResults result={mission.verification} />
          ) : (
            <p className="mt-2 text-stone-600">No verification result recorded.</p>
          )}
          {mission.error !== "" && (
            <p className="mt-2 text-sm text-red-800">{mission.error}</p>
          )}
        </div>
      )}

      {mission !== null && <EvidencePanel items={mission.evidence} />}

      {busy !== null && <p className="mt-3 text-stone-700">{busy}</p>}
      {failure !== null && (
        <div className="mt-3">
          <p className="font-medium text-red-800">Something went wrong</p>
          <p className="mt-1 text-sm text-stone-600">{failure}</p>
          {mission !== null && (
            <button
              type="button"
              onClick={() =>
                void run("Reloading mission…", () => getMission(mission.id), undefined, "reload")
              }
              className="mt-2 rounded-md border border-stone-300 bg-white px-4 py-2 font-medium hover:bg-parchment"
            >
              Reload mission
            </button>
          )}
        </div>
      )}
      {mission !== null && mission.error !== "" && (
        <p className="mt-2 text-sm text-red-800">{mission.error}</p>
      )}
    </div>
  );
}
