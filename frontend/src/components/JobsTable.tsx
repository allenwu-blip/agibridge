/**
 * Jobs table + sub-states. Per frontend/D4_rehydration_spec.md §5:
 *   §5.1 empty  — <EmptyJobs/> on success + jobs.length === 0
 *   §5.2 loading — 3-row skeleton on pending (same <table> chrome, no jump)
 *   §5.3 error  — <section role="alert"> + Retry for 5xx / network
 *   §5.4 per-job — status badge: color decorative, aria-label semantic;
 *                  action column: done -> Download, failed -> View error
 *
 * Supersedes frontend/v0-spa StatusPanel (single-StatusResponse shape didn't
 * fit a list — D4_rehydration_spec.md §6 row). Status badge state names track
 * the live `JobState` (app/schemas.py:32-38).
 */

import { useState } from "react";
import type { JobState, JobView } from "../lib/types";
import { useDownload } from "../lib/queries";

const BADGE: Record<
  JobState,
  { dot: string; label: string }
> = {
  pending: { dot: "bg-stone-400", label: "upload pending" },
  extracting: { dot: "bg-amber-500", label: "extracting" },
  running: { dot: "bg-blue-500 animate-pulse", label: "running" },
  validating: { dot: "bg-blue-500 animate-pulse", label: "validating" },
  done: { dot: "bg-green-600", label: "done" },
  failed: { dot: "bg-red-600", label: "failed — see details" },
  expired: { dot: "bg-stone-400", label: "file purged" },
};

function StatusBadge({ state }: { state: JobState }) {
  const b = BADGE[state];
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden="true"
        className={`inline-block h-2 w-2 rounded-full ${b.dot}`}
      />
      <span className="text-sm" aria-label={b.label}>
        {state}
      </span>
    </span>
  );
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function duration(job: JobView): string {
  if (!job.started_at) return "—";
  const end = job.finished_at ? new Date(job.finished_at) : new Date();
  const ms = end.getTime() - new Date(job.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function JobActions({ job }: { job: JobView }) {
  const download = useDownload();
  const [errOpen, setErrOpen] = useState(false);

  if (job.state === "done") {
    return (
      <button
        type="button"
        onClick={() => download.mutate(job.job_id)}
        disabled={download.isPending}
        className="rounded-md bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-60"
      >
        {download.isPending ? "Preparing…" : "Download"}
      </button>
    );
  }
  if (job.state === "failed") {
    return (
      <div>
        <button
          type="button"
          onClick={() => setErrOpen((v) => !v)}
          aria-expanded={errOpen}
          className="rounded-md border border-red-300 px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950/40"
        >
          {errOpen ? "Hide error" : "View error"}
        </button>
        {errOpen && (
          <div className="mt-2 max-w-md text-xs">
            <span className="inline-block rounded bg-stone-200 px-1.5 py-0.5 font-mono dark:bg-stone-800">
              {job.error_code ?? "error"}
            </span>
            <p className="mt-1 text-stone-700 dark:text-stone-300">
              {job.error_msg ?? "The conversion failed."}
            </p>
          </div>
        )}
      </div>
    );
  }
  return <span className="text-stone-400">—</span>;
}

export function JobsTableSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <table className="w-full text-left">
      <Thead />
      <tbody>
        {Array.from({ length: rows }).map((_, i) => (
          <tr key={i} className="border-t border-stone-200 dark:border-stone-800">
            {Array.from({ length: 5 }).map((__, j) => (
              <td key={j} className="px-3 py-3">
                <span className="block h-3 w-24 animate-pulse rounded bg-stone-200 dark:bg-stone-800" />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Thead() {
  return (
    <thead>
      <tr className="text-xs uppercase tracking-wide text-stone-500">
        <th scope="col" className="px-3 py-2 font-medium">Direction</th>
        <th scope="col" className="px-3 py-2 font-medium">Status</th>
        <th scope="col" className="px-3 py-2 font-medium">Created</th>
        <th scope="col" className="px-3 py-2 font-medium">Duration</th>
        <th scope="col" className="px-3 py-2 font-medium">Actions</th>
      </tr>
    </thead>
  );
}

/**
 * First-run empty state (Track 3 — guide the first conversion). A brand-new
 * user lands here with zero jobs; this is their cue to act. The copy names
 * the concrete next step (drop an AgiBot World archive in the form ABOVE),
 * states the fixed MVP direction so the direction is never a question, and
 * sets the expectation that conversions stream back into this table. No CTA
 * button — the obvious action IS the UploadForm directly above; a second
 * button here would only split attention (the test pins "no button").
 */
export function EmptyJobs() {
  return (
    <section
      aria-labelledby="empty-jobs-heading"
      className="rounded-lg border border-stone-200 p-10 text-center dark:border-stone-800"
    >
      <h3 id="empty-jobs-heading" className="text-base font-semibold">
        Convert your first dataset
      </h3>
      <p className="mx-auto mt-2 max-w-md text-sm text-stone-600 dark:text-stone-400">
        Drop an AgiBot World archive into the upload box above and hit{" "}
        <span className="font-medium text-stone-700 dark:text-stone-300">
          Convert
        </span>
        . We&rsquo;ll turn it into a verified LeRobot v3 dataset — your
        conversions show up here as they run, ready to download when done.
      </p>
    </section>
  );
}

export function JobsError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  const offline = message === "Failed to fetch";
  return (
    <section
      role="alert"
      className="rounded-lg border border-red-200 p-6 dark:border-red-900"
    >
      <h3 className="text-base font-semibold">
        Couldn&rsquo;t load your conversions.
      </h3>
      <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
        {offline
          ? "Looks like you're offline. Reconnect and retry."
          : "The server returned an error. Retry below; if it keeps failing, check status.agibridge.dev."}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
      >
        Retry
      </button>
    </section>
  );
}

export function JobsTable({ jobs }: { jobs: JobView[] }) {
  return (
    <table className="w-full text-left">
      <Thead />
      <tbody>
        {jobs.map((job) => (
          <tr
            key={job.job_id}
            className="border-t border-stone-200 align-top dark:border-stone-800"
          >
            <td className="px-3 py-3 text-sm">
              {job.from_format ?? "?"} → {job.to_format ?? "?"}
            </td>
            <td className="px-3 py-3">
              <StatusBadge state={job.state} />
            </td>
            <td className="px-3 py-3 text-sm text-stone-600 dark:text-stone-400">
              {fmtTime(job.created_at)}
            </td>
            <td className="px-3 py-3 text-sm text-stone-600 dark:text-stone-400">
              {duration(job)}
            </td>
            <td className="px-3 py-3">
              <JobActions job={job} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
