/**
 * TanStack Query v5 hooks for live job polling + the presign->PUT->create
 * upload chain. Cadences + patterns per frontend/D4_rehydration_spec.md §4.
 *
 * Polling-interval-as-function is documented at
 * https://tanstack.com/query/latest/docs/framework/react/guides/polling
 * (accessed 2026-05-16): returning `false` from `refetchInterval` halts the
 * timer — used here to stop on terminal states.
 */

import {
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { NoOrganizationError, useApiClient } from "./api";
import { ACTIVE_STATES, TERMINAL_STATES } from "./types";
import type { JobListResponse, JobView } from "./types";

const JOBS_KEY = ["jobs"] as const;
const jobKey = (id: string) => ["job", id] as const;

/**
 * Single QueryClient per module (NOT per render) per D4_rehydration_spec.md
 * §4.5. Defaults: TanStack auto-retries failed queries 3x with exponential
 * backoff (https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults,
 * accessed 2026-05-16) — this also absorbs the backend's 60 req/min/org 429
 * (Cycle H §4.2) without custom code.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 2,
        refetchOnWindowFocus: true,
      },
    },
  });
}

/**
 * Dashboard list. queryKey ['jobs']; 3s while any job active/pending,
 * 30s when idle (Cycle H §4.2). `refetchOnWindowFocus` already on via the
 * client default.
 */
export function useJobs() {
  const api = useApiClient();
  return useQuery({
    queryKey: JOBS_KEY,
    queryFn: () => api.listJobs(),
    refetchInterval: (query) => {
      const data = query.state.data as JobListResponse | undefined;
      if (!data) return 3_000;
      const busy = data.jobs.some(
        (j) => j.state === "pending" || ACTIVE_STATES.has(j.state),
      );
      return busy ? 3_000 : 30_000;
    },
  });
}

/**
 * Per-job poll. queryKey ['job', id]; 2s while running, 5s while
 * pending/queued, halts on terminal (Cycle H §4.2). `staleTime: 1_000`
 * prevents re-render storms during rapid transitions.
 */
export function useJob(jobId: string | null) {
  const api = useApiClient();
  return useQuery({
    queryKey: jobKey(jobId ?? "__none__"),
    queryFn: () => api.getJob(jobId as string),
    enabled: jobId !== null,
    staleTime: 1_000,
    refetchInterval: (query) => {
      const s = (query.state.data as JobView | undefined)?.state;
      if (!s) return 5_000;
      if (TERMINAL_STATES.has(s)) return false;
      if (s === "running" || s === "validating") return 2_000;
      return 5_000;
    },
  });
}

/**
 * Post-signup org-provisioning probe (Track 3 #1 friction).
 *
 * After Clerk sign-up the user is redirected straight to `/dashboard`, but
 * the personal org (`org_personal_<uid>`) is provisioned ASYNCHRONOUSLY by
 * the Clerk `user.created` Svix webhook (`app/api/webhooks.py`) — it mirrors
 * the DB rows then writes `public_metadata.org_id` back to Clerk, which
 * takes a few seconds. If the Dashboard's first call fires first, the JWT
 * carries no `org_id` claim and the middleware 403s `no_organization`
 * (`app/api/clerk_auth.py:230-240`) — a transient race, NOT a real error.
 *
 * This probe is the recovery loop. It re-issues `listJobs` on a `skipCache`
 * client (`useApiClient({ skipCache: true })`) so every attempt FORCE-MINTS
 * a fresh Clerk token — the only way the just-written `org_id` claim is
 * picked up (Clerk caches the minted token for ~its lifetime; a normal
 * `getToken` keeps re-handing the stale org-less one — see `lib/api.ts`).
 *
 * TanStack `retry` as a predicate runs the backoff for us: keep retrying
 * while the failure is `NoOrganizationError`, give up on anything else (a
 * genuine 401/500 is not a provisioning race). `retryDelay` is a fixed ~2s
 * (the brief's cadence); `~30s` ceiling = ~15 attempts. `enabled` lets the
 * Dashboard mount the probe ONLY once `useJobs` has actually hit the race,
 * so the happy path pays nothing.
 */
export const PROVISION_PROBE_INTERVAL_MS = 2_000;
export const PROVISION_PROBE_MAX_ATTEMPTS = 15; // ~30s ceiling at 2s cadence

/**
 * The probe's retry decision, extracted as a pure function so it is unit
 * testable without driving TanStack's 2s timer: retry ONLY a transient
 * `NoOrganizationError` race, and only until the ~30s attempt ceiling.
 */
export function shouldRetryProvisionProbe(
  failureCount: number,
  error: unknown,
): boolean {
  if (!(error instanceof NoOrganizationError)) return false;
  return failureCount < PROVISION_PROBE_MAX_ATTEMPTS;
}

export function useWorkspaceProbe(enabled: boolean) {
  const api = useApiClient({ skipCache: true });
  return useQuery({
    queryKey: ["workspace-probe"],
    queryFn: () => api.listJobs(),
    enabled,
    // Don't surface a cached probe result; each mount re-checks live.
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: shouldRetryProvisionProbe,
    retryDelay: PROVISION_PROBE_INTERVAL_MS,
  });
}

export interface UploadArgs {
  file: File;
  fromFormat: "agibot" | "lerobot-v3";
  toFormat: "agibot" | "lerobot-v3";
  onProgress?: (pct: number) => void;
}

/**
 * The 3-step upload chain (D4_rehydration_spec.md §2.2 + §4.3):
 *   1. POST /jobs/presign-upload  -> {job_id, upload_url, key}
 *   2. PUT bytes to upload_url via XHR (XHR not fetch — upload progress not
 *      in Chrome-stable fetch as of 2026-05; D4_rehydration_spec.md §2.2 step 2)
 *   3. POST /jobs {job_id}        -> JobView (status transitions to extracting)
 *
 * Optimistic insertion per §4.3: do NOT optimistically render the presign
 * step (no real bytes yet); prepend an optimistic row only on `onMutate` of
 * the create step, roll back on error, reconcile via invalidate on settle.
 */
export function useUpload() {
  const api = useApiClient();
  const qc = useQueryClient();

  return useMutation({
    mutationFn: async ({
      file,
      fromFormat,
      toFormat,
      onProgress,
    }: UploadArgs): Promise<JobView> => {
      const presign = await api.presignUpload({
        filename: file.name,
        from_format: fromFormat,
        to_format: toFormat,
        // Track 4 edge-hardening: report the byte length so the backend can
        // reject an oversized file with a clean 413 BEFORE issuing the R2
        // URL (app/api/jobs.py `presign_upload`) — no doomed upload, no
        // silent R2 failure.
        size_bytes: file.size,
      });

      await putToR2(presign.upload_url, file, onProgress);

      return api.createJob({ job_id: presign.job_id });
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: JOBS_KEY });
    },
  });
}

