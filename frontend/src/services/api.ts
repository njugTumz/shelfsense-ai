/** Typed client for the FIELD backend API. */

export interface HealthStatus {
  status: string;
  service: string;
  env: string;
}

const API_URL =
  import.meta.env.VITE_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export async function getHealth(signal?: AbortSignal): Promise<HealthStatus> {
  const response = await fetch(`${API_URL}/health`, { signal });
  if (!response.ok) {
    throw new Error(`Backend responded with status ${response.status}`);
  }
  return (await response.json()) as HealthStatus;
}
