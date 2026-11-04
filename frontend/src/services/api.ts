/** Typed client for the FIELD backend API. */

export interface HealthStatus {
  status: string;
  service: string;
  env: string;
}

export interface VerificationCheck {
  name: string;
  category: string;
  status: "pass" | "fail" | "skipped" | "unavailable";
  required: boolean;
  detail: string;
  latency_s: number;
}

export interface BrowserStep {
  name: string;
  status: "pass" | "fail" | "not_applicable" | "blocked";
  detail: string;
  duration_ms: number;
}

export interface BrowserScreenshot {
  name: string;
  data_url: string;
}

export interface BrowserTestResult {
  available: boolean;
  browser: string;
  steps: BrowserStep[];
  screenshots: BrowserScreenshot[];
  error: string;
  latency_s: number;
}

export interface E2BVerification {
  available: boolean;
  passed: boolean;
  checks: VerificationCheck[];
  preview_url: string;
  sandbox_id: string;
  error: string;
  latency_s: number;
  browser: BrowserTestResult | null;
}

export interface AuditFinding {
  severity: "critical" | "high" | "medium" | "low";
  category: string;
  finding: string;
  evidence: string;
  recommendation: string;
  file: string;
}

export interface AuditResult {
  provider: string;
  available: boolean;
  completed: boolean;
  verdict: "PASS" | "PASS_WITH_WARNINGS" | "FAIL" | null;
  summary: string;
  findings: AuditFinding[];
  model_id: string;
  usage: Record<string, number>;
  error: string;
  latency_s: number;
}

export interface RequirementCoverage {
  requirement_id: string;
  requirement: string;
  status: "implemented" | "unimplemented" | "missing_evidence";
  files: string[];
  detail: string;
}

export interface AcceptanceMapping {
  criterion: string;
  status: "supported" | "unverified";
  by_checks: string[];
  detail: string;
}

export interface CoverageReport {
  requirements: RequirementCoverage[];
  acceptance: AcceptanceMapping[];
}

export interface FeatureMapping {
  feature: string;
  requirement_ids: string[];
}

export interface UnimplementedItem {
  requirement_id: string;
  reason: string;
}

export interface GenerationRecord {
  status: "generating" | "generated" | "failed";
  started_at: string;
  completed_at: string;
  model_id: string;
  usage: Record<string, number>;
  summary: string;
  manifest: string[];
  feature_map: FeatureMapping[];
  unimplemented: UnimplementedItem[];
  assumptions: string[];
  error: string;
}

export interface RepairRecord {
  status: "repairing" | "repaired" | "failed";
  attempts: number;
  trigger: string;
  previous_status: string;
  previous_reason: string;
  new_status: string;
  new_reason: string;
  summary: string;
  manifest: string[];
  model_id: string;
  usage: Record<string, number>;
  started_at: string;
  completed_at: string;
  error: string;
}

export interface VerificationResult {
  status: "VERIFIED" | "VERIFIED_WITH_WARNINGS" | "FAILED" | "AUDIT_LIMITED";
  reason: string;
  e2b: E2BVerification;
  audit: AuditResult;
  coverage: CoverageReport | null;
  audit_provider_active: string;
  audit_provider_target: string;
  started_at: string;
  completed_at: string;
}

export interface EvidenceItem {
  category: "OBSERVED" | "STATED" | "INFERRED";
  text: string;
  source: string;
}

export interface InterviewTurn {
  index: number;
  question: string;
  rationale: string;
  answer: string;
}

export interface InterviewState {
  status: "not_started" | "in_progress" | "complete";
  turns: InterviewTurn[];
  max_questions: number;
}

export interface FrameRecord {
  id: string;
  timestamp_s: number;
  path: string;
  width: number;
  height: number;
}

export interface TranscriptTurn {
  index: number;
  start_ms: number;
  end_ms: number;
  transcript: string;
  speaker: string;
}

export interface TranscriptRecord {
  text: string;
  turns: TranscriptTurn[];
  audio_duration_ms: number;
  model_id: string;
}

export interface VisualObservation {
  observation: string;
  frame_ids: string[];
  confidence: string;
}

export interface VisualHypothesis {
  hypothesis: string;
  basis_frame_ids: string[];
  confidence: string;
}

