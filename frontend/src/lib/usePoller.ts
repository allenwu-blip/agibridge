/**
 * Status poller hook. Implements spec §2.2 polling cadence:
 *   - 2s while `running`
 *   - 5s while `pending` (and `extracting`/`validating` by extension)
 *   - stops on terminal states (`done` | `failed` | `expired`)
 *
 * No analytics, no SDK — bare fetch + setTimeout. Cancels on unmount.
 */

import { useEffect, useRef, useState } from "react";
import { fetchStatus } from "./api";
import type { StatusResponse } from "./types";

export interface PollerHandle {
  status: StatusResponse | null;
  error: Error | null;
  /**
   * `true` once a terminal state (done/failed/expired) is reached or polling
   * was canceled by an unrecoverable error (e.g. 404 session_not_found).
   */
  terminated: boolean;
}

const RUNNING_INTERVAL_MS = 2_000;
const PENDING_INTERVAL_MS = 5_000;

function intervalFor(state: StatusResponse["state"] | "unknown"): number {
  switch (state) {
    case "running":
    case "validating":
    case "extracting":
      return RUNNING_INTERVAL_MS;
    case "pending":
      return PENDING_INTERVAL_MS;
    default:
      return PENDING_INTERVAL_MS;
  }
}

export function usePoller(sessionId: string | null): PollerHandle {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [terminated, setTerminated] = useState(false);
  const cancelRef = useRef<{ cancelled: boolean }>({ cancelled: false });

  useEffect(() => {
    if (!sessionId) return;
    cancelRef.current = { cancelled: false };
    const cancel = cancelRef.current;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (cancel.cancelled) return;
      try {
        const next = await fetchStatus(sessionId);
        if (cancel.cancelled) return;
        setStatus(next);
        const isTerminal =
          next.state === "done" ||
          next.state === "failed" ||
          next.state === "expired";
        if (isTerminal) {
          setTerminated(true);
          return;
        }
        timer = setTimeout(tick, intervalFor(next.state));
      } catch (err) {
        if (cancel.cancelled) return;
        // 404 session_not_found is terminal: spec §2.2 says it's the lookup
        // returned for both never-existed and purged sessions.
        const e = err as Error & { status?: number };
        setError(e);
        if (e.status === 404) {
          setTerminated(true);
          return;
        }
        // Transient error — keep trying at a backoff.
        timer = setTimeout(tick, RUNNING_INTERVAL_MS);
      }
    };

    // First tick immediate.
    void tick();

    return () => {
      cancel.cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [sessionId]);

  return { status, error, terminated };
}
