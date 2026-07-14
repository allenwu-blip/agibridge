/**
 * Post-signup workspace-provisioning state (Track 3 #1 friction fix).
 *
 * The race: Clerk redirects a freshly-signed-up user straight to
 * `/dashboard` (`App.tsx` `forceRedirectUrl="/dashboard"`), but the personal
 * org (`org_personal_<uid>`) is provisioned ASYNCHRONOUSLY by the Clerk
 * `user.created` Svix webhook (`app/api/webhooks.py`) — it mirrors the DB
 * rows then writes `public_metadata.org_id` back to Clerk, ~a few seconds.
 * If the Dashboard's first backend call fires first, the JWT carries no
 * `org_id` claim and the middleware 403s `no_organization`
 * (`app/api/clerk_auth.py:230-240`). Under DR-022/DR-023 (Clerk Orgs
 * disabled, auto-personal-org) EVERY `no_organization` is this transient
 * race — never a real "you must create an org" state (see `lib/api.ts`
 * `NoOrganizationError` docstring).
 *
 * So instead of `JobsError`'s scary "Couldn't load your conversions", a
 * fresh user sees this calm "Setting up your workspace…" panel. It mounts
 * `useWorkspaceProbe` (`lib/queries.ts`) — a `skipCache` retry loop that
 * force-mints a fresh Clerk token every ~2s up to ~30s until the new
 * `org_id` claim lands. On success it invalidates the `jobs` query and
 * calls `onReady`; the Dashboard then transitions to the normal app with
 * NO user action. If the ~30s ceiling is genuinely exceeded (webhook truly
 * stuck), it degrades to a manual "Retry" — never an unrecoverable dead end.
 */

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useWorkspaceProbe } from "../lib/queries";
import { NoOrganizationError } from "../lib/api";

export function WorkspaceProvisioning({ onReady }: { onReady: () => void }) {
  const qc = useQueryClient();
  // `enabled` is always true here — the component only mounts once the
  // Dashboard has already observed the race, so the probe should run now.
  const probe = useWorkspaceProbe(true);

  // Probe succeeded → the org claim is live. Drop the stale `jobs` error so
  // the Dashboard's `useJobs` refetches cleanly, then hand control back.
  useEffect(() => {
    if (probe.status === "success") {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      onReady();
    }
  }, [probe.status, qc, onReady]);

  // The probe only stops retrying on the ~30s ceiling or a non-race error.
  // `NoOrganizationError` past the ceiling = "still provisioning, took
  // unusually long"; anything else = a genuine failure. Either way the user
  // gets a calm retry rather than being stranded.
  const stalled = probe.status === "error";
  const stillProvisioning =
    stalled && (probe.error as unknown) instanceof NoOrganizationError;

  return (
    <section
      role="status"
      aria-live="polite"
      aria-labelledby="provisioning-heading"
      className="rounded-lg border border-stone-200 p-10 text-center dark:border-stone-800"
    >
      {!stalled ? (
        <>
          {/* Decorative spinner; the semantic state is the heading + copy. */}
          <span
            aria-hidden="true"
            className="mx-auto block h-6 w-6 animate-spin rounded-full border-2 border-stone-300 border-t-indigo-600 dark:border-stone-700 dark:border-t-indigo-400"
          />
          <h3
            id="provisioning-heading"
            className="mt-4 text-base font-semibold"
          >
            Setting up your workspace…
          </h3>
          <p className="mx-auto mt-2 max-w-md text-sm text-stone-600 dark:text-stone-400">
            We&rsquo;re provisioning your personal workspace — this only
            happens once and usually takes a few seconds. Your dashboard will
            open automatically; no need to refresh.
          </p>
        </>
      ) : (
        <>
          <h3
            id="provisioning-heading"
            className="text-base font-semibold"
          >
            Still setting things up.
          </h3>
          <p className="mx-auto mt-2 max-w-md text-sm text-stone-600 dark:text-stone-400">
            {stillProvisioning
              ? "Your workspace is taking longer than usual to provision. Give it a moment, then retry."
              : "We couldn't reach the server to finish setup. Retry below; if it keeps failing, check status.agibridge.dev."}
          </p>
          <button
            type="button"
            onClick={() => void probe.refetch()}
            className="mt-4 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
          >
            Retry
          </button>
        </>
      )}
    </section>
  );
}
