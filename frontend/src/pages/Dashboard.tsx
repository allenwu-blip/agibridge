/**
 * Signed-in dashboard. Stories #6 (Stripe redirects) + #9 (polling).
 *
 * Composes:
 *   - <UploadForm> with parent-owned presign->PUT->create (useUpload)
 *   - <JobsTable> with §5 loading/empty/error/data branches (useJobs)
 *   - Upgrade CTA -> POST /billing/checkout -> window.location (Story #6)
 *   - Manage subscription -> POST /billing/portal -> window.location
 *
 * Org model = auto personal-org (DR-022/DR-023): Clerk Organizations is
 * DISABLED. Every user's org (`org_personal_<uid>`) is provisioned
 * server-side by the Clerk `user.created` Svix webhook
 * (app/api/webhooks.py) and carried as the flat `org_id` JWT claim
 * (custom Clerk template, app/api/clerk_auth.py:18,110). The frontend
 * has NO org-creation UI and does NOT gate on a Clerk org — this route
 * is already <SignedIn>-gated (App.tsx), so a signed-in user lands
 * straight in the app.
 *
 * Refs: frontend/D4_rehydration_spec.md §4.3 (optimistic via invalidate),
 * §5 (states).
 */

import { useEffect, useState } from "react";
import { UserButton } from "@clerk/clerk-react";
import { track } from "../lib/track";
import { useJobs, useUpload } from "../lib/queries";
import { useApiClient } from "../lib/api";
import { ApiError, NoOrganizationError } from "../lib/api";
import {
  EmptyJobs,
  JobsError,
  JobsTable,
  JobsTableSkeleton,
} from "../components/JobsTable";
import { UploadForm } from "../components/UploadForm";
import { WorkspaceProvisioning } from "../components/WorkspaceProvisioning";

/**
 * Track 4 edge-hardening — Stripe checkout return banner.
 *
 * `api.ts` sends Stripe `success_url`/`cancel_url` of
 * `/dashboard?checkout=success|cancel` (lib/api.ts `startCheckout`), but the
 * Dashboard previously never read that param: a customer who clicked
 * "Cancel" on the Stripe page landed back on a silent dashboard with no
 * acknowledgment — a confusing "did anything happen?" state. This reads the
 * param and shows a calm, dismissible banner. The cancel copy is explicit
 * that NO charge was made (the #1 anxiety after a billing-flow exit). It is
 * NOT an error — `role="status"`, not `alert`.
 *
 * The authoritative paid-tier flip is the Stripe webhook (app/api/billing.py
 * `_apply_subscription_state`), never this param — `?checkout=success` only
 * means the user finished the Stripe-hosted page; the banner says
 * "activating", and the live `orgs.tier` follows once the webhook lands.
 *
 * On mount it strips `?checkout` from the URL via `history.replaceState` so a
 * refresh or shared link doesn't re-show a stale banner.
 */
export function CheckoutBanner() {
  const [outcome, setOutcome] = useState<"success" | "cancel" | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const v = params.get("checkout");
    if (v !== "success" && v !== "cancel") return;
    setOutcome(v);
    // Drop the query param so a reload doesn't resurface the banner.
    params.delete("checkout");
    const qs = params.toString();
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (qs ? `?${qs}` : ""),
    );
  }, []);

  if (outcome === null) return null;

  const isCancel = outcome === "cancel";
  return (
    <div
      role="status"
      className={[
        "mb-6 flex items-start justify-between gap-4 rounded-lg border p-4 text-sm",
        isCancel
          ? "border-stone-300 bg-stone-50 dark:border-stone-700 dark:bg-stone-900/40"
          : "border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950/30",
      ].join(" ")}
    >
      <p className="text-stone-700 dark:text-stone-300">
        {isCancel
          ? "Checkout cancelled — no charge was made. You can upgrade any time."
          : "Thanks! Your upgrade is being activated — your new plan limits apply as soon as the payment is confirmed."}
      </p>
      <button
        type="button"
        onClick={() => setOutcome(null)}
        aria-label="Dismiss"
        className="shrink-0 rounded px-2 text-stone-500 hover:text-stone-800 dark:hover:text-stone-200"
      >
        ✕
      </button>
    </div>
  );
}

function BillingButtons() {
  const api = useApiClient();
  const [busy, setBusy] = useState<null | "checkout" | "portal">(null);
  const [err, setErr] = useState<string | null>(null);

  async function go(kind: "checkout" | "portal") {
    setErr(null);
    setBusy(kind);
    // Funnel: the user clicked Upgrade — intent to buy, BEFORE the Stripe
    // redirect. The matching server-trustworthy `checkout_completed` is
    // emitted by the Stripe webhook (app/api/billing.py), never the client.
    // Fired before the await so it is recorded even though go() then
    // navigates the page away. Not tracked for the portal path.
    if (kind === "checkout") track("checkout_started", { tier: "solo" });
    try {
      const res =
        kind === "checkout"
          ? await api.startCheckout("solo")
          : await api.openPortal();
      // Full-page redirect to the Stripe-hosted page (Story #6).
      window.location.assign(res.url);
    } catch (e) {
      setBusy(null);
      setErr(
        e instanceof ApiError
          ? `Billing is not available yet (${e.detail.code}).`
          : "Billing is not available yet.",
      );
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => go("checkout")}
          disabled={busy !== null}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-60"
        >
          {busy === "checkout" ? "Redirecting…" : "Upgrade"}
        </button>
        <button
          type="button"
          onClick={() => go("portal")}
          disabled={busy !== null}
          className="rounded-md border border-stone-300 px-3 py-1.5 text-sm font-medium hover:bg-stone-100 disabled:opacity-60 dark:border-stone-700 dark:hover:bg-stone-900"
        >
          {busy === "portal" ? "Redirecting…" : "Manage subscription"}
        </button>
      </div>
      {err && (
        <p role="alert" className="text-xs text-red-700 dark:text-red-300">
          {err}
        </p>
      )}
    </div>
  );
}

