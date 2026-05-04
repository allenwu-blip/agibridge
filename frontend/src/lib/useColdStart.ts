/**
 * Cold-start detector. Phase D A2.6 (resolved):
 *   "Show 'Space is warming up — first run takes ~60s' overlay if /health
 *   returns 5xx OR doesn't respond within 5s on initial page load. Dismiss
 *   when /health is ok:true."
 *
 * Backend exposes /health at app/api/health.py:92-114 with HealthResponse
 * (app/schemas.py:90-97).
 */

import { useEffect, useState } from "react";
import { fetchHealth } from "./api";

export type ColdStartState = "checking" | "warming" | "ready" | "unhealthy";

const HEALTH_TIMEOUT_MS = 5_000;
const POLL_AFTER_FAILURE_MS = 3_000;

export function useColdStart(): ColdStartState {
  const [state, setState] = useState<ColdStartState>("checking");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const probe = async () => {
      try {
        const health = await fetchHealth(HEALTH_TIMEOUT_MS);
        if (cancelled) return;
        if (health.ok) {
          setState("ready");
        } else {
          // Defensive: backend defaults `ok=true` so this branch is unusual.
          setState("warming");
          timer = setTimeout(probe, POLL_AFTER_FAILURE_MS);
        }
      } catch (err) {
        if (cancelled) return;
        const e = err as Error & { status?: number };
        // 5xx OR timeout (AbortError) → warming. Distinguish hard 4xx (which
        // would be a backend bug — there is no 4xx on /health) from server
        // errors and abort.
        if (e.name === "AbortError" || (e.status !== undefined && e.status >= 500)) {
          setState("warming");
          timer = setTimeout(probe, POLL_AFTER_FAILURE_MS);
        } else if (e.status !== undefined && e.status >= 400) {
          setState("unhealthy");
        } else {
          // Network error (fetch threw) — also treat as warming.
          setState("warming");
          timer = setTimeout(probe, POLL_AFTER_FAILURE_MS);
        }
      }
    };

    void probe();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return state;
}
