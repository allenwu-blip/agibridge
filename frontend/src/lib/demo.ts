/**
 * Anonymous client for the "try it, no signup" demo
 * (`POST /api/v1/demo/convert`, `app/api/demo.py`).
 *
 * Deliberately standalone — NOT routed through `lib/api.ts`'s
 * `useApiClient()`: that injects a Clerk `Authorization` header, and this
 * endpoint is Clerk-EXEMPT and anonymous BY DESIGN (the whole point is no
 * signup). Same zero-coupling posture as `lib/track.ts` (its own base
 * derivation, no Clerk import, `credentials:"omit"`).
 *
 * The backend contract guarantees this never 500s: success / soft-failure
 * are both HTTP 200 with `{ok}` in the body; the only non-200s are 429
 * (rate-limited) and 503 (demo box busy). We normalize ALL of those into a
 * `DemoResult` so the caller renders readable copy and Landing never shows
 * a raw error.
 */

import type { DemoResult } from "./types";

/** Same base derivation as `lib/track.ts:29-30` (kept independent so the
 * demo has zero coupling to the authed client / Clerk). */
const RAW_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

/**
 * Run the bundled-sample demo conversion. `anonId` is an OPTIONAL funnel
 * correlator (the same localStorage UUID `track()` uses) passed as a query
 * param — the demo works without it; no body is ever sent (the endpoint
 * rejects bodies — the input is a fixed server-side sample).
 *
 * Never throws: a network error / timeout is mapped to a soft
 * `{ok:false, state:"error"}` so the UI degrades instead of crashing.
 */
export async function runDemoConversion(
  anonId?: string | null,
): Promise<DemoResult> {
  const base = RAW_BASE.replace(/\/$/, "");
  const qs = anonId ? `?anon_id=${encodeURIComponent(anonId)}` : "";
  const url = `${base}/api/v1/demo/convert${qs}`;

  // The conversion is ~1.5s on real infra; allow generous headroom for a
  // cold Space + the single-flight queue before we give up client-side.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60_000);

  try {
    const res = await fetch(url, {
      method: "POST",
      // No body (endpoint takes none), no cookies, no Authorization:
      // anonymous by design.
      credentials: "omit",
      signal: controller.signal,
    });

    if (res.status === 429) {
      return {
        ok: false,
        state: "busy",
        message: "Lots of people are trying the demo right now.",
        suggestion: "Give it a minute and try again.",
      };
    }
    if (res.status === 503) {
      // The backend already returns the {ok:false,state:"busy",...} body.
      try {
        return (await res.json()) as DemoResult;
      } catch {
        return {
          ok: false,
          state: "busy",
          message: "The demo box is busy converting another sample.",
          suggestion: "Give it a few seconds and try again.",
        };
      }
    }

    // 200 (success OR soft failure — the body's `ok` disambiguates).
    return (await res.json()) as DemoResult;
  } catch {
    // Network down / aborted / non-JSON: degrade, never throw into render.
    return {
      ok: false,
      state: "error",
      message: "Couldn't reach the demo just now.",
      suggestion: "Check your connection and try again — or just sign up.",
    };
  } finally {
    clearTimeout(timer);
  }
}
