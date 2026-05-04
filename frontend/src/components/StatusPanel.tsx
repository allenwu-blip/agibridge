/**
 * Status panel. Renders the current StatusResponse plus a download button
 * when state == done.
 *
 * Backend contract:
 *   - state machine values per app/schemas.py:33-39
 *   - download_url is set only on state == done per app/api/status.py:43-45
 *   - error is set only on state == failed per app/api/status.py:35-41
 */

import { downloadUrlFor } from "../lib/api";
import type { StatusResponse } from "../lib/types";
import { ProgressBar } from "./ProgressBar";

interface Props {
  status: StatusResponse;
}

const STATE_HUMAN: Record<StatusResponse["state"], string> = {
  pending: "Waiting for the converter to pick this up.",
  extracting: "Extracting the archive into the workspace.",
  running: "Running embodied-data convert (this is the long step).",
  validating: "Running the 5-check validator on the converted output.",
  done: "Conversion finished. Download below.",
  failed: "The run did not finish. Details below.",
  expired: "This run has expired and its files are gone.",
};

export function StatusPanel({ status }: Props) {
  return (
    <section
      aria-labelledby="status-heading"
      className="space-y-3 rounded-lg border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900/40"
    >
      <h2 id="status-heading" className="text-base font-semibold">
        Run status
      </h2>
      <ProgressBar
        state={status.state}
        estimatedProgressPct={status.estimated_progress_pct}
      />
      <p className="text-sm text-stone-700 dark:text-stone-300">
        {STATE_HUMAN[status.state]}
      </p>
      <p className="font-mono text-xs text-stone-500 dark:text-stone-400">
        session_id: {status.session_id}
      </p>
      {status.state === "done" && status.download_url && (
        <DownloadButton sessionId={status.session_id} />
      )}
    </section>
  );
}

function DownloadButton({ sessionId }: { sessionId: string }) {
  // Use anchor with `download` to trigger browser save. Backend sets
  // Content-Disposition: attachment; filename="<session_id>.zip" per
  // app/api/download.py:64-66.
  const href = downloadUrlFor(sessionId);
  return (
    <a
      href={href}
      download
      className="inline-flex items-center justify-center rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-emerald-500"
    >
      Download {sessionId}.zip
    </a>
  );
}
