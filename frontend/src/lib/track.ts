/**
 * Tiny first-party funnel tracker — POSTs client-reported top-of-funnel
 * events to the backend `POST /api/v1/events` (`app/api/events.py`).
 *
 * Hard constraints (brief): NO third-party analytics SDK, NO paid
 * dependency, first-party only, privacy-safe.
 *
 * Privacy: the only correlator sent is `anon_id` — a random UUID generated
 * in this browser and persisted in localStorage. It is NOT a fingerprint,
 * NOT an IP, NOT an email, NOT the Clerk id. It only ties a few events
 * together within one browser so the funnel conversion rate is computable.
 * No JWT is attached (the endpoint is Clerk-exempt and untrusted by
 * design) and no PII is ever put in `meta`.
 *
 * Fire-and-forget: `track()` NEVER throws, NEVER awaits in the caller's
 * critical path, and swallows every error (network down, 400, 429, blocked
 * localStorage). Instrumentation must never break or slow the user flow —
 * a tracking failure is invisible to the UI.
 *
 * Event kinds mirror the backend allowlist `ClientEventKind`
 * (`app/api/events.py` — landing_view / signup_started / dashboard_view /
 * pricing_view / checkout_started). The CONVERSIONS (signup_completed,
 * checkout_completed) are emitted SERVER-side by the Clerk/Stripe webhooks
 * and are intentionally NOT trackable from here.
 */

/** Same base derivation as `lib/api.ts:40-41` (kept independent so the
 * tracker has zero coupling to the authed client / Clerk). */
const RAW_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const EVENTS_URL = `${RAW_BASE.replace(/\/$/, "")}/api/v1/events`;

/** Client-reportable funnel events. 1:1 with the backend allowlist. */
export type TrackKind =
  | "landing_view"
  | "signup_started"
  | "dashboard_view"
  | "pricing_view"
  | "checkout_started"
  // Visitor clicked "Try it, no signup" on Landing (client INTENT). The
  // server-trustworthy `demo_run` (conversion actually ran) is emitted
  // server-side by `app/api/demo.py` and is intentionally NOT trackable
  // here — same boundary as signup_started vs (server) signup_completed.
  | "demo_started"
  // Signed-in user submitted the Dashboard upload form — the start of
  // converting their own data (Track 3 signup→first-conversion funnel).
  // Client INTENT only; the conversion actually running is the server-side
  // `job` lifecycle (`app/api/jobs.py`). 1:1 with the backend allowlist
  // `ClientEventKind.conversion_started` (`app/api/events.py`).
  | "conversion_started";

/** Small, non-PII metadata. Scalar values only (matches the backend
 * `EventIn.meta` shape: `dict[str, str|int|float|bool|None]`). */
export type TrackMeta = Record<string, string | number | boolean | null>;

const ANON_ID_KEY = "agb_anon_id";

/**
 * Get-or-create the per-browser anonymous id. Random (crypto UUID), stored
 * in localStorage. If localStorage is unavailable (privacy mode, blocked),
 * fall back to a per-page-load ephemeral id so tracking degrades instead
 * of throwing — the funnel just loses cross-page correlation for that
 * session, which is acceptable and strictly better than a crash.
 */
let ephemeralAnonId: string | null = null;

function newId(): string {
  // crypto.randomUUID is available in all evergreen browsers + the Vitest
  // jsdom env; guard anyway so a missing crypto can't throw.
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `anon-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function getAnonId(): string {
  try {
    const existing = localStorage.getItem(ANON_ID_KEY);
    if (existing) return existing;
    const id = newId();
    localStorage.setItem(ANON_ID_KEY, id);
    return id;
  } catch {
    // localStorage threw (Safari private mode, embedded webview, etc.).
    if (!ephemeralAnonId) ephemeralAnonId = newId();
    return ephemeralAnonId;
  }
}

/**
 * Record one funnel event. Fire-and-forget: returns void immediately, the
 * POST runs in the background, every failure is swallowed.
 *
 * Uses `keepalive: true` so an event fired right before a navigation
 * (e.g. `signup_started` on the sign-up link) still gets flushed even as
 * the page unloads — `fetch keepalive` is the standard, dependency-free
 * way to do this (replaces the old navigator.sendBeacon hack).
 */
export function track(kind: TrackKind, meta: TrackMeta = {}): void {
  let anonId: string;
  try {
    anonId = getAnonId();
  } catch {
    return; // even id resolution must never throw into the caller
  }

  try {
    void fetch(EVENTS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, anon_id: anonId, meta }),
      // No cookies, no Authorization: the endpoint is anonymous by design.
      credentials: "omit",
      keepalive: true,
    }).catch(() => {
      // Network error / abort — swallow. Instrumentation is best-effort.
    });
  } catch {
    // Synchronous throw (e.g. fetch unavailable in some test env) — swallow.
  }
}