export interface MediaRecord {
  mission_id: string;
  original_filename: string;
  video_path: string;
  content_type: string;
  size_bytes: number;
  duration_s: number;
  width: number;
  height: number;
  status: "uploaded" | "extracting" | "transcribing" | "analyzing" | "ready" | "failed";
  error: string;
  frames: FrameRecord[];
  transcript: TranscriptRecord | null;
  observations: VisualObservation[];
  hypotheses: VisualHypothesis[];
  vision_model_id: string;
  vision_usage: Record<string, number>;
  notes: string[];
  created_at: string;
  updated_at: string;
}

export interface UserRole {
  name: string;
  description: string;
}

export interface FunctionalRequirement {
  id: string;
  requirement: string;
}

export interface UserJourney {
  name: string;
  steps: string[];
}

export interface DataEntity {
  name: string;
  fields: string[];
}

export interface StructuredRequirements {
  business_context: string;
  problem_statement: string;
  objectives: string[];
  user_roles: UserRole[];
  functional_requirements: FunctionalRequirement[];
  non_functional_requirements: string[];
  user_journeys: UserJourney[];
  core_features: string[];
  data_entities: DataEntity[];
  acceptance_criteria: string[];
  assumptions: string[];
  unresolved_questions: string[];
  revision: number;
}

export interface Mission {
  id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  requirements: string;
  acceptance_criteria: string[];
  files: Record<string, string> | null;
  verification: VerificationResult | null;
  error: string;
  generation: GenerationRecord | null;
  repair: RepairRecord | null;
  media: MediaRecord | null;
  evidence: EvidenceItem[];
  interview: InterviewState;
  spec: StructuredRequirements | null;
  approved_at: string;
  is_approved: boolean;
  progress_step: string;
  progress_label: string;
}

const API_URL =
  import.meta.env.VITE_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `Backend responded with status ${response.status}${detail ? `: ${detail.slice(0, 300)}` : ""}`,
    );
  }
  return (await response.json()) as T;
}

export async function getHealth(signal?: AbortSignal): Promise<HealthStatus> {
  const response = await fetch(`${API_URL}/health`, { signal });
  if (!response.ok) {
    throw new Error(`Backend responded with status ${response.status}`);
  }
  return (await response.json()) as HealthStatus;
}

export async function createDemoMission(signal?: AbortSignal): Promise<Mission> {
  return request<Mission>("/api/missions/demo", { method: "POST", signal });
}

export async function verifyMission(
  missionId: string,
  body: { use_demo_app: boolean },
  signal?: AbortSignal,
): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}/verify`, {
    method: "POST",
    body: JSON.stringify(body),
    signal,
  });
}

export async function createMission(name: string): Promise<Mission> {
  return request<Mission>("/api/missions", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function getMission(missionId: string): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}`);
}

export async function startInterview(
  missionId: string,
  body: { owner_context: string; use_demo_evidence: boolean },
): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}/interview/start`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function answerQuestion(
  missionId: string,
  answer: string,
): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}/interview/answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}

export async function generateRequirements(missionId: string): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}/requirements/generate`, {
    method: "POST",
  });
}

export async function requestChanges(
  missionId: string,
  feedback: string,
): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}/requirements/changes`, {
    method: "POST",
    body: JSON.stringify({ feedback }),
  });
}

export async function approveRequirements(missionId: string): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}/approve`, {
    method: "POST",
  });
}

export async function buildApplication(
  missionId: string,
  force = false,
): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}/build`, {
    method: "POST",
    body: JSON.stringify({ force }),
  });
}

export async function repairApplication(missionId: string): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}/repair`, {
    method: "POST",
  });
}

export async function uploadVideo(missionId: string, file: File): Promise<Mission> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_URL}/api/missions/${missionId}/video`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `Backend responded with status ${response.status}${detail ? `: ${detail.slice(0, 300)}` : ""}`,
    );
  }
  return (await response.json()) as Mission;
}

export async function analyzeMission(missionId: string): Promise<Mission> {
  return request<Mission>(`/api/missions/${missionId}/analyze`, {
    method: "POST",
  });
}

export async function getMedia(missionId: string): Promise<MediaRecord> {
  return request<MediaRecord>(`/api/missions/${missionId}/media`);
}

export function frameUrl(missionId: string, frameId: string): string {
  return `${API_URL}/api/missions/${missionId}/media/frames/${frameId}`;
}
