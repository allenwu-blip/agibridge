/**
 * Sentry browser SDK init (D4 dispatch §5 "Sentry browser SDK init").
 * DSN injected by DevOps as VITE_SENTRY_DSN in Vercel env (D4_specs.md §6
 * Deliverable 5). No-op if the DSN is absent so local dev / preview without
 * the secret never errors.
 *
 * F-1 carry-forward discipline (D4_specs.md §4 Refactor #5): do NOT attach
 * user-attributable strings beyond the opaque Clerk id + org id.
 */

import * as Sentry from "@sentry/react";

let initialized = false;

export function initSentry(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN;
  if (!dsn || initialized) return;
  Sentry.init({
    dsn,
    tracesSampleRate: 0.1,
    environment: import.meta.env.MODE,
  });
  initialized = true;
}

/** Tag the opaque Clerk identity after Clerk loads (dispatch §5). */
export function setSentryUser(userId: string, orgId: string | null): void {
  if (!initialized) return;
  Sentry.setUser({ id: userId });
  Sentry.setTag("org_id", orgId ?? "none");
}

export { Sentry };
