import { useEffect, useRef, useState } from "react";
import {
  MACHINE_ACTIVE_STATUSES,
  TERMINAL_STATUSES,
  getBuildJob,
  getMission,
  type BuildJob,
  type Mission,
} from "../services/api.ts";

export interface LivePollState {
  /** Last successful refresh, for "updated Ns ago". Null if never refreshed. */
  lastUpdated: Date | null;
  /** True while refreshes are failing (backing off, still trying). */
  reconnecting: boolean;
  /** Active background build job, if any (worker liveness). */
  job: BuildJob | null;
}

const POLL_MS = 2500;
const BACKOFF_CAP_MS = 30000;

/**
 * Persistent mission refresh, keyed on mission status rather than on any
 * single request's lifetime: it polls every ~2.5s while the backend is
 * working (including async background builds whose POST returns at once),
 * backs off with a visible reconnecting state on errors, refreshes
 * immediately when the tab becomes visible, and stops at terminal status.
 */
export function useMissionPolling(
  mission: Mission | null,
  setMission: (mission: Mission) => void,
  onRefresh?: (mission: Mission) => void,
): LivePollState {
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [job, setJob] = useState<BuildJob | null>(null);
  const delayRef = useRef(POLL_MS);
  const status = mission?.status ?? null;
  const missionId = mission?.id ?? null;

  useEffect(() => {
    if (missionId === null || status === null) return;
    if (TERMINAL_STATUSES.includes(status)) {
      setJob(null);
      return;
    }
    if (!MACHINE_ACTIVE_STATUSES.includes(status)) {
      return;
    }
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const [current, currentJob] = await Promise.all([
          getMission(missionId),
          getBuildJob(missionId),
        ]);
        if (!alive) return;
        setMission(current);
        setJob(currentJob);
        setLastUpdated(new Date());
        setReconnecting(false);
        onRefresh?.(current);
        delayRef.current = POLL_MS;
        if (!TERMINAL_STATUSES.includes(current.status)) {
          timer = window.setTimeout(tick, delayRef.current);
        }
      } catch {
        if (!alive) return;
        setReconnecting(true);
        delayRef.current = Math.min(delayRef.current * 2, BACKOFF_CAP_MS);
        timer = window.setTimeout(tick, delayRef.current);
      }
    };
    timer = window.setTimeout(tick, delayRef.current);
    const onVisible = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // Restart the cadence only when the mission or its status changes; every
    // tick re-reads fresh state, so per-tick mission objects must not restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missionId, status]);

  return { lastUpdated, reconnecting, job };
}