/**
 * Idle-timeout for the R2 PUT. Track 4 edge-hardening: without a timeout a
 * dropped connection mid-PUT (Wi-Fi loss, proxy stall) leaves the XHR — and
 * the upload progress bar — spinning forever with no error. `XMLHttpRequest`
 * `timeout` is an IDLE timer (resets on every byte of progress), so a slow
 * but live uplink for a large archive is NOT killed; only a genuinely
 * stalled connection trips it. 60s of zero progress is decisively dead.
 */
export const R2_UPLOAD_IDLE_TIMEOUT_MS = 60_000;

/** XHR PUT with upload progress. Rejects on non-2xx / network / abort /
 * stall. The reject messages are readable copy — they surface verbatim in
 * the UploadForm `role="alert"` slot. */
function putToR2(
  url: string,
  file: File,
  onProgress?: (pct: number) => void,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    // Idle timeout — see R2_UPLOAD_IDLE_TIMEOUT_MS. Resets on each progress
    // event, so it only fires when the connection has genuinely stalled.
    xhr.timeout = R2_UPLOAD_IDLE_TIMEOUT_MS;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(100);
        resolve();
      } else {
        reject(new Error(`R2 upload failed (HTTP ${xhr.status}).`));
      }
    };
    xhr.onerror = () =>
      reject(
        new Error(
          "Upload failed — the connection was lost. Check your network " +
            "and try the upload again.",
        ),
      );
    xhr.ontimeout = () =>
      reject(
        new Error(
          "Upload stalled — the connection went quiet. Check your network " +
            "and try the upload again.",
        ),
      );
    xhr.onabort = () => reject(new Error("Upload cancelled."));
    xhr.send(file);
  });
}

/**
 * Trigger a fresh presigned GET and start the browser download. We do NOT
 * inline download_url on the job row (D4_rehydration_spec.md §3.4: avoid
 * stale-URL UX) — fetch a fresh one on click.
 */
export function useDownload() {
  const api = useApiClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      const { download_url } = await api.presignDownload(jobId);
      const a = document.createElement("a");
      a.href = download_url;
      a.rel = "noopener";
      a.click();
    },
  });
}

/**
 * Clears the whole cache. Called on /sign-in mount so a fresh sign-in never
 * sees the previous tenant's cached jobs (D4_rehydration_spec.md §4.4).
 */
export function clearJobsCache(qc: QueryClient) {
  qc.clear();
}