function JobsSection() {
  const { data, status, fetchStatus, error, refetch } = useJobs();
  // Once the post-signup org-provisioning probe has resolved we suppress the
  // provisioning panel even while `useJobs` is mid-refetch — without this the
  // section would flip provisioning → (brief refetch) → provisioning again.
  const [provisioned, setProvisioned] = useState(false);

  // The org-provisioning race (Track 3 #1): a freshly-signed-up user reaches
  // the Dashboard before the `user.created` webhook has set their `org_id`
  // claim, so `useJobs` 403s `no_organization`. Under DR-022/DR-023 this is
  // ALWAYS transient (Clerk Orgs disabled — there is no real "no org" state),
  // so we show the calm WorkspaceProvisioning panel instead of JobsError and
  // let it auto-retry with fresh tokens until the claim lands.
  const racing = status === "error" && error instanceof NoOrganizationError;
  if (racing && !provisioned) {
    return <WorkspaceProvisioning onReady={() => setProvisioned(true)} />;
  }
  // After the probe resolved, `useJobs` keeps its stale 403 `error` until the
  // invalidation-triggered refetch lands (TanStack v5: `status` stays "error"
  // while `fetchStatus` is "fetching"). Show the skeleton during that window
  // so the transition never flashes JobsError.
  if (provisioned && status === "error" && fetchStatus === "fetching") {
    return <JobsTableSkeleton rows={3} />;
  }

  if (status === "pending") return <JobsTableSkeleton rows={3} />;
  if (status === "error") {
    return (
      <JobsError
        message={error instanceof Error ? error.message : "Unknown error"}
        onRetry={() => void refetch()}
      />
    );
  }
  if (data.jobs.length === 0) return <EmptyJobs />;
  return (
    <div className="overflow-x-auto rounded-lg border border-stone-200 dark:border-stone-800">
      <JobsTable jobs={data.jobs} />
    </div>
  );
}

function ConversionPanel() {
  const upload = useUpload();
  const [pct, setPct] = useState<number | null>(null);

  async function onPresignAndStart({ file }: { file: File }) {
    // Funnel: the user submitted the upload form — INTENT to convert their
    // own data, the Track 3 first-conversion signal. Fired BEFORE the
    // presign→PUT→create chain so it is recorded even if the upload then
    // fails. track() is fire-and-forget (no Clerk identity by design); the
    // conversion actually running is the server-side `job` lifecycle.
    track("conversion_started", { direction: "agibot__lerobot-v3" });
    setPct(0);
    try {
      await upload.mutateAsync({
        file,
        // Single-direction MVP (D4_rehydration_spec.md §2.4); backend still
        // requires the pair (app/api/jobs.py:80-82).
        fromFormat: "agibot",
        toFormat: "lerobot-v3",
        onProgress: setPct,
      });
    } finally {
      setPct(null);
    }
  }

  // Track 4 edge-hardening: surface the FULL typed error, not just
  // `.message`. The backend's `{code,message,suggestion}` envelope carries
  // the actionable next step — for a 402 `soft_cap_exceeded` (plan limit
  // hit) or a 413 `upload_too_large` the `suggestion` is the upgrade/CLI
  // path. Without appending it the user saw only "You've reached your Free
  // plan's monthly limit (5)." with no told-you-how-to-fix-it. A non-typed
  // Error (e.g. a network failure from `putToR2`) still surfaces verbatim.
  let uploadError: string | null = null;
  if (upload.isError && upload.error instanceof ApiError) {
    const d = upload.error.detail;
    uploadError = d.suggestion ? `${d.message} ${d.suggestion}` : d.message;
  } else if (upload.isError && upload.error instanceof Error) {
    uploadError = upload.error.message;
  }

  return (
    <div className="rounded-lg border border-stone-200 p-6 dark:border-stone-800">
      <UploadForm
        disabled={upload.isPending}
        onPresignAndStart={onPresignAndStart}
        uploadPct={pct}
        uploadError={uploadError}
      />
    </div>
  );
}

export function Dashboard() {
  // Funnel: one `dashboard_view` per mount of the signed-in dashboard
  // (the post-signup landing surface). track() is fire-and-forget; the
  // endpoint is anonymous so this carries no Clerk identity by design.
  useEffect(() => {
    track("dashboard_view");
  }, []);

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <CheckoutBanner />
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Conversions</h1>
          <p className="text-sm text-stone-500">Personal workspace</p>
        </div>
        <div className="flex items-center gap-6">
          <BillingButtons />
          <UserButton afterSignOutUrl="/" />
        </div>
      </div>

      <div className="space-y-8">
        <ConversionPanel />
        <JobsSection />
      </div>
    </main>
  );
}
